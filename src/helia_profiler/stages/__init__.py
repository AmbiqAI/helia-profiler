"""Pipeline stages — one module per stage, re-exported here."""

from .s00_preflight import PreflightStage
from .s00b_ensure_powered import EnsureBoardPoweredStage
from .s01_resolve_platform import ResolvePlatformStage
from .s01a_resolve_jlink import ResolveJLinkProbeStage
from .s02_prepare_engine import PrepareEngineStage
from .s02a_analyze_model import AnalyzeModelStage
from .s02b_plan_memory import PlanMemoryStage
from .s03_generate_firmware import GenerateFirmwareStage
from .s04_build_firmware import BuildFirmwareStage
from .s05_flash_firmware import FlashFirmwareStage
from .s06_capture_pmu import CapturePmuStage
from .s07_capture_power import CapturePowerStage
from .s08_generate_report import GenerateReportStage

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
    "FlashFirmwareStage",
    "CapturePmuStage",
    "CapturePowerStage",
    "GenerateReportStage",
]
