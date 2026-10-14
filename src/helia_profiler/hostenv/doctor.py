"""Typed host-dependency checks shared by doctor and preflight."""

from __future__ import annotations

import importlib.metadata
import os
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from ..config import Toolchain, Transport
from ..engines import EngineType
from ..errors import CaptureError, ConfigError
from ..target.probe.jlink import JLINK_COMMANDER, find_jlink_exe
from .toolchains import get_toolchain_spec, resolve_toolchain_executable

# The tools NSX's ATfE toolchain file requires from ATFE_ROOT/bin only:
# https://github.com/AmbiqAI/neuralspotx/blob/main/src/neuralspotx/cmake/toolchains/atfe.cmake
_ATFE_EXECUTABLES = ("clang", "clang++", "llvm-ar", "llvm-objcopy", "llvm-size", "llvm-nm")


@dataclass(frozen=True)
class DoctorCheck:
    """Availability result for one required or optional host dependency."""

    label: str
    name: str
    available: bool
    path: str | None = None
    required: bool = True
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "name": self.name,
            "available": self.available,
            "path": self.path,
            "required": self.required,
            "hint": self.hint,
        }


@dataclass(frozen=True)
class DoctorVersionCheck:
    """One tool/package/engine version check against the HPX compatibility baseline.

    Purely informational and never raises: ``hpx doctor`` reports version
    drift without failing so it stays usable to diagnose the exact drift.
    ``installed``/``ok`` are ``None`` when a version could not be determined
    (tool missing, ``--version`` unparsable) — unknown, not failed.
    """

    label: str
    name: str
    installed: str | None
    required: str | None
    ok: bool | None
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "name": self.name,
            "installed": self.installed,
            "required": self.required,
            "ok": self.ok,
            "hint": self.hint,
        }


