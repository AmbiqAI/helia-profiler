"""What the installed profiler qualifies for fixed-fixture builds and captures."""

from __future__ import annotations

from dataclasses import dataclass

from ._fixture_build import (
    FIXTURE_CAPABILITIES,
    FIXTURE_READBACK_BUDGET,
    FixtureCapability,
    FixtureDType,
)
from .engines import EngineType
from .fixture_capture import FIXTURE_CPU_HZ, FIXTURE_SETTLE_TICKS, FIXTURE_TIMER_HZ
from .fixture_image import MAX_IMAGE
from .fixture_target import FIXTURE_CLOCK_PROFILE, supported_fixture_target


@dataclass(frozen=True)
class QualifiedFixtureTarget:
    """A board and clock profile with a qualified fixture build and capture path."""

    board: str
    clock_profile: str
    cpu_hz: int
    device: str
    load_address: int
    timer_hz: int
    image_limit: int
    settle_ticks: tuple[int, int]


@dataclass(frozen=True)
class EngineDTypeCapability:
    """Producer status of one engine and IO element type."""

    engine: EngineType
    dtype: FixtureDType
    capability: FixtureCapability


@dataclass(frozen=True)
class FixtureCapabilities:
    """Targets, engine and dtype status, and limits of this fixture API."""

    api_version: tuple[int, int]
    targets: tuple[QualifiedFixtureTarget, ...]
    engines: tuple[EngineDTypeCapability, ...]
    readback_budget: int


def fixture_capabilities() -> FixtureCapabilities:
    """Report the qualified targets and per-engine dtype status."""
    from .fixture import FIXTURE_API_VERSION

    target = supported_fixture_target()
    return FixtureCapabilities(
        api_version=FIXTURE_API_VERSION,
        targets=(
            QualifiedFixtureTarget(
                board=target.board,
                clock_profile=FIXTURE_CLOCK_PROFILE,
                cpu_hz=FIXTURE_CPU_HZ,
                device=target.device,
                load_address=target.load_address,
                timer_hz=FIXTURE_TIMER_HZ,
                image_limit=MAX_IMAGE,
                settle_ticks=FIXTURE_SETTLE_TICKS,
            ),
        ),
        engines=tuple(
            EngineDTypeCapability(engine, FixtureDType(dtype), capability)
            for engine, table in FIXTURE_CAPABILITIES.items()
            for dtype, capability in sorted(table.items())
        ),
        readback_budget=FIXTURE_READBACK_BUDGET,
    )
