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

from dataclasses import dataclass
import logging
import os
import re
import shutil
import subprocess

from .errors import CaptureError, ConfigError
from .platform import CoreArch

log = logging.getLogger("hpx")

# --- RSTGEN software power-on-initialization (SWPOI) reset ------------------
#
# ``AM_HAL_RESET_CONTROL_SWPOI`` (am_hal_reset.h): "power on initialization,
# which results in a reset of all blocks except for registers in clock gen,
# RTC, stimer."  This is a *more thorough* reset than the debug-level
# ``ResetTarget()`` HPX's plain ``reset_target()`` performs (equivalent to
# AIRCR/SWPOR, which additionally leaves PMU registers untouched).  SWPOI
# also resets PMU/power-management state, which was empirically found
# (2026-07-02, AP510 KWS LP) to change steady-state measured power by
# ~15-20% (~8.2 mW debug-reset-only vs ~6.9 mW after SWPOI) for identical
# firmware — matching neuralSPOT AutoDeploy's own ``make reset`` step,
# which performs this exact register write after every deploy.
#
# Writing this register triggers an immediate chip reset, so the write
# transaction itself is interrupted mid-flight and JLinkExe reports it as a
# failed memory write / non-zero exit code — this is the expected symptom
# of the reset succeeding, not evidence that it did not happen.  neuralSPOT's
# own tooling relies on this (it discards the return code of `make reset`).
# HPX defaults this to Apollo5-family power capture only; other SoCs must opt in
# through an explicit reset strategy while their board behavior is validated.
_RSTGEN_SWPOI_ADDR = 0x40000004
_RSTGEN_SWPOI_VALUE = 0x1B

# Default wall-clock budget for any single JLinkExe invocation (seconds).
# Reset/erase scripts complete in well under 5s on healthy hardware; 15s
# leaves room for slow USB enumeration on macOS.
_DEFAULT_TIMEOUT_S = 15

