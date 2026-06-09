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
import time

log = logging.getLogger("hpx")


# ---------------------------------------------------------------------------
# HPX protocol sentinels — must match firmware templates exactly
# ---------------------------------------------------------------------------

HPX_START = "--- HPX_START ---"
HPX_END = "--- HPX_END ---"

#: Current protocol version emitted by firmware and expected by the parser.
HPX_PROTOCOL_VERSION = 1


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
# Shared line-collection loop (byte-stream transports: RTT, SWO)
# ---------------------------------------------------------------------------


def collect_lines(
    read_fn,
    *,
    transport_name: str,
    overall_timeout_s: float | None = None,
    heartbeat_timeout_s: float = HEARTBEAT_TIMEOUT_S,
    poll_interval_s: float = 0.005,
    # Legacy kwargs — accepted for back-compat; if provided they override
    # the new parameters.  Will be removed in a future release.
    timeout_s: float | None = None,
    line_timeout_s: float | None = None,
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
        timeout_s: Deprecated.  Maps to ``overall_timeout_s`` if provided.
        line_timeout_s: Deprecated.  Maps to ``heartbeat_timeout_s``.

    Returns:
        List of non-empty, stripped text lines.
    """
    if timeout_s is not None:
        overall_timeout_s = timeout_s
    if line_timeout_s is not None:
        heartbeat_timeout_s = line_timeout_s

    lines: list[str] = []
    buf = b""
    start = time.monotonic()
    overall_deadline: float | None = (
        start + overall_timeout_s if overall_timeout_s is not None else None
    )
    hb_deadline = start + heartbeat_timeout_s
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

            # Extract complete newline-delimited lines from the buffer
            while b"\n" in buf:
                raw_line, buf = buf.split(b"\n", 1)
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                lines.append(line)
                if line.startswith("HPX_HEARTBEAT"):
                    log.info("%s heartbeat: %s", transport_name, line)
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
