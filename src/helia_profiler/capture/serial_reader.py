"""SWO-based firmware output capture.

Reads text output from the target board via SEGGER JLinkSWOViewerCL (ITM
port 0).

Sequence:
  1. Reset the board via JLinkExe (no SWO viewer connected yet, so the
     Secure Bootloader does NOT halt for the debugger).
  2. Immediately start JLinkSWOViewerCL.  The firmware has a built-in
     startup delay (see ``main.cc.j2``) to allow the viewer time to attach.
  3. Collect lines until ``--- HPX_END ---`` or a timeout expires.
  4. Kill the viewer process.
"""

from __future__ import annotations

import logging
import signal
import subprocess
import time
from pathlib import Path

from ..errors import CaptureError
from ..jlink import reset_target, swo_viewer_command

log = logging.getLogger("hpx")

DEFAULT_TIMEOUT_S = 120  # generous default for long profiling runs


def capture_swo_output(
    *,
    build_dir: Path | None = None,
    app_name: str = "hpx_profiler",
    timeout_s: float = DEFAULT_TIMEOUT_S,
    jlink_serial: str | None = None,
    jlink_device: str = "AP510NFA-CBR",
) -> list[str]:
    """Read firmware output via SWO until HPX_END or timeout.

    The firmware includes a startup delay (``am_util_delay_ms``) before
    printing so the host has time to start the SWO viewer after the reset.

    Returns the list of captured lines (including SWO viewer boilerplate
    which the parser will skip).
    """
    if build_dir is None:
        raise CaptureError(
            "build_dir required for SWO capture",
            hint="Pass the build directory from the pipeline context.",
        )

    # --- Step 1: reset the target BEFORE starting the SWO viewer ---
    # This avoids the JLink probe conflict and prevents the SBL from
    # detecting a debugger and halting.
    reset_target(device=jlink_device, jlink_serial=jlink_serial)

    # --- Step 2: start the SWO viewer ---
    swo_cmd = swo_viewer_command(device=jlink_device, jlink_serial=jlink_serial)
    log.info("Starting SWO viewer: %s", " ".join(swo_cmd))

    lines: list[str] = []
    viewer: subprocess.Popen[bytes] | None = None

    try:
        viewer = subprocess.Popen(
            swo_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(build_dir),
        )

        # Brief wait for the viewer to connect to the probe
        time.sleep(0.5)

        if viewer.poll() is not None:
            stderr = viewer.stderr.read().decode("utf-8", errors="replace") if viewer.stderr else ""
            raise CaptureError(
                f"SWO viewer exited immediately (rc={viewer.returncode})",
                hint=f"Check JLink probe connection. stderr: {stderr[:200]}",
            )

        # --- Step 3: collect lines ---
        deadline = time.monotonic() + timeout_s
        assert viewer.stdout is not None

        while time.monotonic() < deadline:
            raw = viewer.stdout.readline()
            if not raw:
                if viewer.poll() is not None:
                    log.warning("SWO viewer exited (rc=%s)", viewer.returncode)
                    break
                continue

            try:
                line = raw.decode("utf-8", errors="replace").strip()
            except Exception:
                continue
            if not line:
                continue

            lines.append(line)
            log.debug("SWO: %s", line)

            if line == "--- HPX_END ---":
                log.info("Captured %d lines (HPX_END received)", len(lines))
                return lines

    except CaptureError:
        raise
    except Exception as exc:
        raise CaptureError(
            f"SWO capture error: {exc}",
            hint="Check that the JLink probe is connected and not in use.",
        ) from exc
    finally:
        if viewer is not None and viewer.poll() is None:
            viewer.send_signal(signal.SIGINT)
            try:
                viewer.wait(timeout=3)
            except subprocess.TimeoutExpired:
                viewer.kill()
                viewer.wait(timeout=2)

    log.warning(
        "SWO capture timed out after %.0fs (%d lines captured)",
        timeout_s,
        len(lines),
    )
    return lines


# Keep the old name as an alias for backwards compatibility
capture_serial_output = capture_swo_output
