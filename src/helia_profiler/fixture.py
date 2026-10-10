"""Public fixed-fixture API: build, capture and summarize bounded fixed-input firmware.

Only the names in ``__all__`` are the contract. ``FIXTURE_API_VERSION`` is
``(major, minor)``: minor grows with additive changes, major with removals or
changed semantics. ``tests/contracts/fixture_api_v<major>.json`` records the surface.
"""

from __future__ import annotations

from typing import Final

from ._fixture_build import (
    FIXTURE_CAPABILITIES,
    FIXTURE_READBACK_BUDGET,
    EngineSource,
    FixedFixture,
    FixtureBackend,
    FixtureBuild,
    FixtureCapability,
    FixtureDType,
    FixtureIO,
    FixtureMethod,
    FixtureRole,
    FixtureTimingScope,
    TypedFixture,
    build_fixed_fixture,
)
from ._fixture_closure import SourceClosure, source_closure
from ._fixture_facts import (
    EngineDTypeCapability,
    FixtureCapabilities,
    QualifiedFixtureTarget,
    fixture_capabilities,
)
from ._fixture_request import (
    FixtureBuildRequest,
    FixturePlacement,
    HeliaAotOptions,
    build_fixture,
)
from .engines import EngineType
from .fixture_analysis import (
    FixtureTensor,
    Int8Tensor,
    PerAxisQuantization,
    PerTensorQuantization,
    TypedFixtureModelAnalysis,
    analyze_typed_fixture_model,
)
from .fixture_capture import (
    FixtureCaptureGuard,
    FixtureCaptureRequest,
    FixtureCaptureResult,
    FixtureMemory,
    FixtureTiming,
    capture_fixture,
)
from .fixture_metrics import FixtureFootprint, FixtureMetric, inspect_fixture_footprint
from .fixture_observation import (
    FixtureMeasurements,
    summarize_fixture_measurements,
)
from .fixture_runtime import (
    FixtureFile,
    PreparedUpstreamRuntime,
    RuntimeABI,
    RuntimeHeader,
    RuntimeProvider,
    VerifiedPreparedRuntime,
)
from .fixture_runtime import RuntimeManifest as PreparedRuntimeManifest
from .fixture_stage import FixtureStage
from .fixture_target import FixtureTarget, supported_fixture_target
from .hostenv.elf_inventory import ElfSection, LoadSegment, SectionInventory, section_inventory
from .placement import Placement
from .results.models import ToolchainInfo

FIXTURE_API_VERSION: Final[tuple[int, int]] = (3, 2)

__all__ = [
    "FIXTURE_API_VERSION",
    # Producer declarations
    "FIXTURE_CAPABILITIES",
    "FIXTURE_READBACK_BUDGET",
    # Closed sets
    "EngineType",
    "FixtureBackend",
    "FixtureCapability",
    "FixtureDType",
    "FixtureRole",
    "FixtureStage",
    "FixtureTimingScope",
    "Placement",
    # Inputs
    "FixtureBuildRequest",
    "FixturePlacement",
    "HeliaAotOptions",
    "FixedFixture",
    "FixtureCaptureRequest",
    "FixtureFile",
    "FixtureIO",
    "FixtureMethod",
    "FixtureTarget",
    "FixtureTensor",
    "Int8Tensor",
    "PerAxisQuantization",
    "PerTensorQuantization",
    "PreparedUpstreamRuntime",
    "TypedFixture",
    # Caller-supplied authority
    "FixtureCaptureGuard",
    # Operations
    "analyze_typed_fixture_model",
    "build_fixture",
    "fixture_capabilities",
    "build_fixed_fixture",
    "capture_fixture",
    "inspect_fixture_footprint",
    "section_inventory",
    "source_closure",
    "summarize_fixture_measurements",
    "supported_fixture_target",
    # Results
    "EngineDTypeCapability",
    "EngineSource",
    "FixtureCapabilities",
    "PreparedRuntimeManifest",
    "QualifiedFixtureTarget",
    "RuntimeABI",
    "RuntimeHeader",
    "RuntimeProvider",
    "VerifiedPreparedRuntime",
    "FixtureBuild",
    "FixtureCaptureResult",
    "FixtureFootprint",
    "FixtureMeasurements",
    "TypedFixtureModelAnalysis",
    "FixtureMemory",
    "FixtureMetric",
    "FixtureTiming",
    "ElfSection",
    "LoadSegment",
    "SectionInventory",
    "SourceClosure",
    "ToolchainInfo",
]
