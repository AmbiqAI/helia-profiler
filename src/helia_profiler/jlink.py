"""SEGGER J-Link helpers — the *only* place we shell out to ``JLinkExe``.

The rest of the codebase reaches the J-Link probe in two ways:

* For runtime data (SWO/RTT register reads, memory peeks) we use the
  in-process :mod:`pylink` library.
* For commander-script operations (target reset, erase, custom scripts)
  we shell out to ``JLinkExe`` here.  Apollo510's secure bootloader
  requires the debugger to release the probe between reset and the
  application launch, which the ``JLinkExe`` exit naturally provides
  but ``pylink`` does not — so this wrapper stays.

If you need a new J-Link command-line operation, add a thin wrapper
that calls :func:`run_jlink_script` rather than inlining ``subprocess``
elsewhere.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

from .errors import CaptureError

log = logging.getLogger("hpx")

# Default wall-clock budget for any single JLinkExe invocation (seconds).
# Reset/erase scripts complete in well under 5s on healthy hardware; 15s
# leaves room for slow USB enumeration on macOS.
_DEFAULT_TIMEOUT_S = 15

_JLINK_NOT_FOUND_HINT = (
    "Install the SEGGER J-Link package and ensure JLinkExe is in PATH, "
    "or set JLINK_PATH to the JLinkExe binary."
)


# ------------------------------------------------------------------
# Executable discovery
# ------------------------------------------------------------------

def find_jlink_exe() -> str:
    """Return the absolute path to ``JLinkExe`` or raise :class:`CaptureError`.

    Search order:
      1. ``JLINK_PATH`` environment variable (explicit user override)
      2. ``JLinkExe`` on ``PATH``
      3. Common install locations (``/usr/local/bin/JLinkExe``)
    """
    # 1. Explicit env var
    env_path = os.environ.get("JLINK_PATH")
    if env_path:
        if os.path.isfile(env_path):
            return env_path
        raise CaptureError(
            f"JLINK_PATH={env_path} does not exist or is not a file",
            hint="Set JLINK_PATH to the full path of JLinkExe.",
        )
    # 2. PATH lookup
    exe = shutil.which("JLinkExe")
    if exe:
        return exe
    # 3. Common install locations
    for candidate in ("/usr/local/bin/JLinkExe",):
        if os.path.isfile(candidate):
            return candidate
    raise CaptureError("JLinkExe not found", hint=_JLINK_NOT_FOUND_HINT)


# ------------------------------------------------------------------
# Generic JLinkExe driver
# ------------------------------------------------------------------

def run_jlink_script(
    script: str,
    *,
    device: str,
    jlink_serial: str | None = None,
    speed_khz: int = 4000,
    interface: str = "SWD",
    timeout_s: int = _DEFAULT_TIMEOUT_S,
    op_label: str = "JLinkExe",
) -> subprocess.CompletedProcess[str]:
    """Run a JLinkExe commander script and return the completed process.

    Parameters
    ----------
    script:
        Newline-terminated commander script.  Must include ``exit`` so
        ``JLinkExe`` returns control to us.
    device, jlink_serial, speed_khz, interface:
        Probe / target configuration.  When *jlink_serial* is given the
        ``-SelectEmuBySN`` flag is added so the correct probe is selected
        when multiple J-Links are connected.
    timeout_s:
        Wall-clock timeout passed to :func:`subprocess.run`.
    op_label:
        Short label used in the timeout / error messages
        (e.g. ``"reset"`` -> ``"JLinkExe reset"``).

    Raises
    ------
    CaptureError
        On non-zero rc, ``FileNotFoundError`` (JLinkExe missing), or
        timeout.  Other unexpected exceptions propagate.
    """
    jlink_exe = find_jlink_exe()
    cmd = [
        jlink_exe,
        "-device", device,
        "-if", interface,
        "-speed", str(speed_khz),
        "-autoconnect", "1",
    ]
    if jlink_serial:
        cmd.extend(["-SelectEmuBySN", jlink_serial])

    try:
        result = subprocess.run(
            cmd,
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise CaptureError(
            f"{op_label} timed out ({timeout_s}s)",
            hint="Check that the J-Link probe is connected and not in use by another process.",
        ) from exc
    except FileNotFoundError as exc:
        raise CaptureError("JLinkExe not found", hint=_JLINK_NOT_FOUND_HINT) from exc

    if result.returncode != 0:
        raise CaptureError(
            f"{op_label} failed (rc={result.returncode})",
            hint=f"stderr: {(result.stderr or '').strip()[:300]}",
        )
    return result


# ------------------------------------------------------------------
# Target reset
# ------------------------------------------------------------------

def reset_target(
    *,
    device: str,
    jlink_serial: str | None = None,
) -> None:
    """Reset and start the target via ``JLinkExe``.

    Sends the commander script ``r`` (reset), ``g`` (go), ``exit``.
    ``JLinkExe`` releases the probe on exit, which is required so the
    Apollo510 secure bootloader does not detect an attached debugger
    on the boot following the reset.
    """
    log.info("Resetting target via JLinkExe (serial=%s)", jlink_serial or "auto")
    run_jlink_script(
        "r\ng\nexit\n",
        device=device,
        jlink_serial=jlink_serial,
        op_label="JLinkExe reset",
    )
    log.info("Reset complete")


__all__ = ["find_jlink_exe", "reset_target", "run_jlink_script"]
