"""heliaPROFILER — Profile LiteRT models on Ambiq silicon"""

from ._version import __version__
from .api import profile
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
from .placement import ModelLocation, Placement
from .power.base import PowerMode, PowerResult
from .results import (
    FirmwareMeta,
    LayerResult,
    NsxModuleRef,
    PmuResult,
    PresetResult,
    ProfileResult,
    RunMetadata,
)
from .target.lifecycle import ResetStrategy

__all__ = [
    "__version__",
    # Public API
    "profile",
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
    "ModelLocation",
    "PowerMode",
    "PowerResult",
    "ResetStrategy",
    # Results
    "ProfileResult",
    "PmuResult",
    "PresetResult",
    "LayerResult",
    "FirmwareMeta",
    "RunMetadata",
    "NsxModuleRef",
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
