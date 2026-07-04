"""Target lifecycle policy for capture phases.

Power capture is sensitive to leftover debug, PMU, reset, and transport state.
This module centralizes the host-side reset/power actions needed before a
capture phase so stages do not encode board and SoC folklore inline.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import logging
import time
from typing import TYPE_CHECKING

from .errors import PowerError

if TYPE_CHECKING:
    from .pipeline import PipelineContext

log = logging.getLogger("hpx")


class CapturePhase(StrEnum):
    """High-level target phase the host is preparing to run."""

    PMU = "pmu"
    POWER = "power"
    SYNC_SELFTEST = "sync_selftest"


class ResetAction(StrEnum):
    """Target reset primitive selected by lifecycle policy."""

    NONE = "none"
    DEBUG_RESET = "debug_reset"
    SWPOI_RESET = "swpoi_reset"
    DEBUG_RESET_THEN_SWPOI = "debug_reset+swpoi_reset"


class ResetStrategy(StrEnum):
    """User-selectable reset policy for target lifecycle preparation."""

    AUTO = "auto"
    POWER_CYCLE = "power_cycle"
    NONE = ResetAction.NONE.value
    DEBUG_RESET = ResetAction.DEBUG_RESET.value
    SWPOI_RESET = ResetAction.SWPOI_RESET.value
    DEBUG_RESET_THEN_SWPOI = ResetAction.DEBUG_RESET_THEN_SWPOI.value


@dataclass(frozen=True)
class TargetLifecyclePlan:
    """Resolved host-side lifecycle actions before a capture phase."""

    phase: CapturePhase
    power_cycle_attempted: bool
    power_cycle_succeeded: bool
    reset_strategy: ResetStrategy
    reset_action: ResetAction
    actions: tuple[str, ...]
    timings_s: dict[str, float]

    def to_metadata(self) -> dict[str, object]:
        """Return a JSON-safe metadata representation."""
        metadata: dict[str, object] = {
            "phase": self.phase.value,
            "power_cycle_attempted": self.power_cycle_attempted,
            "power_cycle_succeeded": self.power_cycle_succeeded,
            "reset_strategy": self.reset_strategy.value,
            "reset_action": self.reset_action.value,
            "actions": list(self.actions),
        }
        if self.timings_s:
            metadata["timings_s"] = self.timings_s
        return metadata


def prepare_target_for_phase(
    ctx: PipelineContext,
    *,
    phase: CapturePhase,
    power_driver: object,
    power_driver_name: str,
) -> TargetLifecyclePlan:
    """Prepare the target for *phase* and return the lifecycle plan used."""
    if phase is not CapturePhase.POWER:
        return TargetLifecyclePlan(
            phase=phase,
            power_cycle_attempted=False,
            power_cycle_succeeded=False,
            reset_strategy=ResetStrategy.NONE,
            reset_action=ResetAction.NONE,
            actions=(),
            timings_s={},
        )

    timings_s: dict[str, float] = {}
    requested_reset_strategy = ResetStrategy(ctx.config.power.reset_strategy)
    power_cycle_attempted = requested_reset_strategy is ResetStrategy.POWER_CYCLE
    power_cycle_succeeded = False
    if power_cycle_attempted:
        power_cycle_succeeded = _time_action(
            timings_s,
            "power_cycle",
            lambda: _try_power_cycle(
                power_driver,
                power_driver_name,
            ),
        )
    reset_action = _time_action(
        timings_s,
        "reset",
        lambda: _reset_for_power_phase(ctx, requested_reset_strategy),
    )
    return TargetLifecyclePlan(
        phase=phase,
        power_cycle_attempted=power_cycle_attempted,
        power_cycle_succeeded=power_cycle_succeeded,
        reset_strategy=requested_reset_strategy,
        reset_action=reset_action,
        actions=_lifecycle_actions(power_cycle_succeeded, reset_action),
        timings_s=timings_s,
    )


def _time_action(timings_s: dict[str, float], key: str, action):
    started = time.monotonic()
    try:
        return action()
    finally:
        timings_s[key] = round(time.monotonic() - started, 6)


def _try_power_cycle(power_driver: object, power_driver_name: str) -> bool:
    try:
        power_driver.power_cycle(off_time_s=0.5, settle_time_s=2.0)  # type: ignore[attr-defined]
        log.info("Power-cycle reset via '%s' succeeded", power_driver_name)
        return True
    except PowerError:
        raise


def _reset_for_power_phase(ctx: PipelineContext, requested: ResetStrategy) -> ResetAction:
    if requested is ResetStrategy.POWER_CYCLE:
        return ResetAction.NONE

    if ctx.soc is None or not ctx.soc.jlink_device:
        return ResetAction.NONE

    if requested is ResetStrategy.AUTO:
        requested = _default_power_reset_strategy(ctx)

    return _execute_reset_strategy(ctx, requested)


def _default_power_reset_strategy(ctx: PipelineContext) -> ResetStrategy:
    from .platform import SocFamily

    # Apollo5-family only: a debug-level reset alone leaves PMU/power-management
    # state that was measured to inflate AP510 steady-state power. Keep AP3/AP4
    # on debug reset until their SWPOI behavior is validated as a replacement.
    if ctx.soc.family is SocFamily.AP5:
        return ResetStrategy.DEBUG_RESET_THEN_SWPOI
    return ResetStrategy.DEBUG_RESET


def _execute_reset_strategy(ctx: PipelineContext, strategy: ResetStrategy) -> ResetAction:
    if strategy is ResetStrategy.NONE:
        return ResetAction.NONE

    from .jlink import reset_target, reset_target_poi

    jlink_serial = ctx.resolved_jlink_serial or ctx.config.target.jlink_serial

    if strategy is ResetStrategy.DEBUG_RESET:
        reset_target(device=ctx.soc.jlink_device, jlink_serial=jlink_serial)
        return ResetAction.DEBUG_RESET
    if strategy is ResetStrategy.SWPOI_RESET:
        reset_target_poi(device=ctx.soc.jlink_device, jlink_serial=jlink_serial)
        return ResetAction.SWPOI_RESET
    if strategy is ResetStrategy.DEBUG_RESET_THEN_SWPOI:
        reset_target(device=ctx.soc.jlink_device, jlink_serial=jlink_serial)
        reset_target_poi(device=ctx.soc.jlink_device, jlink_serial=jlink_serial)
        return ResetAction.DEBUG_RESET_THEN_SWPOI

    raise AssertionError(f"Unhandled reset strategy: {strategy}")


def _lifecycle_actions(power_cycle_succeeded: bool, reset_action: ResetAction) -> tuple[str, ...]:
    actions: list[str] = []
    if power_cycle_succeeded:
        actions.append("power_cycle")
    if reset_action is not ResetAction.NONE:
        actions.append(reset_action.value)
    return tuple(actions)


__all__ = [
    "CapturePhase",
    "ResetAction",
    "ResetStrategy",
    "TargetLifecyclePlan",
    "prepare_target_for_phase",
]