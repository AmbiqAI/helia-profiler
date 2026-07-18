"""heliaPROFILER — Profile LiteRT models on Ambiq silicon"""

from ._version import __version__
from .api import profile
from .compare import CompareResult
from .evaluation import (
    ComparabilityAssessment,
    ComparabilityIssue,
    ComparabilitySeverity,
    ComparisonProfile,
    ComparisonVerdict,
    MetricDirection,
    MetricPolicy,
    MetricVerdict,
    MissingMetricPolicy,
    VerdictStatus,
    assess_comparability,
    evaluate_comparison_profile,
    RunEvaluation,
    evaluate_run,
)
from .config import (
    BuildConfig,
    ClockSelection,
    EngineConfig,
    HeartbeatConfig,
    ModelConfig,
    NsxModuleOverride,
    OutputConfig,
    OutputFormat,
    PowerConfig,
    ProfileConfig,
    ProfilingConfig,
    TargetConfig,
    TimeoutsConfig,
    Toolchain,
    Transport,
)
from .engines import EngineType
from . import examples
from .counters import PmuCounter
from .doctor import DoctorCheck, DoctorResult
from .errors import (
    BuildError,
    CaptureError,
    ConfigError,
    EngineError,
    FirmwareError,
    HpxError,
    NetworkError,
    PlatformError,
    PowerError,
    ReportError,
)
from .placement import Placement
from .model_analysis import ModelAnalysis
from .platform import BoardDef
from .power.base import PowerMode, PowerResult
from .artifacts import OnDevicePowerSummary, PowerObservation, PowerTerminalRecord
from .results import (
    FirmwareMeta,
    LayerResult,
    NsxModuleRef,
    PmuResult,
    PresetResult,
    ProfileResult,
    RunMetadata,
)
from .result_manifest import (
    ResultArtifact,
    ResultIssue,
    ResultManifest,
    ResultValidity,
    RunStatus,
    load_result_manifest,
)
from .session import Session
from .target.probe.jlink import JLinkProbe, JLinkProbeMatch
from .target.lifecycle import ResetStrategy
from .transport.ports import SerialPortInfo

__all__ = [
    "__version__",
    # Public API
    "profile",
    "Session",
    "examples",
    # Config
    "ProfileConfig",
    "ModelConfig",
    "EngineConfig",
    "EngineType",
    "TargetConfig",
    "ProfilingConfig",
    "PowerConfig",
    "OutputConfig",
    "Toolchain",
    "Transport",
    "ClockSelection",
    "OutputFormat",
    "HeartbeatConfig",
    "TimeoutsConfig",
    "BuildConfig",
    "NsxModuleOverride",
    "Placement",
    "PowerMode",
    "PowerResult",
    "PowerObservation",
    "PowerTerminalRecord",
    "OnDevicePowerSummary",
    "ResetStrategy",
    # Results
    "ProfileResult",
    "PmuResult",
    "PresetResult",
    "LayerResult",
    "FirmwareMeta",
    "RunMetadata",
    "NsxModuleRef",
    "ModelAnalysis",
    "CompareResult",
    "ComparabilityAssessment",
    "ComparabilityIssue",
    "ComparabilitySeverity",
    "assess_comparability",
    "ComparisonProfile",
    "ComparisonVerdict",
    "MetricDirection",
    "MetricPolicy",
    "MetricVerdict",
    "MissingMetricPolicy",
    "VerdictStatus",
    "evaluate_comparison_profile",
    "RunEvaluation",
    "evaluate_run",
    "ResultManifest",
    "ResultArtifact",
    "ResultIssue",
    "ResultValidity",
    "RunStatus",
    "load_result_manifest",
    "DoctorCheck",
    "DoctorResult",
    "BoardDef",
    "PmuCounter",
    "JLinkProbe",
    "JLinkProbeMatch",
    "SerialPortInfo",
    # Errors
    "HpxError",
    "ConfigError",
    "PlatformError",
    "EngineError",
    "FirmwareError",
    "BuildError",
    "NetworkError",
    "CaptureError",
    "PowerError",
    "ReportError",
]
