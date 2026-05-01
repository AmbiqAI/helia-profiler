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
from .transport import DEFAULT_TIMEOUT_S, collect_lines

log = logging.getLogger("hpx")

_SBL_SETTLE_S = 1.0  # post-reset delay for SBL before pylink connects


def capture_swo_output(
    *,
    build_dir=None,  # unused — kept for interface parity
    jlink_serial: str | None = None,
    jlink_device: str = "AP510NFA-CBR",
    timeout_s: float = DEFAULT_TIMEOUT_S,
    cpu_freq: int = 96_000_000,
    swo_freq: int = 1_000_000,
) -> list[str]:
    """Capture firmware output via SWO/ITM until HPX_END or timeout.

    .. warning::

       SWO has no flow control — data can be silently dropped if the
       firmware outputs faster than the SWO pin bandwidth (~1 Mbps).
       Use ``--transport rtt`` for guaranteed lossless delivery.

    Returns:
        List of captured text lines.
    """
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

    # --- Step 2: brief delay for SBL to finish ---
    time.sleep(_SBL_SETTLE_S)

    # --- Step 3: connect pylink and enable SWO ---
    jlink = pylink.JLink()
    jlink.disable_dialog_boxes()

    try:
        if jlink_serial:
            jlink.open(serial_no=int(jlink_serial))
        else:
            jlink.open()
        jlink.set_tif(pylink.JLinkInterfaces.SWD)
        jlink.connect(jlink_device, 4000)
        log.info("pylink connected to %s for SWO capture", jlink_device)

        jlink.swo_enable(cpu_speed=cpu_freq, swo_speed=swo_freq, port_mask=0x01)
        log.info("SWO enabled (cpu=%d Hz, swo=%d Hz)", cpu_freq, swo_freq)

        return collect_lines(
            lambda: bytes(jlink.swo_read_stimulus(0, 4096)),
            transport_name="SWO",
            timeout_s=timeout_s,
            poll_interval_s=0.01,  # 10 ms — SWO has limited bandwidth
        )

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
