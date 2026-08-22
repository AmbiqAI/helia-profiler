"""Shared transport utilities for heliaPROFILER data capture.

Defines HPX protocol constants and the byte-stream line-collection loop
shared by RTT and SWO transports.  USB CDC uses pyserial's ``readline()``
and handles line collection internally.

HPX Protocol
------------
The firmware emits a structured text stream delimited by sentinels::

    --- HPX_START ---
    HPX_VERSION=1
    HPX_KEY=value
    ...
    --- HPX_PRESET preset_name ---
    --- HPX_ITER 0 ---
    "Layer","Op","counter1",...,"overflow"
    0,CONV_2D,12345,...,0
    ...
    --- HPX_END ---

Transport implementations collect raw lines from the wire and return them
as ``list[str]`` for the protocol parser.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable

from ..wire import (
    HPX_END_SENTINEL,
    HPX_HEARTBEAT_PREFIX,
    HPX_PROTOCOL_VERSION,
    HPX_START_SENTINEL,
    HeartbeatPhase,
)

log = logging.getLogger("hpx")

#: Matches a run of non-ASCII/control characters at the start of a line --
#: the shape of a UART/ITM peripheral re-enable glitch (see
#: ``collect_lines``), not a real character range strip (``str.lstrip``
#: treats its argument as a character *set*, not a range, so "\\x00-\\x1f"
#: would wrongly include a literal '-' while still missing most control
#: chars -- a regex is used here instead for correctness).
_LEADING_GLITCH_RE = re.compile(r"^[^\x20-\x7e]+")


# ---------------------------------------------------------------------------
# HPX protocol sentinels
# ---------------------------------------------------------------------------
#
# Declared once in ``helia_profiler.wire`` and re-exported here under the names
# every transport already imports, so no transport had to change when the
# registry landed.  ``HPX_PROTOCOL_VERSION`` comes along for the same reason.

HPX_START = HPX_START_SENTINEL
HPX_END = HPX_END_SENTINEL
#: ``HPX_PROTOCOL_VERSION`` is re-exported by the import above: ``capture``
#: and ``capture.parser`` both reach it through this module.


# ---------------------------------------------------------------------------
# Default timeouts (seconds)
# ---------------------------------------------------------------------------

#: Legacy hard overall deadline (kept for back-compat with callers that do
#: not pass ``overall_timeout_s``).  ``None`` = rely entirely on heartbeats.
DEFAULT_TIMEOUT_S = 600

#: Legacy per-line gap timeout — used when heartbeats are disabled.  Large
#: PSRAM models can keep the firmware busy for minutes between lines.
LINE_TIMEOUT_S = 300

#: Default inactivity timeout when heartbeats are enabled.  Any line from
#: the firmware (heartbeat, CSV row, or sentinel) resets the deadline, so
#: 30 s is plenty to catch a true hang without falsely tripping on long
#: inferences.
HEARTBEAT_TIMEOUT_S = 30


# ---------------------------------------------------------------------------
# Clean-window "announce and extend"
# ---------------------------------------------------------------------------
#
# Before the silent clean (power) window the firmware emits, e.g.::
#
#     HPX_HEARTBEAT phase=clean_window_begin iters=200 est_ms=1000
#
# That window is intentionally quiet (no per-line traffic so the power/cycle
# measurement stays pristine), which can outlast the normal inactivity
# heartbeat for a large model.  The host owns the timeout *policy*: on the
# announce it widens its deadline to cover the firmware's estimate plus a
# safety factor, so a long-but-healthy blackout is not mistaken for a hang.

#: Phase marker that precedes the silent clean-inference window.
CLEAN_WINDOW_BEGIN_PHASE = f"phase={HeartbeatPhase.CLEAN_WINDOW_BEGIN}"

#: Multiplier applied to the firmware's est_ms so jitter / a slightly slower
#: run than the warm estimate cannot trip the deadline.
WINDOW_BUDGET_SAFETY = 2.0

#: Flat cushion (seconds) added on top of the scaled estimate.
WINDOW_BUDGET_MARGIN_S = 15.0

#: Ceiling on any single announce's budget.  The estimate is firmware
#: arithmetic on a live cycle counter: one garbage reading (a CYCCNT
#: re-zeroed between the two bracket reads wraps to a ~1.3e9-cycle delta,
#: ~13.6 s/iteration at 96 MHz) times a large iteration count would
#: otherwise convert 30 s hang detection into a multi-hour zombie wait,
#: because both consumers only ever *raise* their deadlines from it.  An
#: hour is far above any sane window and far below what garbage fabricates;
#: a genuinely longer window needs target.heartbeat.overall_timeout_s
#: anyway (#170).
WINDOW_BUDGET_CAP_S = 3600.0


def window_budget_s(line: str) -> float | None:
    """Return the deadline budget (seconds) for a clean-window announce.

    Parses a ``HPX_HEARTBEAT phase=clean_window_begin ... est_ms=<n>`` line
    and returns ``est_ms / 1000 * WINDOW_BUDGET_SAFETY +
    WINDOW_BUDGET_MARGIN_S``, capped at :data:`WINDOW_BUDGET_CAP_S`.

    Returns ``None`` when *line* is not a clean-window announce or carries no
    usable (> 0) estimate. Since #164/#170 every profile build announces a
    real duration statement — a measured warm-inference estimate for infer
    windows (both window modes), the compile-time ``window_target_ms`` for
    busy-loop windows — so a zero here means a dedicated power binary's
    announce (which no host ever reads) or a measurement that degraded at
    runtime (DWT frozen through every warmup by a debugger-attach
    transient); the caller then keeps its normal heartbeat behaviour.
    """
    if CLEAN_WINDOW_BEGIN_PHASE not in line:
        return None
    est_ms: int | None = None
    for tok in line.split():
        if tok.startswith("est_ms="):
            try:
                est_ms = int(tok.split("=", 1)[1])
            except ValueError:
                est_ms = None
            break
    if est_ms is None or est_ms <= 0:
        return None
    budget = est_ms / 1000.0 * WINDOW_BUDGET_SAFETY + WINDOW_BUDGET_MARGIN_S
    return min(budget, WINDOW_BUDGET_CAP_S)


# ---------------------------------------------------------------------------
# Shared line-collection loop (byte-stream transports: RTT, SWO)
# ---------------------------------------------------------------------------


def collect_lines(
    read_fn,
    *,
    transport_name: str,
    overall_timeout_s: float | None = None,
    heartbeat_timeout_s: float = HEARTBEAT_TIMEOUT_S,
    poll_interval_s: float = 0.005,
    on_line: Callable[[str, float], None] | None = None,
) -> list[str]:
    """Collect HPX protocol lines from a byte-stream transport.

    The loop returns on any of:

    * ``HPX_END`` sentinel seen  (success).
    * ``overall_timeout_s`` wall clock elapsed (safety net; ``None`` = off).
    * ``heartbeat_timeout_s`` with no line of any kind received (hang).

    Every received line — data, heartbeat, or metadata — refreshes the
    inactivity deadline, so long-running inferences that emit periodic
    ``HPX_HEARTBEAT`` lines never time out.

    Args:
        read_fn: Zero-argument callable returning ``bytes``.  Must return
            ``b""`` when no data is available.  Exceptions propagate to
            the caller unchanged.
        transport_name: Human-readable name for log messages
            (e.g. ``"RTT"``, ``"SWO"``).
        overall_timeout_s: Absolute ceiling.  ``None`` = unbounded.
        heartbeat_timeout_s: Max gap between *any* received lines.
        poll_interval_s: Sleep between ``read_fn()`` polls when no data
            is available.
    Returns:
        List of non-empty, stripped text lines.
    """
    lines: list[str] = []
    buf = b""
    start = time.monotonic()
    overall_deadline: float | None = (
        start + overall_timeout_s if overall_timeout_s is not None else None
    )
    hb_deadline = start + heartbeat_timeout_s
    # A clean-window announce's budget is a FLOOR on the inactivity deadline
    # until it expires, not a one-shot raise: the per-line reset below used
    # to discard it on the very next received line, which for a busy-loop
    # window is the in-window HPX_CLEAN_WINDOW_PROBE print — the widened
    # deadline evaporated and the silent window was reported as a hang
    # (#170).  Held across iterations and re-applied on every reset; once
    # wall-clock passes it, it stops mattering on its own.
    window_deadline: float | None = None
    seen_start = False

    while True:
        if overall_deadline is not None and time.monotonic() > overall_deadline:
            log.warning(
                "%s capture hit overall timeout %.0fs (%d lines captured)",
                transport_name,
                overall_timeout_s,
                len(lines),
            )
            return lines

        data = read_fn()

        if data:
            buf += data
            hb_deadline = time.monotonic() + heartbeat_timeout_s
            if window_deadline is not None and window_deadline > hb_deadline:
                hb_deadline = window_deadline

            # Extract complete newline-delimited lines from the buffer
            while b"\n" in buf:
                raw_line, buf = buf.split(b"\n", 1)
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                # A UART/ITM peripheral re-enabled mid-run (e.g. released
                # around a gated power window and restored afterward) can
                # glitch a single leading byte on its first transmission --
                # observed as a stray U+FFFD replacement char (or other
                # non-printable) prepended to an otherwise-clean protocol
                # line, e.g. "\ufffdHPX_CLEAN_INFER_COUNT=236".  That silently
                # broke every ``^HPX_...`` / ``^--- HPX_...`` anchored match
                # downstream, dropping the clean-window result and falling
                # back to a whole-capture power estimate (found 2026-07-06).
                # Strip any run of non-ASCII/control characters before the
                # first recognisable HPX marker; a genuinely garbled/empty
                # line degrades no further than before (still fails to
                # match, just for a different reason).
                if line and not line[0].isascii():
                    stripped = _LEADING_GLITCH_RE.sub("", line)
                    if stripped != line:
                        log.debug(
                            "%s: stripped %d leading non-ASCII byte(s) from line "
                            "(peripheral re-enable glitch)",
                            transport_name,
                            len(line) - len(stripped),
                        )
                        line = stripped
                    if not line:
                        continue
                line_ts = time.monotonic()

                lines.append(line)
                if on_line is not None:
                    on_line(line, line_ts)
                if line.startswith(HPX_HEARTBEAT_PREFIX):
                    log.info("%s heartbeat: %s", transport_name, line)
                    budget = window_budget_s(line)
                    if budget is not None:
                        held = line_ts + budget
                        if window_deadline is None or held > window_deadline:
                            window_deadline = held
                        if window_deadline > hb_deadline:
                            hb_deadline = window_deadline
                        if (
                            overall_deadline is not None
                            and window_deadline > overall_deadline
                        ):
                            overall_deadline = window_deadline
                        log.info(
                            "%s: clean window announced (~%.0fs budget) — "
                            "holding deadline through the silent measurement window",
                            transport_name,
                            budget,
                        )
                else:
                    log.debug("%s: %s", transport_name, line)

                if line == HPX_START:
                    seen_start = True
                if line == HPX_END:
                    log.info("Captured %d lines (HPX_END received)", len(lines))
                    return lines
        else:
            if time.monotonic() > hb_deadline:
                if seen_start:
                    log.warning(
                        "%s: no data for %.0fs after HPX_START (%d lines) — "
                        "firmware may be hung or HPX_END was lost",
                        transport_name,
                        heartbeat_timeout_s,
                        len(lines),
                    )
                else:
                    log.warning(
                        "%s: no data for %.0fs — firmware may not be running "
                        "(check reset / transport / heartbeat config)",
                        transport_name,
                        heartbeat_timeout_s,
                    )
                return lines
            time.sleep(poll_interval_s)