@dataclass(frozen=True)
class DoctorResult:
    """Structured host-readiness result returned by the programmatic API."""

    checks: tuple[DoctorCheck, ...]
    versions: tuple[DoctorVersionCheck, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return all(check.available for check in self.checks if check.required)

    @property
    def missing_required(self) -> tuple[DoctorCheck, ...]:
        """Required dependencies that are unavailable."""
        return tuple(check for check in self.checks if check.required and not check.available)

    @property
    def version_mismatches(self) -> tuple[DoctorVersionCheck, ...]:
        """Version checks that ran and failed their baseline constraint."""
        return tuple(check for check in self.versions if check.ok is False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
            "versions": [check.to_dict() for check in self.versions],
        }


@dataclass(frozen=True)
class _DependencySpec:
    label: str
    name: str
    kind: str
    hint: str


@dataclass(frozen=True)
class _Environment:
    python: tuple[int, int]
    interpreter: str
    driver: str | None
    devices: tuple[str, ...] | None
    launcher: str | None
    expected_launcher: Path
    in_venv: bool


@dataclass(frozen=True)
class _EnvironmentCheck:
    applies: Callable[[_Environment], bool]
    probe: Callable[[_Environment], DoctorVersionCheck]


def _environment(devices: tuple[str, ...] | None = None) -> _Environment:
    entry = "Scripts/hpx.exe" if os.name == "nt" else "bin/hpx"
    return _Environment(
        (sys.version_info.major, sys.version_info.minor),
        sys.executable,
        _package_version("pyjoulescope-driver"),
        devices,
        shutil.which("hpx"),
        Path(sys.prefix) / entry,
        sys.prefix != sys.base_prefix,
    )


def _joulescope_check(env: _Environment) -> DoctorVersionCheck:
    devices = "unknown" if env.devices is None else ", ".join(env.devices) or "none"
    installed = (
        f"Python {env.python[0]}.{env.python[1]}; {env.interpreter}; "
        f"driver {env.driver or 'unknown'}; {devices}"
    )
    ok: bool | None = True
    hint = None
    if env.devices is None or (env.devices and env.driver is None):
        ok = None
        hint = "Device or driver metadata is unknown; JS320 compatibility was not determined."
    elif "js320" in env.devices and env.python < (3, 12):
        ok = False
        hint = (
            "Run JS320 with Python >= 3.12 in the matching HPX environment. "
            "JS110 and JS220 remain supported. See helia-profiler#228."
        )
    return DoctorVersionCheck(
        "Joulescope environment", "js320_environment", installed, "JS320: Python >= 3.12", ok, hint
    )


def _shim_check(env: _Environment) -> DoctorVersionCheck:
    assert env.launcher is not None
    shadowed = Path(env.launcher).parent.name == "shims" and env.expected_launcher.is_file()
    return DoctorVersionCheck(
        "HPX launcher",
        "hpx_launcher",
        env.launcher,
        str(env.expected_launcher),
        not shadowed,
        f"Put {env.expected_launcher.parent} before pyenv shims on PATH." if shadowed else None,
    )


_ENVIRONMENT_CHECKS = (
    _EnvironmentCheck(
        lambda env: env.driver is not None or env.devices is not None, _joulescope_check
    ),
    _EnvironmentCheck(lambda env: env.launcher is not None and env.in_venv, _shim_check),
)


def _check_joulescope_environment(family: str) -> DoctorVersionCheck:
    return _joulescope_check(_environment((family,)))


def inspect_environment(
    *,
    toolchain: Toolchain = Toolchain.ARM_NONE_EABI_GCC,
    transport: Transport = Transport.RTT,
    engine: EngineType = EngineType.HELIA_RT,
    require_segger_rtt: bool = False,
    segger_rtt_path: Path | None = None,
    include_versions: bool = False,
    joulescope_devices: tuple[str, ...] | None = None,
) -> DoctorResult:
    """Check required and optional host dependencies without printing.

    Set *include_versions* to additionally probe installed tool/package
    versions against the HPX compatibility baseline (see
    :func:`check_versions`). Tool version probes are disabled by default.
    Supplied Joulescope families are observed metadata; ``None`` means unobserved.
    """
    results = [
        _inspect_dependency(spec) for spec in _dependency_specs(toolchain, transport, engine)
    ]
    if require_segger_rtt:
        from ..errors import FirmwareError
        from ..firmware import find_segger_rtt_dir

        try:
            resolved_rtt = find_segger_rtt_dir(segger_rtt_path)
        except FirmwareError:
            results.append(
                DoctorCheck(
                    "SEGGER RTT source checkout",
                    "SEGGER_RTT_PATH",
                    False,
                    hint="Reinstall helia-profiler or set target.segger_rtt_path.",
                )
            )
        else:
            results.append(
                DoctorCheck(
                    "SEGGER RTT source checkout",
                    "SEGGER_RTT_PATH",
                    True,
                    path=str(resolved_rtt),
                )
            )
    env = _environment(joulescope_devices)
    versions = tuple(spec.probe(env) for spec in _ENVIRONMENT_CHECKS if spec.applies(env))
    if include_versions:
        versions += check_versions(toolchain=toolchain)
    return DoctorResult(tuple(results), versions)


def _dependency_specs(
    toolchain: Toolchain,
    transport: Transport,
    engine: EngineType,
) -> tuple[_DependencySpec, ...]:
    specs = [
        _DependencySpec("CMake (>= 3.24)", "cmake", "binary", "Install CMake >= 3.24."),
        _DependencySpec("Ninja build system", "ninja", "binary", "Install Ninja."),
        _DependencySpec(
            "SEGGER J-Link commander",
            JLINK_COMMANDER,
            "jlink",
            "Install SEGGER J-Link host software.",
        ),
        _DependencySpec(
            "neuralspotx Python package",
            "neuralspotx",
            "python",
            "Install helia-profiler with its runtime dependencies.",
        ),
    ]
    compiler = get_toolchain_spec(toolchain).compiler
    if toolchain is Toolchain.ARMCLANG:
        specs.extend(
            (
                _DependencySpec("ARM Compiler", compiler, "binary", "Install Arm Compiler 6."),
                _DependencySpec("ARM fromelf", "fromelf", "binary", "Install Arm Compiler 6."),
            )
        )
    elif toolchain is Toolchain.ATFE:
        specs.append(
            _DependencySpec(
                "Arm Toolchain for Embedded",
                "ATFE_ROOT",
                "atfe",
                "Set ATFE_ROOT to a complete Arm Toolchain for Embedded installation.",
            )
        )
    else:
        specs.append(
            _DependencySpec(
                "ARM GCC toolchain",
                compiler,
                "binary",
                "Install the GNU Arm Embedded toolchain.",
            )
        )
    if transport in (Transport.RTT, Transport.SWO):
        specs.append(
            _DependencySpec(
                f"pylink Python package ({transport.value.upper()} transport)",
                "pylink",
                "python",
                "Install pylink-square.",
            )
        )
    if engine is EngineType.HELIA_AOT:
        specs.append(
            _DependencySpec(
                "heliaAOT compiler",
                "helia_aot",
                "python",
                "Install helia-profiler with the 'aot' extra.",
            )
        )
    return tuple(specs)


def _inspect_dependency(spec: _DependencySpec) -> DoctorCheck:
    path: str | None = None
    if spec.kind == "binary":
        path = shutil.which(spec.name)
        available = path is not None
    elif spec.kind == "jlink":
        # Same discovery the probe code uses: JLINK_PATH, both commander
        # names on PATH, then common install locations — a plain
        # which("JLinkExe") misses Windows installs, where the binary is
        # JLink.exe.
        try:
            path = find_jlink_exe()
        except CaptureError:
            path = None
        available = path is not None
    elif spec.kind == "python":
        available = find_spec(spec.name) is not None
    else:
        found = [_which_toolchain_executable(Toolchain.ATFE, name) for name in _ATFE_EXECUTABLES]
        clang = found[0]
        available = bool(os.environ.get("ATFE_ROOT")) and all(found)
        path = os.path.dirname(clang) if available and clang else None
    return DoctorCheck(
        spec.label,
        spec.name,
        available,
        path=path,
        hint=spec.hint,
    )


def _which_toolchain_executable(toolchain: Toolchain, name: str) -> str | None:
    # which() on the split path, not the joined one, so Windows PATHEXT
    # (clang -> clang.exe) applies on every supported Python.
    directory, executable = os.path.split(resolve_toolchain_executable(toolchain, name))
    if not directory:
        return shutil.which(executable)
    found = shutil.which(executable, path=directory)
    # Windows which() also searches the working directory before *path*.
    return found if found is not None and Path(found).parent == Path(directory) else None


_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")
_CMAKE_MIN_VERSION = (3, 24, 0)


def _parse_version(text: str) -> tuple[int, int, int] | None:
    """Extract the first ``X.Y[.Z]`` version from a free-form banner."""
    match = _VERSION_RE.search(text)
    if match is None:
        return None
    major, minor, patch = match.groups()
    return (int(major), int(minor), int(patch or 0))


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def check_versions(
    *,
    toolchain: Toolchain = Toolchain.ARM_NONE_EABI_GCC,
    timeout_s: int = 10,
) -> tuple[DoctorVersionCheck, ...]:
    """Best-effort tool/package/engine version checks (never raises).

    Every probe (subprocess ``--version`` call or package-metadata lookup)
    is isolated so a missing tool, an offline host, or an unparsable banner
    degrades exactly that one check to ``ok=None`` (unknown) instead of
    failing the whole report — this stays safe to call with no network and
    no attached hardware.
    """
    from .._version import __version__ as hpx_version
    from ..deps.compatibility import load_compatibility_baseline
    from .toolchain_probe import cmake_version, compiler_version

    checks: list[DoctorVersionCheck] = [
        DoctorVersionCheck(
            "heliaPROFILER (hpx)", "hpx", installed=hpx_version, required=None, ok=True
        )
    ]

    neuralspotx_installed = _package_version("neuralspotx")
    try:
        baseline = load_compatibility_baseline()
    except ConfigError:
        baseline = None
    if baseline is not None:
        required = baseline.neuralspotx_version
        ok = None if neuralspotx_installed is None else neuralspotx_installed == required
        checks.append(
            DoctorVersionCheck(
                "neuralspotx Python package",
                "neuralspotx",
                installed=neuralspotx_installed,
                required=f"=={required}",
                ok=ok,
                hint=(
                    None
                    if ok in (True, None)
                    else f"Install neuralspotx=={required} to match the HPX compatibility baseline."
                ),
            )
        )

    cmake_banner = cmake_version(timeout_s=timeout_s)
    cmake_installed = _parse_version(cmake_banner) if cmake_banner else None
    checks.append(
        DoctorVersionCheck(
            "CMake",
            "cmake",
            installed=".".join(str(part) for part in cmake_installed) if cmake_installed else None,
            required=">=3.24",
            ok=None if cmake_installed is None else cmake_installed >= _CMAKE_MIN_VERSION,
            hint=(
                None
                if cmake_installed is None or cmake_installed >= _CMAKE_MIN_VERSION
                else "Upgrade CMake to >= 3.24."
            ),
        )
    )

    compiler_banner = compiler_version(toolchain.value, timeout_s=timeout_s)
    checks.append(
        DoctorVersionCheck(
            f"{toolchain.value} compiler",
            toolchain.value,
            installed=compiler_banner or None,
            required=None,
            ok=None if not compiler_banner else True,
        )
    )

    return tuple(checks)
