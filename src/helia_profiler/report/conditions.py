"""The :class:`Conditions` a profile run measured under (#648)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..engines.base import HeliaAotArtifacts
from ..engines.helia_aot.compile import (
    aot_target_name,
    placement_memory_type,
    user_wildcard_kinds,
)
from ..placement import Placement
from ..results.models import Conditions

if TYPE_CHECKING:
    from ..pipeline import PipelineContext


def run_conditions(ctx: PipelineContext) -> Conditions:
    """The conditions *ctx* measured under, from its frozen config and capture."""
    meta = ctx.captured_pmu.meta
    platform = ctx.run_metadata.platform
    # The configured clock, a setting two runs can share. The on-device
    # measurement jitters from run to run and stays where the results report
    # it: firmware.measured_clock_hz.
    cpu_hz: int | None = None
    if platform is not None and platform.cpu_clock_mhz > 0:
        cpu_hz = platform.cpu_clock_mhz * 1_000_000
    elif meta.system_clock_hz:
        cpu_hz = meta.system_clock_hz
    return Conditions(
        target=aot_target_name(ctx.config),
        cpu_hz=cpu_hz,
        weights_memory=_weights_memory(ctx),
        external_memory_clock=_external_memory_clock_mhz(ctx),
    )


def _weights_memory(ctx: PipelineContext) -> str | None:
    """Where hpx's placement rule put the constants; ``None`` when it set none.

    A heliaAOT user rule for the ``constant`` kind replaces hpx's rule, so the
    planned region then says nothing about where heliaAOT put them.
    """
    if ctx.weights_region is None:
        return None
    artifacts = ctx.engine_artifacts
    if isinstance(artifacts, HeliaAotArtifacts):
        tensors = json.loads(artifacts.aot_memory_intent_json).get("tensors")
        if isinstance(tensors, list) and "constant" in user_wildcard_kinds(tensors):
            return None
    return placement_memory_type(ctx.weights_region)


def _external_memory_clock_mhz(ctx: PipelineContext) -> int | None:
    """The PSRAM clock in MHz: as the firmware reported it, else as configured.

    Absent when the run placed nothing in PSRAM and the firmware reported none.
    """
    psram = ctx.captured_pmu.meta.psram
    if psram is not None and psram.clock_hz > 0:
        return psram.clock_hz // 1_000_000
    if Placement.PSRAM in (ctx.weights_region, ctx.arena_region):
        return ctx.config.target.psram.clock_hz // 1_000_000
    return None
