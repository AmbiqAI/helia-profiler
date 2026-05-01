"""heliaPROFILER — Profile LiteRT models on Ambiq Apollo hardware."""

from ._version import __version__
from .api import profile
from .config import (
    EngineConfig,
    ModelConfig,
    OutputConfig,
    PowerConfig,
    ProfileConfig,
    ProfilingConfig,
    TargetConfig,
)
from .errors import (
    BuildError,
    CaptureError,
    ConfigError,
    EngineError,
    FirmwareError,
    HpxError,
    PlatformError,
    PowerError,
    ReportError,
)
from .results import (
    FirmwareMeta,
    LayerResult,
    NsxModuleRef,
    PmuResult,
    PresetResult,
    ProfileResult,
    RunMetadata,
)

__all__ = [
    "__version__",
    # Public API
    "profile",
    # Config
    "ProfileConfig",
    "ModelConfig",
    "EngineConfig",
    "TargetConfig",
    "ProfilingConfig",
    "PowerConfig",
    "OutputConfig",
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
    "CaptureError",
    "PowerError",
    "ReportError",
]