_JLINK_NOT_FOUND_HINT = (
    "Install the SEGGER J-Link package and ensure JLinkExe is in PATH, "
    "or set JLINK_PATH to the JLinkExe binary."
)
_EMU_LIST_RE = re.compile(
    r"Connection:\s*(?P<connection>[^,]+),\s*Serial number:\s*(?P<serial>\d+),"
    r"\s*ProductName:\s*(?P<product>[^,]+)"
)
_FOUND_CORE_RE = re.compile(r"Found\s+Cortex-M(?P<core>\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class JLinkProbe:
    serial: str
    product: str = ""
    connection: str = "USB"


@dataclass(frozen=True)
class JLinkProbeMatch:
    probe: JLinkProbe
    detected_core: CoreArch | None


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


def list_connected_probes() -> list[JLinkProbe]:
    """Return the connected J-Link probes visible to ``JLinkExe``."""
    jlink_exe = find_jlink_exe()
    try:
        result = subprocess.run(
            [jlink_exe],
            input="ShowEmuList\nexit\n",
            capture_output=True,
            text=True,
            timeout=_DEFAULT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise ConfigError(
            "Timed out while enumerating J-Link probes.",
            hint="Check that the J-Link probes are connected and not in use by another process.",
        ) from exc
    except FileNotFoundError as exc:
        raise ConfigError("JLinkExe not found", hint=_JLINK_NOT_FOUND_HINT) from exc

    probes: list[JLinkProbe] = []
    seen: set[str] = set()
    for match in _EMU_LIST_RE.finditer((result.stdout or "") + "\n" + (result.stderr or "")):
        serial = match.group("serial")
        if serial in seen:
            continue
        seen.add(serial)
        probes.append(
            JLinkProbe(
                serial=serial,
                product=match.group("product").strip(),
                connection=match.group("connection").strip(),
            )
        )
    return probes


def resolve_probe_serial(
    *,
    device: str,
    expected_core: CoreArch,
    requested_serial: str | None = None,
) -> str:
    """Resolve the J-Link serial to use for this run.

    Selection policy:
    - explicit serial: must exist and match the requested target core
    - implicit serial with one attached probe: auto-select only if it matches
    - implicit serial with multiple probes: auto-select only when exactly one
      attached probe matches the requested target core; otherwise fail with a
      disambiguation message listing the available probes
    """
    probes = list_connected_probes()
    if not probes:
        raise ConfigError(
            "No J-Link probes detected.",
            hint="Connect the target board via J-Link or pass --jlink-serial once the probe is attached.",
        )

    if requested_serial is not None:
        requested_serial = str(requested_serial)
        probe = next((probe for probe in probes if probe.serial == requested_serial), None)
        if probe is None:
            raise ConfigError(
                f"J-Link serial '{requested_serial}' was not found.",
                hint=f"Connected probes: {_format_probe_list(probes)}.",
            )
        match = _inspect_probe_target(probe, device=device)
        if match.detected_core is not expected_core:
            raise ConfigError(
                f"J-Link serial '{requested_serial}' does not match the requested target.",
                hint=(
                    f"Expected {expected_core.value}, but probe '{probe.serial}' reached "
                    f"{_format_core(match.detected_core)}. Connected probes: {_format_probe_list(probes)}."
                ),
            )
        return probe.serial

    matches = [
        match
        for match in (_inspect_probe_target(probe, device=device) for probe in probes)
        if match.detected_core is expected_core
    ]
    if len(matches) == 1:
        return matches[0].probe.serial
    if len(matches) > 1:
        raise ConfigError(
            f"{len(matches)} J-Link probes match the requested target.",
            hint=(
                "Multiple attached probes report the same core, so it can't be "
                "auto-selected. Disambiguate with: "
                "`hpx profile --jlink-serial <serial>` (direct profile runs) or "
                "`hpx validate --jlink-serials <board>=<serial>` (validation suite). "
                "Run `hpx probes match --board <board>` to find the right serial "
                f"for a specific board. Matching probes: {_format_probe_matches(matches)}."
            ),
        )

    raise ConfigError(
        "Could not find a connected J-Link probe for the requested target.",
        hint=(
            f"Expected a {expected_core.value} target. Run `hpx probes list` to see "
            "attached probes and `hpx probes match --board <board>` to check "
            "compatibility. Connected probes: "
            f"{_format_probe_matches([_inspect_probe_target(probe, device=device) for probe in probes])}."
        ),
    )


def _inspect_probe_target(probe: JLinkProbe, *, device: str) -> JLinkProbeMatch:
    jlink_exe = find_jlink_exe()
    cmd = [
        jlink_exe,
        "-device",
        device,
        "-if",
        "SWD",
        "-speed",
        "4000",
        "-autoconnect",
        "1",
        "-SelectEmuBySN",
        probe.serial,
    ]
    try:
        result = subprocess.run(
            cmd,
            input="exit\n",
            capture_output=True,
            text=True,
            timeout=_DEFAULT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise ConfigError(
            f"Timed out while querying J-Link serial '{probe.serial}'.",
            hint="Check that the probe is connected and not in use by another process.",
        ) from exc

    detected_core = _parse_detected_core((result.stdout or "") + "\n" + (result.stderr or ""))
    return JLinkProbeMatch(probe=probe, detected_core=detected_core)


def inspect_probe_target(probe: JLinkProbe, *, device: str) -> JLinkProbeMatch:
    """Inspect the core visible behind a connected probe for *device*.

    This public wrapper exists for diagnostic CLI commands.  It keeps the raw
    ``JLinkExe`` interaction centralized in this module while letting users and
    agents ask HPX which target a probe can actually reach.
    """
    return _inspect_probe_target(probe, device=device)


def _parse_detected_core(output: str) -> CoreArch | None:
    match = _FOUND_CORE_RE.search(output)
    if match is None:
        return None
    core = match.group("core")
    if core == "4":
        return CoreArch.CORTEX_M4
    if core == "55":
        return CoreArch.CORTEX_M55
    return None


def _format_core(core: CoreArch | None) -> str:
    return core.value if core is not None else "unknown target"


def _format_probe_list(probes: list[JLinkProbe]) -> str:
    return ", ".join(f"{probe.serial} ({probe.product or probe.connection})" for probe in probes)


def _format_probe_matches(matches: list[JLinkProbeMatch]) -> str:
    return ", ".join(
        f"{match.probe.serial} ({match.probe.product or match.probe.connection}, {_format_core(match.detected_core)})"
        for match in matches
    )


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
        "-device",
        device,
        "-if",
        interface,
        "-speed",
        str(speed_khz),
        "-autoconnect",
        "1",
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


def reset_target_poi(
    *,
    device: str,
    jlink_serial: str | None = None,
    speed_khz: int = 4000,
    interface: str = "SWD",
    timeout_s: int = _DEFAULT_TIMEOUT_S,
) -> None:
    """Trigger an SWPOI (software power-on-initialization) reset.

    This is a *deeper* reset than :func:`reset_target`: it additionally
    resets PMU/power-management registers left untouched by a debug-level
    reset, which measurably lowers steady-state power for some firmware
    (see the module-level comment above ``_RSTGEN_SWPOI_ADDR``).  It also
    reboots the CPU, so the firmware relaunches exactly as it does after
    :func:`reset_target` — this can replace that call, not just follow it.

    HPX's automatic lifecycle policy uses this only on Apollo5-family targets
    today. Other SoCs may use it through an explicit experimental reset
    strategy, but should not become defaults until their board-level reset and
    GPIO lockstep behavior is validated.

    The register write intentionally triggers an immediate self-reset, so
    JLinkExe's own memory-write verification fails and it exits non-zero —
    this is expected and is *not* treated as an error here (matching
    neuralSPOT's own ``make reset``, which discards this exit code).
    """
    log.info("Triggering SWPOI reset via JLinkExe (serial=%s)", jlink_serial or "auto")
    jlink_exe = find_jlink_exe()
    cmd = [
        jlink_exe,
        "-device",
        device,
        "-if",
        interface,
        "-speed",
        str(speed_khz),
        "-autoconnect",
        "1",
    ]
    if jlink_serial:
        cmd.extend(["-SelectEmuBySN", jlink_serial])

    script = (
        "connect\n"
        "sleep 1000\n"
        f"w4 {_RSTGEN_SWPOI_ADDR:x} {_RSTGEN_SWPOI_VALUE:x}\n"
        "sleep 1000\n"
        "exit\n"
    )
    try:
        subprocess.run(
            cmd,
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise CaptureError(
            f"JLinkExe SWPOI reset timed out ({timeout_s}s)",
            hint="Check that the J-Link probe is connected and not in use by another process.",
        ) from exc
    except FileNotFoundError as exc:
        raise CaptureError("JLinkExe not found", hint=_JLINK_NOT_FOUND_HINT) from exc
    # Non-zero return code is expected (the write self-interrupts the debug
    # session) and is deliberately not checked here.
    log.info("SWPOI reset complete")


__all__ = [
    "JLinkProbe",
    "JLinkProbeMatch",
    "find_jlink_exe",
    "inspect_probe_target",
    "list_connected_probes",
    "resolve_probe_serial",
    "reset_target",
    "reset_target_poi",
    "run_jlink_script",
]
