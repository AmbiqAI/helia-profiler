"""Validate engine placement and profiling capabilities before preparation."""

from __future__ import annotations

from ..config import CleanWindowProbe, ProfileConfig, Transport
from ..errors import ConfigError
from ..placement import Placement
from . import EngineType, get_adapter
from .base import PsramWeightsSource


def check_runtime_split_locations(cfg: ProfileConfig) -> None:
    """Validate requested regions, including engine-specific PSRAM constraints."""
    runtime_arena = cfg.model.arena_location
    runtime_weights = cfg.model.weights_location

    # The engine, not the placement, decides how PSRAM gets populated
    # (#219) — so PSRAM validity is an adapter capability, not an
    # EngineType branch.
    adapter = get_adapter(cfg.engine.type)
    if runtime_arena == Placement.PSRAM or runtime_weights == Placement.PSRAM:
        if adapter.psram_weights_source is PsramWeightsSource.UNSUPPORTED:
            raise ConfigError(
                f"{adapter.name} profiling does not support PSRAM model or arena placement.",
                hint="Use model.arena_location=tcm|sram and model.weights_location=tcm|sram|mram.",
            )
        if (
            runtime_weights == Placement.PSRAM
            and adapter.psram_weights_source is PsramWeightsSource.HOST_UPLOAD
            and cfg.target.transport != Transport.RTT
        ):
            raise ConfigError(
                "PSRAM model weights require target.transport='rtt' for this engine.",
                hint=(
                    "Host-side PSRAM model upload uses the RTT transport. "
                    "Use --transport rtt, or keep weights in MRAM/SRAM."
                ),
            )
    # Engine-specific PSRAM constraints — called unconditionally, not just
    # when the coarse split fields say PSRAM: an engine can be steered into
    # PSRAM by its own config (heliaAOT per-tensor rules in
    # aot_args.memory.tensors) with both coarse fields unset, and that path
    # must hit the same fail-fast wall.
    adapter.check_psram_placement(cfg)
    _check_explicit_location(
        runtime_arena,
        name="model.arena_location",
        valid=(Placement.TCM, Placement.SRAM, Placement.PSRAM),
    )
    _check_explicit_location(
        runtime_weights,
        name="model.weights_location",
        valid=tuple(Placement),
    )


def _check_explicit_location(loc: str | None, *, name: str, valid: tuple[Placement, ...]) -> None:
    if loc is None:
        return
    if loc not in valid:
        raise ConfigError(
            f"Invalid {name}: '{loc}'.",
            hint=f"Expected one of: {', '.join(valid)}.",
        )


def check_profiling_support(
    engine: EngineType, *, power_enabled: bool, clean_window_probe: CleanWindowProbe
) -> None:
    """Reject profiling modes the selected engine cannot implement."""
    if engine is EngineType.EXECUTORCH and power_enabled:
        raise ConfigError(
            "ExecuTorch profiling does not yet support the dedicated power binary.",
            hint="Disable power capture; clean end-to-end cycle measurements are supported.",
        )
    if engine is EngineType.EXECUTORCH and clean_window_probe is CleanWindowProbe.BUSY_LOOP:
        # The busy_loop probe is a power-window diagnostic: it replaces the
        # model with a calibrated CPU spin so an external instrument has a
        # known-shape window to gate on, and reports HPX_CLEAN_INFER_COUNT=1
        # for the single unit of work it performs.  ExecuTorch has no power
        # support at all (rejected just above), so the probe has nothing to
        # gate -- and since #154 phase 4 the render would silently succeed:
        # main_executorch.cc.j2's engine_clean_window override delegates the
        # busy_loop branch straight back to the base, so the firmware would
        # come back with a nop-loop window reporting COUNT=1 where this
        # engine's HPX_CLEAN_INFER_* are defined as real execute-only
        # inference timing.
        raise ConfigError(
            "The busy_loop clean-window probe requires an engine with "
            "power-window support; engine.type=executorch has none.",
            hint=(
                "Use profiling.clean_window_probe=infer with ExecuTorch, or "
                "switch to an engine that supports the dedicated power binary "
                "(tflm, helia-rt, helia-aot)."
            ),
        )
