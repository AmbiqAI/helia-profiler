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

from collections.abc import Iterator
import contextlib
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from contextlib import AbstractContextManager

from ...errors import CaptureError, ConfigError
from ...platform import CoreArch
from .base import DebugMemorySession

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
_READINESS_POLL_INTERVAL_S = 0.1
_SBL_SETTLE_S = 0.2
JLINK_COMMANDER = "JLinkExe"

_JLINK_NOT_FOUND_HINT = (
    "Install the SEGGER J-Link package and ensure JLinkExe is in PATH, "
    "or set JLINK_PATH to the JLinkExe binary."
)
_EMU_LIST_RE = re.compile(
    r"Connection:\s*(?P<connection>[^,]+),\s*Serial number:\s*(?P<serial>\d+),"
    r"\s*ProductName:\s*(?P<product>[^,]+)"
)
_FOUND_CORE_RE = re.compile(r"Found\s+Cortex-M(?P<core>\d+)", re.IGNORECASE)
_JLINK_DLL_ENV_VARS = ("HPX_JLINK_DLL", "JLINK_DLL_PATH", "JLINKARM_DLL")
_JLINK_WRAPPER_LIB_PATH_RE = re.compile(
    r"(?:DYLD_LIBRARY_PATH|LD_LIBRARY_PATH)=['\"](?P<path>[^'\"]+)['\"]"
)


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
    exe = shutil.which(JLINK_COMMANDER)
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


def flash_binary(
    binary_path: Path,
    *,
    device: str,
    jlink_serial: str | None = None,
    speed_khz: int = 4000,
    interface: str = "SWD",
    timeout_s: int = _DEFAULT_TIMEOUT_S,
) -> None:
    """Flash a second NSX target's image via its NSX-generated JLink script.

    This exists for the dedicated power binary (``hpx_profiler_power``): it
    is a *second* executable in the same NSX/CMake project as the transport
    binary and has no ``nsx flash`` entry point of its own (``nsx flash`` /
    :class:`JLinkFlashBackend` always target the project's primary
    executable).

    The NSX build generates a ready-made commander script per target at
    ``<build_dir>/jlink/<target>/flash_cmds.jlink`` containing the exact
    proven flash recipe (``LoadFile <target>.bin, <mram_base>`` -- the
    address-explicit ``.bin`` form the primary flash path uses).  Prefer
    executing that script verbatim.  Hand-rolling ``loadfile`` on the
    extension-less ELF was tried first and SILENTLY programmed nothing on
    Apollo510 (measured window current stayed byte-identical to the previous
    firmware), so this must stay on the NSX-generated recipe.

    Falls back to an explicit ``.bin``-sibling load only if the script is
    missing, and raises if neither is available -- a silent no-op flash is
    the worst possible failure mode for a power measurement (the wrong,
    transport-enabled firmware gets measured while metadata claims
    "dedicated").

    The target free-runs immediately after this returns -- see
    ``capture._flash_power_binary`` for why that is fine (a race-free reset
    happens again later, right before the gated capture window is armed).
    """
    target_name = binary_path.stem if binary_path.suffix else binary_path.name
    script_path = binary_path.parent / "jlink" / target_name / "flash_cmds.jlink"
    if script_path.is_file():
        script = script_path.read_text()
        # The generated script ends with Exit; run it verbatim.
        log.info(
            "Flashing %s via NSX-generated JLink script %s (serial=%s)",
            target_name,
            script_path,
            jlink_serial or "auto",
        )
    else:
        # Fallback: explicit .bin + base address, mirroring the generated
        # script's shape.  A raw ELF loadfile is NOT a safe fallback (see
        # docstring); require the .bin sibling.
        bin_path = binary_path if binary_path.suffix == ".bin" else binary_path.with_suffix(".bin")
        if not bin_path.is_file():
            raise CaptureError(
                f"No flashable image for {target_name}: neither the NSX flash "
                f"script ({script_path}) nor a .bin sibling ({bin_path}) exists.",
                hint="Re-run the build; the NSX build emits both per target.",
            )
        log.warning(
            "NSX flash script missing for %s; falling back to explicit .bin "
            "load of %s",
            target_name,
            bin_path,
        )
        script = f"ExitOnError 1\nReset\nLoadFile {bin_path}, 0x00410000\nReset\nGo\nExit\n"

    proc = run_jlink_script(
        script,
        device=device,
        jlink_serial=jlink_serial,
        speed_khz=speed_khz,
        interface=interface,
        timeout_s=timeout_s,
        op_label="JLinkExe flash",
    )
    # Verify the program step actually ran.  JLinkExe reports a successful
    # flash with an explicit summary line; require it rather than trusting
    # the exit code alone.
    output = (proc.stdout or "") + (proc.stderr or "")
    if "Flash download: Total" not in output and "O.K." not in output:
        raise CaptureError(
            f"JLinkExe flash of {target_name} produced no flash-download "
            "confirmation — the image was likely NOT programmed.",
            hint="Inspect the JLinkExe output; check the probe connection "
            "and that the .bin/base-address recipe matches the board.",
        )
    log.info("Flash complete: %s", target_name)


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


