"""Centralised timing constants and telemetry for capture transports.

This module is the **single place** for the few unavoidable blind delays in
the capture path — the windows where the target is doing something the host
cannot observe (secure-bootloader bring-up, USB re-enumeration).  Everything
else should use bounded, signal-driven polling (see ``readiness.py``) rather
than a fixed ``time.sleep()``.

Keeping these here means new fixed delays are discouraged and the existing
ones stay discoverable and tunable in one spot instead of accreting as magic
numbers scattered across the readers.

It also owns :class:`CaptureTimingTracker`, the shared HPX_START/HPX_END
observation bookkeeping every transport reports through ``timing_out``.
"""

from __future__ import annotations

import time

#: Post-reset settle window for the Apollo secure bootloader (SBL) before the
#: host attempts its first J-Link attach.  The SBL bring-up is not observable
#: from the host, so a small floor is used; the host then *polls* for attach
#: readiness (see ``readiness.open_jlink_with_retry``) rather than assuming the
#: target is ready after this delay.
SBL_SETTLE_S = 0.2

#: Floor delay after target reset before scanning for a re-enumerated USB CDC
#: device.  The old TinyUSB device takes a moment to drop off the host USB bus;
#: this floor avoids racing the host enumerator.  After the floor we *poll* for
#: the new device with a deadline rather than sleeping the full window.
USB_REENUM_FLOOR_S = 0.5

#: Default cadence for host-side readiness polling loops (J-Link attach,
#: device re-enumeration).  Small enough to feel responsive, large enough to
#: avoid hammering the probe / USB subsystem.
READINESS_POLL_INTERVAL_S = 0.1


class CaptureTimingTracker:
    """Track HPX_START/HPX_END sightings and emit capture-timing telemetry.

    Every capture transport reports the same three ``timing_out`` keys
    (``capture_duration_s``, ``hpx_start_latency_s``, ``protocol_duration_s``)
    derived from when the protocol's start/end sentinels were observed
    relative to capture start.  This class is the single implementation of
    that bookkeeping; transports feed it observed lines and call
    :meth:`finalize` on exit.
    """

    def __init__(self, *, start_marker: str, end_marker: str) -> None:
        self.capture_started_s = time.monotonic()
        self.hpx_start_s: float | None = None
        self.hpx_end_s: float | None = None
        self._start_marker = start_marker
        self._end_marker = end_marker

    def observe_line(self, line: str, line_ts: float) -> None:
        """Record the first start-marker and latest end-marker timestamps."""
        if line == self._start_marker and self.hpx_start_s is None:
            self.hpx_start_s = line_ts
        elif line == self._end_marker:
            self.hpx_end_s = line_ts

    def finalize(self, timing_out: dict[str, float] | None) -> None:
        """Populate ``timing_out`` with the standard capture-timing keys."""
        if timing_out is None:
            return
        timing_out["capture_duration_s"] = time.monotonic() - self.capture_started_s
        if self.hpx_start_s is not None:
            timing_out["hpx_start_latency_s"] = self.hpx_start_s - self.capture_started_s
        if self.hpx_start_s is not None and self.hpx_end_s is not None:
            timing_out["protocol_duration_s"] = self.hpx_end_s - self.hpx_start_s
