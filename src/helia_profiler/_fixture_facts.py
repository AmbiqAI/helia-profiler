"""Supported targets, IO types and limits of run builds and captures."""

from __future__ import annotations

from dataclasses import dataclass

from ._fixture_build import (
    FIXTURE_CAPABILITIES,
    FIXTURE_READBACK_BUDGET,
    FixtureCapability,
    FixtureDType,
)
from .engines import EngineType
from .fixture_capture import FIXTURE_SETTLE_TICKS, FIXTURE_TIMER_HZ
from .fixture_image import MAX_IMAGE
from .fixture_target import (
    FIXTURE_BOARDS,
    FIXTURE_CLOCK_PROFILE,
    fixture_cpu_hz,
    fixture_engines_for_board,
    fixture_target_for_board,
)


@dataclass(frozen=True)
class QualifiedFixtureTarget:
    """A supported fixture build and capture target, not model qualification."""

    board: str
    clock_profile: str
    cpu_hz: int
    device: str
    load_address: int
    timer_hz: int
    image_limit: int
    settle_ticks: tuple[int, int]
    engines: tuple[EngineType, ...] = ()


@dataclass(frozen=True)
class EngineDTypeCapability:
    """Producer status of one engine and IO element type."""

    engine: EngineType
    dtype: FixtureDType
    capability: FixtureCapability


@dataclass(frozen=True)
class FixtureCapabilities:
    """Targets, engine and dtype status, and limits of this heliaPROFILER's runs."""

    targets: tuple[QualifiedFixtureTarget, ...]
    engines: tuple[EngineDTypeCapability, ...]
    readback_budget: int


def fixture_capabilities() -> FixtureCapabilities:
    """Report supported targets and public IO types, independently of qualification."""
    return FixtureCapabilities(
        targets=tuple(
            QualifiedFixtureTarget(
                board=target.board,
                clock_profile=FIXTURE_CLOCK_PROFILE,
                cpu_hz=fixture_cpu_hz(target.board),
                device=target.device,
                load_address=target.load_address,
                timer_hz=FIXTURE_TIMER_HZ,
                image_limit=MAX_IMAGE,
                settle_ticks=FIXTURE_SETTLE_TICKS,
                engines=fixture_engines_for_board(target.board),
            )
            for board in FIXTURE_BOARDS
            for target in (fixture_target_for_board(board),)
        ),
        engines=tuple(
            EngineDTypeCapability(engine, FixtureDType(dtype), capability)
            for engine, table in FIXTURE_CAPABILITIES.items()
            for dtype, capability in sorted(table.items())
        ),
        readback_budget=FIXTURE_READBACK_BUDGET,
    )
