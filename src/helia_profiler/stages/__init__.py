"""Pipeline stages — one module per stage, re-exported here.

The canonical execution order is ``profiler.build_default_pipeline()``;
stage docstrings carry no position numbers.
"""

from .analyze_model import AnalyzeModelStage
from .build_firmware import BuildFirmwareStage
from .build_power_firmware import BuildPowerFirmwareStage
from .capture_pmu import CapturePmuStage
from .capture_power import CapturePowerStage
from .collect_power_terminal import CollectPowerTerminalStage
from .ensure_powered import EnsureBoardPoweredStage
from .flash import FlashFirmwareStage
from .flash_power import FlashPowerFirmwareStage
from .generate_firmware import GenerateFirmwareStage
from .plan_memory import PlanMemoryStage
from .plan_power import PlanPowerRunStage
from .preflight import PreflightStage
from .prepare_engine import PrepareEngineStage
from .report import GenerateReportStage
from .resolve_platform import ResolvePlatformStage
from .resolve_probe import ResolveJLinkProbeStage
from .verify_placement import VerifyPlacementStage

__all__ = [
    "PreflightStage",
    "EnsureBoardPoweredStage",
    "ResolvePlatformStage",
    "ResolveJLinkProbeStage",
    "PrepareEngineStage",
    "AnalyzeModelStage",
    "PlanMemoryStage",
    "GenerateFirmwareStage",
    "BuildFirmwareStage",
    "VerifyPlacementStage",
    "FlashFirmwareStage",
    "CapturePmuStage",
    "PlanPowerRunStage",
    "BuildPowerFirmwareStage",
    "FlashPowerFirmwareStage",
    "CapturePowerStage",
    "CollectPowerTerminalStage",
    "GenerateReportStage",
]
