"""NSX build-system helpers.

Encapsulates all ``nsx`` CLI subprocess invocations (configure, build, flash)
so that the rest of the codebase never calls ``subprocess`` for build
operations directly.  Each function validates its inputs, applies sensible
timeouts, and translates failures into :class:`BuildError`.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from .errors import BuildError

log = logging.getLogger("hpx")

# Conservative timeouts — cmake configure is fast, builds can be slow,
# flash involves J-Link probe negotiation.
_CONFIGURE_TIMEOUT_S = 120
_BUILD_TIMEOUT_S = 300
_FLASH_TIMEOUT_S = 120


def _require_nsx() -> str:
    """Return the path to the ``nsx`` CLI, or raise :class:`BuildError`."""
    exe = shutil.which("nsx")
    if exe:
        return exe
    raise BuildError(
        "nsx CLI not found",
        hint="Install neuralspotx: pip install neuralspotx  (or ensure 'nsx' is in PATH)",
    )


def _run_nsx(
    args: list[str],
    *,
    timeout: int,
    label: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run an ``nsx`` CLI command with standard error handling.

    Parameters
    ----------
    args : list[str]
        Full argument list (e.g. ``["nsx", "build", "--app-dir", ...]``).
    timeout : int
        Maximum seconds to wait before killing the process.
    label : str
        Human-readable label for error messages (e.g. ``"nsx build"``).
    env : dict | None
        Optional environment override (merged with ``os.environ``).

    Returns
    -------
    subprocess.CompletedProcess
        On success (returncode == 0).

    Raises
    ------
    BuildError
        On any failure (non-zero exit, timeout, missing binary).
    """
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError:
        raise BuildError(
            "nsx CLI not found",
            hint="Install neuralspotx: pip install neuralspotx  (or ensure 'nsx' is in PATH)",
        )
    except subprocess.TimeoutExpired as exc:
        raise BuildError(
            f"{label} timed out after {timeout}s",
            hint="The build may be stuck.  Check for missing dependencies or hardware issues.",
        ) from exc

    if result.returncode != 0:
        raise BuildError(
            f"{label} failed",
            returncode=result.returncode,
            stderr=result.stderr,
        )
    return result


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def configure(app_dir: Path) -> str:
    """Run ``nsx configure`` on the given app directory.

    Returns the stdout output from the configure step.
    """
    _require_nsx()
    log.info("Running: nsx configure --app-dir %s", app_dir)
    result = _run_nsx(
        ["nsx", "configure", "--app-dir", str(app_dir)],
        timeout=_CONFIGURE_TIMEOUT_S,
        label="nsx configure",
    )
    log.info("Configure stdout:\n%s", result.stdout)
    return result.stdout


def build(app_dir: Path) -> str:
    """Run ``nsx build`` on the given app directory.

    Returns the stdout output from the build step.
    """
    _require_nsx()
    log.info("Running: nsx build --app-dir %s", app_dir)
    result = _run_nsx(
        ["nsx", "build", "--app-dir", str(app_dir)],
        timeout=_BUILD_TIMEOUT_S,
        label="nsx build",
    )
    log.info("Build stdout:\n%s", result.stdout)
    return result.stdout


def flash(
    app_dir: Path,
    *,
    jlink_serial: str | None = None,
) -> str:
    """Run ``nsx flash`` on the given app directory.

    When *jlink_serial* is provided, the ``SEGGER_SNCODE`` environment
    variable is set so the underlying cmake flash target selects the
    correct J-Link probe.

    Returns the stdout output from the flash step.
    """
    _require_nsx()
    log.info("Running: nsx flash --app-dir %s", app_dir)

    env = None
    if jlink_serial:
        env = {**os.environ, "SEGGER_SNCODE": jlink_serial}
        log.info("  J-Link serial: %s", jlink_serial)

    result = _run_nsx(
        ["nsx", "flash", "--app-dir", str(app_dir)],
        timeout=_FLASH_TIMEOUT_S,
        label="nsx flash",
        env=env,
    )
    log.info("Flash complete.")
    return result.stdout
