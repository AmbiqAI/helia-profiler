"""Typed host-dependency checks shared by doctor and preflight."""

from __future__ import annotations

from dataclasses import dataclass, field
import importlib.metadata
from importlib.util import find_spec
import os
from pathlib import Path
import re
import shutil
from typing import Any

from ..config import Toolchain, Transport
from ..engines import EngineType
from ..errors import CaptureError, ConfigError
from ..target.probe.jlink import JLINK_COMMANDER, find_jlink_exe


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


def inspect_environment(
    *,
    toolchain: Toolchain = Toolchain.ARM_NONE_EABI_GCC,
    transport: Transport = Transport.RTT,
    engine: EngineType = EngineType.HELIA_RT,
    require_segger_rtt: bool = False,
    segger_rtt_path: Path | None = None,
    include_versions: bool = False,
) -> DoctorResult:
    """Check required and optional host dependencies without printing.

    Set *include_versions* to additionally probe installed tool/package
    versions against the HPX compatibility baseline (see
    :func:`check_versions`). Disabled by default so existing callers keep
    their current (fast, version-free) behavior.
    """
    results = [_inspect_dependency(spec) for spec in _dependency_specs(toolchain, transport, engine)]
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
    versions = check_versions(toolchain=toolchain) if include_versions else ()
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
    if toolchain in (Toolchain.ARM_NONE_EABI_GCC, Toolchain.GCC):
        specs.append(
            _DependencySpec(
                "ARM GCC toolchain",
                "arm-none-eabi-gcc",
                "binary",
                "Install the GNU Arm Embedded toolchain.",
            )
        )
    elif toolchain is Toolchain.ARMCLANG:
        specs.extend(
            (
                _DependencySpec("ARM Compiler", "armclang", "binary", "Install Arm Compiler 6."),
                _DependencySpec("ARM fromelf", "fromelf", "binary", "Install Arm Compiler 6."),
            )
        )
    else:
        specs.append(
            _DependencySpec(
                "Arm Toolchain for Embedded",
                "ATFE_ROOT",
                "atfe",
                "Set ATFE_ROOT to a complete Arm Toolchain for Embedded installation.",
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
        root = os.environ.get("ATFE_ROOT")
        bin_dir = Path(root).expanduser() / "bin" if root else None
        executables = (
            "clang",
            "clang++",
            "llvm-ar",
            "llvm-objcopy",
            "llvm-size",
            "llvm-nm",
        )
        available = bin_dir is not None and all((bin_dir / name).is_file() for name in executables)
        path = str(bin_dir) if available else None
    return DoctorCheck(
        spec.label,
        spec.name,
        available,
        path=path,
        hint=spec.hint,
    )


# ---------------------------------------------------------------------------
# Version checks — informational, safe offline, never raise.
# ---------------------------------------------------------------------------

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
