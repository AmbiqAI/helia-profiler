"""Typed host-dependency checks shared by doctor and preflight."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
import os
from pathlib import Path
import shutil

from .config import Toolchain, Transport
from .engines import EngineType
from .target.probe.jlink import JLINK_COMMANDER


@dataclass(frozen=True)
class DoctorCheck:
    """Availability result for one required or optional host dependency."""

    label: str
    name: str
    available: bool
    path: str | None = None
    required: bool = True
    hint: str | None = None


@dataclass(frozen=True)
class DoctorResult:
    """Structured host-readiness result returned by the programmatic API."""

    checks: tuple[DoctorCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.available for check in self.checks if check.required)

    @property
    def missing_required(self) -> tuple[DoctorCheck, ...]:
        """Required dependencies that are unavailable."""
        return tuple(check for check in self.checks if check.required and not check.available)


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
) -> DoctorResult:
    """Check required and optional host dependencies without printing."""
    results = [_inspect_dependency(spec) for spec in _dependency_specs(toolchain, transport, engine)]
    if require_segger_rtt:
        from .errors import FirmwareError
        from .firmware import _find_segger_rtt_dir

        try:
            resolved_rtt = _find_segger_rtt_dir(segger_rtt_path)
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
    return DoctorResult(tuple(results))


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
            "binary",
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
