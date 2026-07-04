"""SWO capture transport — reads HPX output via ITM stimulus port 0.

SWO (Serial Wire Output) reads from the target's ITM debug port via the
J-Link probe.  It requires no additional hardware beyond the standard
SWD debug connection.

.. warning::

   SWO has **no flow control**.  The single-word ITM FIFO can silently
   drop data when the firmware outputs faster than the SWO pin bandwidth
   (~1 Mbps).  This may produce corrupted CSV rows or missing lines.
   Prefer RTT (``--transport rtt``) for reliable, lossless capture.

Sequence:
  1. Reset the target via SEGGER commander.
  2. Connect pylink and enable SWO reception.
  3. Collect lines until ``--- HPX_END ---`` or timeout.
  4. Stop SWO and close the connection.
"""

from __future__ import annotations

import logging
import time

from ..errors import CaptureError
from ..target.probe.base import ResetController
from ..target.probe.jlink import (
    JLinkResetController,
    create_debug_memory_session,
    is_jlink_exception,
    open_jlink_with_retry,
    resume_if_halted,
)
from .timing import SBL_SETTLE_S
from .transport import DEFAULT_TIMEOUT_S, collect_lines

log = logging.getLogger("hpx")

#: Max time to keep retrying the host J-Link attach after reset.
_ATTACH_TIMEOUT_S = 30

#: Some Apollo boards occasionally need one extra reset/attach cycle before
#: SWO traffic starts flowing after a fresh flash.  This also recovers the
#: startup race where the firmware emits ``--- HPX_START ---`` during the
#: host's attach/enable window: SWO has no back-pressure, so that line is
#: lost and the capture arrives missing its start sentinel.  A fresh reset
#: re-runs the firmware with the host already draining the ITM FIFO.
_MAX_CAPTURE_ATTEMPTS = 3

#: Poll SWO aggressively enough to keep up with Apollo ITM bursts.
_SWO_POLL_INTERVAL_S = 0.001

#: Protocol start sentinel.  A capture that has lines but lacks this marker
#: lost its head to the SWO startup race and is worth one more attempt.
_HPX_START_SENTINEL = "--- HPX_START ---"


def capture_swo_output(
    *,
    build_dir=None,  # unused — kept for interface parity
    jlink_serial: str | None = None,
    jlink_device: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    cpu_freq: int = 96_000_000,
    swo_freq: int = 1_000_000,
    timing_out: dict[str, float] | None = None,
    reset_controller: ResetController | None = None,
) -> list[str]:
    """Capture firmware output via SWO/ITM until HPX_END or timeout.

    .. warning::

       SWO has no flow control — data can be silently dropped if the
       firmware outputs faster than the SWO pin bandwidth (~1 Mbps).
       Use ``--transport rtt`` for guaranteed lossless delivery.

    Returns:
        List of captured text lines.
    """
    capture_started_s = time.monotonic()
    hpx_start_s: float | None = None
    hpx_end_s: float | None = None

    def on_line(line: str, line_ts: float) -> None:
        nonlocal hpx_start_s, hpx_end_s
        if line == _HPX_START_SENTINEL and hpx_start_s is None:
            hpx_start_s = line_ts
        elif line == "--- HPX_END ---":
            hpx_end_s = line_ts

    def finalize_timing() -> None:
        if timing_out is None:
            return
        timing_out["capture_duration_s"] = time.monotonic() - capture_started_s
        if hpx_start_s is not None:
            timing_out["hpx_start_latency_s"] = hpx_start_s - capture_started_s
        if hpx_start_s is not None and hpx_end_s is not None:
            timing_out["protocol_duration_s"] = hpx_end_s - hpx_start_s

    controller = reset_controller or JLinkResetController()

    for attempt in range(1, _MAX_CAPTURE_ATTEMPTS + 1):
        # --- Step 1: reset the target BEFORE connecting pylink ---
        # SEGGER commander disconnects on exit so the SBL does not detect a debugger.
        controller.debug_reset(device=jlink_device, jlink_serial=jlink_serial)

        # --- Step 2: small SBL settle floor, then retry the host attach ---
        # The SBL bring-up is not observable from the host, so wait a short floor
        # and then poll the attach (open_jlink_with_retry) instead of assuming the
        # target is ready after one fixed sleep.
        time.sleep(SBL_SETTLE_S)

        # --- Step 3: connect pylink and enable SWO ---
        jlink = create_debug_memory_session()

        try:
            open_jlink_with_retry(
                jlink,
                device=jlink_device,
                jlink_serial=jlink_serial,
                timeout_s=_ATTACH_TIMEOUT_S,
            )
            log.info("pylink connected to %s for SWO capture", jlink_device)
            resume_if_halted(jlink)

            jlink.swo_enable(cpu_speed=cpu_freq, swo_speed=swo_freq, port_mask=0x01)
            log.info("SWO enabled (cpu=%d Hz, swo=%d Hz)", cpu_freq, swo_freq)

            lines = collect_lines(
                lambda: bytes(jlink.swo_read_stimulus(0, 4096)),
                transport_name="SWO",
                timeout_s=timeout_s,
                poll_interval_s=_SWO_POLL_INTERVAL_S,
                on_line=on_line,
            )
            # A usable capture has data *and* its start sentinel.  Lines
            # without the sentinel mean the firmware's head was emitted before
            # the host was draining the FIFO (SWO has no back-pressure) — a
            # recoverable startup race, so retry with a fresh reset rather than
            # returning a partial capture that fails downstream validation.
            have_start = any(_HPX_START_SENTINEL in l for l in lines)
            if (lines and have_start) or attempt == _MAX_CAPTURE_ATTEMPTS:
                finalize_timing()
                return lines
            if lines and not have_start:
                log.warning(
                    "SWO attempt %d/%d captured %d lines but missed the "
                    "HPX_START sentinel (startup race); retrying with a fresh "
                    "reset",
                    attempt,
                    _MAX_CAPTURE_ATTEMPTS,
                    len(lines),
                )
            else:
                log.warning(
                    "SWO attempt %d/%d captured no data; retrying with a fresh reset",
                    attempt,
                    _MAX_CAPTURE_ATTEMPTS,
                )

        except CaptureError:
            raise
        except Exception as exc:
            if is_jlink_exception(exc):
                raise CaptureError(
                    f"J-Link SWO error: {exc}",
                    hint="Check J-Link probe connection and that the probe is not in use.",
                ) from exc
            raise CaptureError(
                f"SWO capture failed: {exc}",
                hint="Check that the J-Link probe is connected and not in use.",
            ) from exc
        finally:
            try:
                jlink.swo_stop()
            except Exception:
                pass
            try:
                jlink.close()
            except Exception:
                pass

    finalize_timing()
    return []
