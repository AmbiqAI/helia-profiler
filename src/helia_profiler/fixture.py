"""Public fixed-fixture API: build, capture and summarize bounded fixed-input firmware.

Only the names in ``__all__`` are the contract. ``FIXTURE_API_VERSION`` is
``(major, minor)``: minor grows with additive changes, major with removals or
changed semantics. ``tests/contracts/fixture_api_v1.json`` records the surface.
"""

from __future__ import annotations

from typing import Final

from ._fixture_build import (
    FIXTURE_CAPABILITIES,
    FIXTURE_READBACK_BUDGET,
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
from .engines import EngineType
from .fixture_analysis import (
    FixtureTensor,
    Int8Tensor,
    PerAxisQuantization,
    PerTensorQuantization,
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
    FixtureEnergyWindow,
    FixtureMeasurements,
    summarize_fixture_measurements,
)
from .fixture_runtime import FixtureFile, PreparedUpstreamRuntime
from .fixture_stage import FixtureStage
from .fixture_target import FixtureTarget, supported_fixture_target
from .hostenv.elf_inventory import ElfSection, LoadSegment, SectionInventory, section_inventory
from .placement import Placement
from .results.models import ToolchainInfo

FIXTURE_API_VERSION: Final[tuple[int, int]] = (1, 0)

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
    "build_fixed_fixture",
    "capture_fixture",
    "inspect_fixture_footprint",
    "section_inventory",
    "source_closure",
    "summarize_fixture_measurements",
    "supported_fixture_target",
    # Results
    "FixtureBuild",
    "FixtureCaptureResult",
    "FixtureEnergyWindow",
    "FixtureFootprint",
    "FixtureMeasurements",
    "FixtureMemory",
    "FixtureMetric",
    "FixtureTiming",
    "ElfSection",
    "LoadSegment",
    "SectionInventory",
    "SourceClosure",
    "ToolchainInfo",
]