@dataclass(frozen=True)
class JLinkResetController:
    """Reset primitives backed by SEGGER J-Link."""

    def debug_reset(self, *, device: str, jlink_serial: str | None = None) -> None:
        reset_target(device=device, jlink_serial=jlink_serial)

    def swpoi_reset(self, *, device: str, jlink_serial: str | None = None) -> None:
        reset_target_poi(device=device, jlink_serial=jlink_serial)

    def attached_reset_session(
        self,
        *,
        device: str,
        jlink_serial: str | None = None,
        attach_timeout_s: float = 30.0,
        settle_s: float = _SBL_SETTLE_S,
    ) -> AbstractContextManager[DebugMemorySession]:
        return attached_reset_session(
            device=device,
            jlink_serial=jlink_serial,
            attach_timeout_s=attach_timeout_s,
            settle_s=settle_s,
        )


@dataclass(frozen=True)
class JLinkFlashBackend:
    """Flash firmware through the NSX J-Link backend."""

    def flash(
        self,
        firmware_path: Path,
        *,
        toolchain: str,
        jlink_serial: str | None = None,
        frozen: bool = False,
        timeout_s: float,
        verbose: bool = False,
    ) -> None:
        from ... import nsx as nsx_cli

        nsx_cli.flash(
            firmware_path,
            toolchain=toolchain,
            jlink_serial=jlink_serial,
            frozen=frozen,
            timeout_s=timeout_s,
            verbose=verbose,
        )


def create_debug_memory_session() -> DebugMemorySession:
    """Create a pylink-backed debug-memory session."""
    try:
        import pylink
    except ImportError as exc:
        raise CaptureError(
            "pylink-square package not installed (required for debug probe transports)",
            hint="pip install pylink-square",
        ) from exc
    try:
        return pylink.JLink()
    except TypeError as exc:
        msg = str(exc).lower()
        if "dll" not in msg and "dylib" not in msg and "shared library" not in msg:
            raise
    jlink = _create_jlink_with_discovered_dll(pylink)
    if jlink is not None:
        return jlink

    raise CaptureError(
        "pylink could not load the SEGGER J-Link shared library.",
        hint=(
            "Install the SEGGER J-Link package in a standard location, or set "
            "HPX_JLINK_DLL/JLINK_DLL_PATH to libjlinkarm.dylib. If using Nix, "
            "ensure the JLinkExe wrapper is on PATH so hpx can discover its "
            "DYLD_LIBRARY_PATH."
        ),
    )


def _create_jlink_with_discovered_dll(pylink_module):
    """Create ``pylink.JLink`` from a fallback DLL path, or return None.

    Standard SEGGER installs are handled by ``pylink.JLink()`` above. This
    fallback covers non-standard package managers (notably Nix on macOS) where
    ``JLinkExe`` is a wrapper that sets DYLD_LIBRARY_PATH for itself but Python
    cannot see the J-Link SDK library.
    """
    try:
        from pylink import library as pylink_library
    except ImportError:
        return None

    for path in _jlink_dll_candidates():
        try:
            lib = pylink_library.Library(str(path))
            if lib.dll() is None:
                continue
            log.info("Using J-Link shared library for pylink: %s", path)
            return pylink_module.JLink(lib=lib)
        except (OSError, TypeError):
            continue
    return None


