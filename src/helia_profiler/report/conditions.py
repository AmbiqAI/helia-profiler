"""The :class:`Conditions` a profile run measured under (#648)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..engines import EngineType
from ..engines.base import HeliaAotArtifacts
from ..engines.helia_aot.compile import (
    _resolve_aot_tensor_rulesets,
    aot_target_name,
    placement_memory_type,
    user_wildcard_kinds,
)
from ..placement import Placement
from ..platform import get_soc_for_board
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
        weights_cache=meta.dcache or "on",
        external_memory_clock=_external_memory_clock_mhz(ctx),
    )


def _weights_memory(ctx: PipelineContext) -> str | None:
    """Where hpx's placement rule put the constants; ``None`` when it set none.

    A heliaAOT run records the constant rule the compile step sent, built by
    the same function, with the backend adjustments it applies: the staged
    destination when the rule stages the constants, else their memory. A
    user rule for the ``constant`` kind replaces hpx's, so the record then
    says nothing; a user catch-all rule's staging attribute applies under
    hpx's rule as heliaAOT merges them.
    """
    if ctx.config.engine.type is not EngineType.HELIA_AOT:
        return placement_memory_type(ctx.weights_region) if ctx.weights_region else None
    artifacts = ctx.engine_artifacts
    user_rules: list[Any] = []
    if isinstance(artifacts, HeliaAotArtifacts):
        tensors = json.loads(artifacts.aot_memory_intent_json).get("tensors")
        if isinstance(tensors, list):
            user_rules = tensors
    if "constant" in user_wildcard_kinds(user_rules):
        return None
    # heliaAOT merges matching rules by specificity: catch-all (``*``), then
    # type-only, then id-specific, later rules winning within a tier. The
    # first two tiers are modelled here: a user catch-all rule's attributes
    # sit under hpx's type-only constant rule. The third is not, and cannot
    # change what this records: an id-specific constant rule moves one tensor
    # and leaves the rule hpx sent for the constant kind as it is, and that
    # rule is the condition heliaOptimus names. A per-tensor move shows in the
    # plan's own placement facts, not in the run's conditions.
    soc = get_soc_for_board(ctx.config.target.board, registry=ctx.config.platform_registry)
    attributes: dict[str, Any] = {}
    for rule in user_rules:
        if isinstance(rule, dict) and rule.get("type") == "*" and rule.get("id") is None:
            attributes.update(rule.get("attributes") or {})
    for rule in _resolve_aot_tensor_rulesets(ctx.config, soc):
        if rule["type"] == "constant":
            attributes.update(rule["attributes"])
    memory = attributes.get("constant_destination_memory") or attributes.get("memory")
    return str(memory).lower() if memory else None


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
