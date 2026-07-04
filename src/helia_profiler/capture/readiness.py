"""Shared host-side readiness helpers for capture transports.

The capture transports (RTT, SWO, USB) all face the same problem: after the
target is reset it is *not* immediately ready, and the host has no single
"firmware booted" signal to wait on.  Historically each reader solved this
with its own fixed ``time.sleep()``, which is brittle — too short and the
attach fails, too long and every run pays the cost.

This module provides the bounded, signal-driven primitives those readers
share so the behaviour is consistent and the few genuinely-blind delays stay
isolated in ``timing.py``.

The contract is always the same: **poll a cheap predicate until it succeeds or
a deadline elapses**, never sleep blindly hoping the target caught up.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

from .timing import READINESS_POLL_INTERVAL_S, SBL_SETTLE_S

log = logging.getLogger("hpx")

if TYPE_CHECKING:
    import pylink


def poll_until(
    predicate: Callable[[], bool],
    *,
    timeout_s: float,
    interval_s: float = READINESS_POLL_INTERVAL_S,
    description: str = "condition",
) -> bool:
    """Poll *predicate* until it returns True or *timeout_s* elapses.

    This is the single bounded-wait primitive for the capture path.  Prefer it
    over ``time.sleep()`` whenever there is an observable signal to wait for.

    Args:
        predicate: Zero-argument callable returning a truthy value when the
            awaited condition is satisfied.  Exceptions propagate to the
            caller unchanged — use ``open_jlink_with_retry`` for the case
            where the probe call itself may raise while not-yet-ready.
        timeout_s: Maximum wall-clock time to wait, in seconds.
        interval_s: Sleep between polls when the predicate is not yet true.
        description: Human-readable name for log messages.

    Returns:
        ``True`` if the predicate succeeded within the deadline, else
        ``False``.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            log.debug("poll_until(%s) timed out after %.1fs", description, timeout_s)
            return False
        time.sleep(interval_s)


def resume_if_halted(jlink: "pylink.JLink", *, settle_s: float = 0.1) -> bool:
    """Restart the target if the debugger left it halted after attach.

    pylink's connect can leave the core halted on some Apollo setups; the
    firmware never runs until it is restarted.  Shared by the RTT and SWO
    readers so the behaviour stays identical.

    Returns:
        ``True`` if the target was halted and has been restarted, else
        ``False``.
    """
    if not jlink.halted():
        return False
    jlink.restart()
    if settle_s > 0:
        time.sleep(settle_s)
    log.info("Resumed target after pylink attach")
    return True


def open_jlink_with_retry(
    jlink: "pylink.JLink",
    *,
    device: str,
    jlink_serial: str | None = None,
    timeout_s: float,
    interval_s: float = READINESS_POLL_INTERVAL_S,
    interface: "pylink.JLinkInterfaces | None" = None,
    speed_khz: int = 4000,
) -> None:
    """Open and connect a pylink session, retrying until the target is ready.

    Immediately after reset the target may still be transitioning through the
    secure bootloader, so the first ``open()``/``connect()`` can raise.  Rather
    than burning a fixed settle delay and attaching once, retry the attach on a
    cadence until the deadline.  This is the shared replacement for the old
    per-reader "sleep then attach once" pattern.

    Args:
        jlink: The ``pylink.JLink`` instance to open/connect.
        device: J-Link device string (e.g. ``"AP510NFA-CBR"``).
        jlink_serial: Optional probe serial number; ``None`` auto-selects.
        timeout_s: Maximum wall-clock time to keep retrying.
        interval_s: Sleep between attach attempts.
        interface: SWD/JTAG interface enum.  Defaults to SWD when ``None``.
        speed_khz: Target interface speed in kHz.

    Raises:
        CaptureError: If no successful attach occurs before the deadline.
    """
    import pylink

    from ..errors import CaptureError

    if interface is None:
        interface = pylink.JLinkInterfaces.SWD

    deadline = time.monotonic() + timeout_s
    attempt = 0
    last_exc: Exception | None = None

    while True:
        attempt += 1
        try:
            if jlink_serial:
                jlink.open(serial_no=int(jlink_serial))
            else:
                jlink.open()
            jlink.disable_dialog_boxes()
            jlink.set_tif(interface)
            jlink.connect(device, speed_khz)
            log.info("pylink connected to %s (attempt %d)", device, attempt)
            return
        except pylink.errors.JLinkException as exc:
            last_exc = exc
            try:
                jlink.close()
            except Exception:  # noqa: BLE001 — close errors are non-fatal
                pass
            if time.monotonic() >= deadline:
                raise CaptureError(
                    f"Timed out attaching J-Link session to {device} after {timeout_s:.0f}s",
                    hint="Check target power and that the probe is not in use.",
                ) from last_exc
            time.sleep(interval_s)


@contextlib.contextmanager
def attached_reset_session(
    *,
    device: str,
    jlink_serial: str | None = None,
    attach_timeout_s: float = 30.0,
    settle_s: float = SBL_SETTLE_S,
) -> Iterator["pylink.JLink"]:
    """Reset the target and hold a pylink debugger ATTACHED for the whole capture.

    Why this exists
    ---------------
    On the Cortex-M4F families (Apollo3/3P and Apollo4/4P) the ``DWT->CYCCNT``
    cycle counter the profiler reads for per-layer CPU cycles lives in the core
    **debug power domain**.  That
    domain is powered only while a debugger asserts the Debug Access Port's
    ``CDBGPWRUPREQ`` signal — which is *not* memory-mapped and therefore cannot
    be set by firmware running on the core (verified: enabling the Ambiq debug
    peripheral domain, the CM4 debug clock, and the DCU trace/perf gate from
    firmware all fail to keep it alive).  When the plain UART/USB readers reset
    with ``reset_target`` (JLinkExe, which releases the probe on exit), that
    domain powers down and every per-layer cycle reads back as 0.

    The SWO and RTT readers never hit this because they re-attach via pylink and
    keep the session open for the whole capture.  This context manager gives the
    UART/USB readers the same property: it attaches via pylink, performs the
    reset+go itself, and holds the session open until the caller is done
    reading, so the debug domain stays powered for the entire timed inference.
    (Empirically verified on silicon: detaching mid-inference freezes the
    counter on the very next layer.)

    Scope this to the SoCs that actually need it (Apollo3/3P and Apollo4/4P —
    see ``SocDef.requires_attached_probe_for_cycles``).  AP5 does not gate the
    debug domain this way (it uses the Armv8-M PMU), and its secure bootloader
    prefers the probe released, so it keeps using ``reset_target``.
    """
    import pylink

    jlink = pylink.JLink()
    open_jlink_with_retry(
        jlink, device=device, jlink_serial=jlink_serial, timeout_s=attach_timeout_s
    )
    try:
        # Reset+go through the attached session so the debug power domain is held
        # up (CDBGPWRUPREQ asserted) for the entire capture window.
        jlink.reset(halt=True)
        jlink.restart()
        if settle_s > 0:
            time.sleep(settle_s)
        log.info("Holding J-Link attached during capture (debug domain kept powered)")
        yield jlink
    finally:
        try:
            jlink.close()
        except Exception:  # noqa: BLE001 — close errors are non-fatal
            pass