def _jlink_dll_candidates() -> list[Path]:
    """Return candidate J-Link SDK shared libraries in precedence order."""
    candidates: list[Path] = []
    for env_name in _JLINK_DLL_ENV_VARS:
        raw = os.environ.get(env_name)
        if raw:
            candidates.append(Path(raw).expanduser())

    candidates.extend(_jlink_dll_candidates_from_wrapper())
    return _dedupe_existing_paths(candidates)


def _jlink_dll_candidates_from_wrapper() -> list[Path]:
    try:
        exe = Path(find_jlink_exe())
    except CaptureError:
        return []

    candidates: list[Path] = []
    candidates.extend(_jlink_dlls_in_dir(exe.parent))

    try:
        text = exe.read_text(errors="ignore")
    except OSError:
        text = ""

    for match in _JLINK_WRAPPER_LIB_PATH_RE.finditer(text):
        for raw_dir in match.group("path").split(":"):
            if raw_dir:
                candidates.extend(_jlink_dlls_in_dir(Path(raw_dir).expanduser()))
    return candidates


def _jlink_dlls_in_dir(path: Path) -> list[Path]:
    names: tuple[str, ...]
    if sys.platform.startswith("darwin"):
        names = ("libjlinkarm.dylib", "libjlinkarm*.dylib")
    elif sys.platform.startswith("win"):
        names = ("JLink_x64.dll", "JLinkARM.dll")
    else:
        names = ("libjlinkarm.so", "libjlinkarm.so.*")

    found: list[Path] = []
    for pattern in names:
        found.extend(path.glob(pattern))
    return found


def _dedupe_existing_paths(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


def _pylink_module():
    try:
        import pylink
    except ImportError as exc:
        raise CaptureError(
            "pylink-square package not installed (required for debug probe transports)",
            hint="pip install pylink-square",
        ) from exc
    return pylink


def is_jlink_exception(exc: BaseException) -> bool:
    """Return True when *exc* is a pylink J-Link exception."""
    try:
        pylink = _pylink_module()
    except CaptureError:
        return False
    return isinstance(exc, pylink.errors.JLinkException)


def is_jlink_rtt_exception(exc: BaseException) -> bool:
    """Return True when *exc* is a pylink RTT exception."""
    try:
        pylink = _pylink_module()
    except CaptureError:
        return False
    return isinstance(exc, pylink.errors.JLinkRTTException)


def resume_if_halted(jlink: DebugMemorySession, *, settle_s: float = 0.1) -> bool:
    """Restart the target if the debug attach left it halted."""
    if not jlink.halted():
        return False
    jlink.restart()
    if settle_s > 0:
        time.sleep(settle_s)
    log.info("Resumed target after debug attach")
    return True


def open_jlink_with_retry(
    jlink: DebugMemorySession,
    *,
    device: str,
    jlink_serial: str | None = None,
    timeout_s: float,
    interval_s: float = _READINESS_POLL_INTERVAL_S,
    interface: object | None = None,
    speed_khz: int = 4000,
) -> None:
    """Open and connect a pylink session, retrying until the target is ready."""
    pylink = _pylink_module()
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
    settle_s: float = _SBL_SETTLE_S,
) -> Iterator[DebugMemorySession]:
    """Reset the target and hold the debugger attached for the whole capture."""
    jlink = create_debug_memory_session()
    open_jlink_with_retry(
        jlink, device=device, jlink_serial=jlink_serial, timeout_s=attach_timeout_s
    )
    try:
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


__all__ = [
    "JLinkFlashBackend",
    "JLINK_COMMANDER",
    "JLinkProbe",
    "JLinkProbeMatch",
    "JLinkResetController",
    "attached_reset_session",
    "create_debug_memory_session",
    "find_jlink_exe",
    "inspect_probe_target",
    "is_jlink_exception",
    "is_jlink_rtt_exception",
    "list_connected_probes",
    "open_jlink_with_retry",
    "resolve_probe_serial",
    "resume_if_halted",
    "reset_target",
    "reset_target_poi",
    "run_jlink_script",
]
