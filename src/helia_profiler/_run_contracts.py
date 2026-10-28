"""Shared run admission and identity rules for execution and retained records."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, TypeAlias

from .errors import ConfigError
from .lifecycle import Lifecycle

if TYPE_CHECKING:
    from .fixtures.capture import FixtureCaptureResult
    from .fixtures.power import FixtureBoardPowerRequest, FixturePowerSync, FixtureRailPowerRequest
    from .fixtures.types import FixtureBuild
    from .identity import RunIdentity

    _PowerRequest: TypeAlias = FixtureRailPowerRequest | FixtureBoardPowerRequest | None

#: The rail-energy route the run API admits (helia-profiler#558): the gross MCU
#: rail over the timed restore-and-invoke loop of a carried heliaAOT or LiteRT
#: build on the Apollo510 EVB.
_POWER_ENGINES = ("helia-aot", "litert")
_POWER_BOARDS = ("apollo510_evb",)
_POWER_RAIL = "MCU"


def enrich_identity(
    identity: RunIdentity, lifecycle: Lifecycle, intent_hash: str, rail: str | None = None
) -> RunIdentity:
    """Apply the frozen intent and selected lifecycle without changing fixture facts."""
    from .identity import EnergyFacts

    run = replace(identity.run, lifecycle=lifecycle.value)
    if rail is not None and isinstance(run.energy, EnergyFacts):
        run = replace(run, energy=replace(run.energy, rail=rail))
    return replace(identity, run=run, intent_hash=intent_hash)


def check_capture_counts(build: FixtureBuild, capture: FixtureCaptureResult) -> None:
    timing = capture.timing
    if capture.state == "success" and (
        timing is None or (timing.iterations, timing.warmups) != (build.iterations, build.warmups)
    ):
        counts = "none" if timing is None else f"{timing.iterations} and {timing.warmups}"
        raise ConfigError(
            f"The device reported {counts} timed iterations and warmups; this build "
            f"runs {build.iterations} and {build.warmups}"
        )


def check_power_request(build: FixtureBuild, lifecycle: Lifecycle, power: _PowerRequest) -> None:
    """Validate power admission without acquiring device authority."""
    from .fixtures.power import FixtureBoardPowerRequest, FixtureRailPowerRequest

    wiring = build.power_sync
    if power is None:
        if wiring is not None:
            raise ConfigError(
                "This build is compiled for rail energy and needs a FixtureRailPowerRequest",
                hint="Its firmware waits for the instrument's GO before its timed loop.",
            )
        return
    if isinstance(power, FixtureBoardPowerRequest):
        if wiring is not None:
            raise ConfigError(
                "A supply-only FixtureBoardPowerRequest measures no energy; "
                "this build is compiled for rail energy",
                hint="Pass a FixtureRailPowerRequest, or build without power_sync.",
            )
    elif isinstance(power, FixtureRailPowerRequest):
        if wiring is None:
            raise ConfigError(
                "This build has no power wiring to measure rail energy with",
                hint="Build with FixtureMethod(..., power_sync=FixturePowerSync(...)).",
            )
        requested = power.firmware_wiring()
        if replace(requested, protocol_version=wiring.protocol_version) != wiring:
            raise ConfigError(
                f"The power request's GPIO pins {_pins(requested)} "
                f"differ from the build's {_pins(wiring)}",
                hint="Wire the instrument as the firmware was built, or build again.",
            )
        if requested.protocol_version != wiring.protocol_version:
            raise ConfigError(
                f"The build's firmware speaks power protocol {wiring.protocol_version}; "
                f"the run API's rail request speaks protocol {requested.protocol_version}",
                hint="Build again with build(); it compiles the current protocol.",
            )
        if power.rail != _POWER_RAIL:
            raise ConfigError(
                f"The run API measures the {_POWER_RAIL} rail only, not {power.rail!r}"
            )
        require_power_route(build.engine.value, build.target.board, lifecycle)
    else:
        raise ConfigError(
            f"Unknown power request {type(power).__name__}",
            hint="Pass a FixtureRailPowerRequest or a FixtureBoardPowerRequest.",
        )


def _pins(wiring: FixturePowerSync) -> str:
    return f"(sync {wiring.sync_gpio_pin}, state {wiring.state_gpio_pin}, GO {wiring.go_gpio_pin})"


def require_power_route(engine: str | None, board: str | None, lifecycle: Lifecycle) -> None:
    """Refuse rail energy outside the route the run API admits."""
    if lifecycle is not Lifecycle.CARRY:
        raise ConfigError(
            "The run API measures rail energy over a Lifecycle.CARRY build's timed loop, "
            f"not a Lifecycle.{lifecycle.name} one, which runs the model stateless",
            hint="Build with lifecycle=Lifecycle.CARRY for a power run.",
        )
    if engine not in _POWER_ENGINES:
        raise ConfigError(
            "The run API measures rail energy for heliaAOT and LiteRT builds, "
            + (f"not {engine}" if engine else "and the plan names no engine"),
            hint="Use a heliaAOT or LiteRT plan.",
        )
    if board not in _POWER_BOARDS:
        raise ConfigError(
            "The run API measures rail energy on the apollo510_evb, "
            + (f"not {board}" if board else "and the plan names no board"),
            hint="Build for apollo510_evb.",
        )
