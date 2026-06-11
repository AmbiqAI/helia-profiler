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
  1. Reset the target via JLinkExe.
  2. Connect pylink and enable SWO reception.
  3. Collect lines until ``--- HPX_END ---`` or timeout.
  4. Stop SWO and close the connection.
"""

from __future__ import annotations

import logging
import time

from ..errors import CaptureError
from ..jlink import reset_target
from .readiness import open_jlink_with_retry, resume_if_halted
from .timing import SBL_SETTLE_S
from .transport import DEFAULT_TIMEOUT_S, collect_lines

log = logging.getLogger("hpx")

#: Max time to keep retrying the host J-Link attach after reset.
_ATTACH_TIMEOUT_S = 30


def capture_swo_output(
    *,
    build_dir=None,  # unused — kept for interface parity
    jlink_serial: str | None = None,
    jlink_device: str = "AP510NFA-CBR",
    timeout_s: float = DEFAULT_TIMEOUT_S,
    cpu_freq: int = 96_000_000,
    swo_freq: int = 1_000_000,
    timing_out: dict[str, float] | None = None,
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
        if line == "--- HPX_START ---" and hpx_start_s is None:
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

    try:
        import pylink
    except ImportError as exc:
        raise CaptureError(
            "pylink-square package not installed (required for SWO transport)",
            hint="pip install pylink-square",
        ) from exc

    # --- Step 1: reset the target BEFORE connecting pylink ---
    # JLinkExe disconnects on exit so the SBL does not detect a debugger.
    reset_target(device=jlink_device, jlink_serial=jlink_serial)

    # --- Step 2: small SBL settle floor, then retry the host attach ---
    # The SBL bring-up is not observable from the host, so wait a short floor
    # and then poll the attach (open_jlink_with_retry) instead of assuming the
    # target is ready after one fixed sleep.
    time.sleep(SBL_SETTLE_S)

    # --- Step 3: connect pylink and enable SWO ---
    jlink = pylink.JLink()

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
            poll_interval_s=0.01,  # 10 ms — SWO has limited bandwidth
            on_line=on_line,
        )
        finalize_timing()
        return lines

    except CaptureError:
        raise
    except pylink.errors.JLinkException as exc:
        raise CaptureError(
            f"J-Link SWO error: {exc}",
            hint="Check J-Link probe connection and that the probe is not in use.",
        ) from exc
    except Exception as exc:
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
