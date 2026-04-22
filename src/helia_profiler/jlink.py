"""SEGGER J-Link helpers.

Centralises all J-Link executable discovery, target reset, and SWO viewer
command construction so that the rest of the codebase never needs to know
about J-Link command-line flags or probe-selection details.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

from .errors import CaptureError

log = logging.getLogger("hpx")


# ------------------------------------------------------------------
# Executable discovery
# ------------------------------------------------------------------

def find_jlink_exe() -> str:
    """Return the absolute path to ``JLinkExe``.

    Raises :class:`CaptureError` if not found.
    """
    exe = shutil.which("JLinkExe")
    if exe:
        return exe
    for candidate in ("/usr/local/bin/JLinkExe",):
        if os.path.isfile(candidate):
            return candidate
    raise CaptureError(
        "JLinkExe not found",
        hint="Install the SEGGER J-Link package and ensure JLinkExe is in PATH.",
    )


def find_swo_viewer() -> str:
    """Return the absolute path to ``JLinkSWOViewerCL``.

    Raises :class:`CaptureError` if not found.
    """
    exe = shutil.which("JLinkSWOViewerCL")
    if exe:
        return exe
    for candidate in ("/usr/local/bin/JLinkSWOViewerCL",):
        if os.path.isfile(candidate):
            return candidate
    raise CaptureError(
        "JLinkSWOViewerCL not found",
        hint="Install the SEGGER J-Link package and ensure JLinkSWOViewerCL is in PATH.",
    )


# ------------------------------------------------------------------
# Target reset
# ------------------------------------------------------------------

def reset_target(
    *,
    device: str,
    jlink_serial: str | None = None,
) -> None:
    """Reset and start the target via JLinkExe.

    Sends ``r`` (reset), ``g`` (go), ``exit`` to JLinkExe.  When
    *jlink_serial* is given, ``-SelectEmuBySN`` is passed so the correct
    probe is selected when multiple J-Links are connected.
    """
    jlink_exe = find_jlink_exe()
    script = "r\ng\nexit\n"

    cmd = [
        jlink_exe,
        "-device", device,
        "-if", "SWD",
        "-speed", "4000",
        "-autoconnect", "1",
    ]
    if jlink_serial:
        cmd.extend(["-SelectEmuBySN", jlink_serial])

    log.info("Resetting target via JLinkExe (serial=%s)", jlink_serial or "auto")
    try:
        result = subprocess.run(
            cmd,
            input=script,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            raise CaptureError(
                f"JLinkExe reset failed (rc={result.returncode})",
                hint=f"stderr: {result.stderr.strip()[:300]}",
            )
        log.info("Reset complete")
    except CaptureError:
        raise
    except subprocess.TimeoutExpired as exc:
        raise CaptureError(
            "JLinkExe reset timed out (15s)",
            hint="Check that the J-Link probe is connected and not in use by another process.",
        ) from exc
    except FileNotFoundError as exc:
        raise CaptureError(
            "JLinkExe not found",
            hint="Install the SEGGER J-Link package and ensure JLinkExe is in PATH.",
        ) from exc


# ------------------------------------------------------------------
# SWO viewer command
# ------------------------------------------------------------------

def swo_viewer_command(
    *,
    device: str,
    cpu_freq: int = 96_000_000,
    swo_freq: int = 1_000_000,
    itm_port: int = 0,
    jlink_serial: str | None = None,
) -> list[str]:
    """Build the ``JLinkSWOViewerCL`` command list.

    Callers can pass the result directly to :func:`subprocess.Popen`.
    """
    swo_exe = find_swo_viewer()
    cmd = [
        swo_exe,
        "-device", device,
        "-cpufreq", str(cpu_freq),
        "-swofreq", str(swo_freq),
        "-itmport", str(itm_port),
    ]
    if jlink_serial:
        cmd.extend(["-SelectEmuBySN", jlink_serial])
    return cmd
