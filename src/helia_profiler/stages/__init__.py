"""Pipeline stages — one module per stage, re-exported here."""

from .preflight import PreflightStage
from .ensure_powered import EnsureBoardPoweredStage
from .resolve_platform import ResolvePlatformStage
from .resolve_probe import ResolveJLinkProbeStage
from .prepare_engine import PrepareEngineStage
from .analyze_model import AnalyzeModelStage
from .plan_memory import PlanMemoryStage
from .generate_firmware import GenerateFirmwareStage
from .build_firmware import BuildFirmwareStage
from .verify_placement import VerifyPlacementStage
from .flash import FlashFirmwareStage
from .capture_pmu import CapturePmuStage
from .plan_power import PlanPowerRunStage
from .build_power_firmware import BuildPowerFirmwareStage
from .flash_power import FlashPowerFirmwareStage
from .capture_power import CapturePowerStage
from .collect_power_terminal import CollectPowerTerminalStage
from .report import GenerateReportStage

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
