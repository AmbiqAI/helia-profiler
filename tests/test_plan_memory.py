"""Tests for PlanMemoryStage and MemoryPlan dataclasses."""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.config import load_config
from helia_profiler.engines.base import EngineArtifacts
from helia_profiler.errors import PlatformError
from helia_profiler.pipeline import PipelineContext
from helia_profiler.platform import BoardDef, MemoryLayout, SocDef, SocFamily, CoreArch, PmuTier, ClockConfig
from helia_profiler.results import MemoryConsumer, MemoryPlan, MemoryRegionUsage
from helia_profiler.stages.s01_resolve_platform import ResolvePlatformStage
from helia_profiler.stages.s02b_plan_memory import PlanMemoryStage


def _make_ctx(tmp_path: Path, overrides: dict | None = None) -> PipelineContext:
    model = tmp_path / "model.tflite"
    # Write a non-trivial number of bytes so synthesised plans have
    # something to place.
    model.write_bytes(b"\x00" * 2048)
    base = {
        "model": {"path": str(model), "arena_size": 65536},
        "engine": {"type": "tflm"},
        "work_dir": str(tmp_path / "work"),
    }
    if overrides:
        base.update(overrides)
    config = load_config(None, base)
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    ctx = PipelineContext(config=config, work_dir=work_dir)
    ResolvePlatformStage().run(ctx)
    return ctx


class TestMemoryRegionUsage:
    def test_free_and_overflow(self):
        r = MemoryRegionUsage(region="SRAM", capacity=1000, used=400)
        assert r.free == 600
        assert not r.overflow

    def test_overflow_detected(self):
        r = MemoryRegionUsage(region="DTCM", capacity=512, used=1024)
        assert r.free == 0
        assert r.overflow


class TestPlanMemorySynthesise:
    def test_synth_plan_default_auto_places_both_in_tcm(self, tmp_path: Path):
        """With ``model_location=auto`` (the default), both arena and
        a tiny model fit comfortably in DTCM on Apollo510."""
        ctx = _make_ctx(tmp_path)
        PlanMemoryStage().run(ctx)

        assert ctx.memory_plan is not None
        assert ctx.memory_plan.engine == "tflm"
        assert ctx.arena_region == "tcm"
        assert ctx.weights_region == "tcm"

        dtcm = ctx.memory_plan.region("DTCM")
        assert dtcm is not None
        assert any(c.kind == "weights" for c in dtcm.consumers)
        assert any(c.kind == "arena" and c.size == 65536 for c in dtcm.consumers)

    def test_synth_plan_explicit_mram_keeps_weights_in_mram(self, tmp_path: Path):
        """``model_location=mram`` puts weights in MRAM (rodata) but
        still places the arena in TCM when available."""
        ctx = _make_ctx(tmp_path, {
            "model": {
                "path": str(tmp_path / "model.tflite"),
                "arena_size": 65536,
                "model_location": "mram",
            },
        })
        PlanMemoryStage().run(ctx)

        assert ctx.arena_region == "tcm"
        assert ctx.weights_region == "mram"

        mram = ctx.memory_plan.region("MRAM")
        dtcm = ctx.memory_plan.region("DTCM")
        assert mram is not None and dtcm is not None
        assert any(c.kind == "weights" for c in mram.consumers)
        assert any(c.kind == "arena" and c.size == 65536 for c in dtcm.consumers)

    def test_synth_plan_psram_routes_weights(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, {
            "model": {
                "path": str(tmp_path / "model.tflite"),
                "arena_size": 65536,
                "model_location": "psram",
            },
        })
        PlanMemoryStage().run(ctx)

        assert ctx.arena_region == "sram"
        assert ctx.weights_region == "psram"

        psram = ctx.memory_plan.region("PSRAM")
        assert psram is not None
        assert any(c.kind == "weights" for c in psram.consumers)

    def test_empty_regions_added_from_soc(self, tmp_path: Path):
        """Regions the SoC has but the plan does not use should still
        appear (with capacity, used=0) so reports can show them."""
        ctx = _make_ctx(tmp_path)
        PlanMemoryStage().run(ctx)

        # Apollo510 has DTCM, ITCM and PSRAM — even tflm default plan
        # doesn't populate them, but they should appear with capacity.
        dtcm = ctx.memory_plan.region("DTCM")
        assert dtcm is not None
        assert dtcm.capacity > 0


class TestPlanMemoryEngineProvided:
    def test_engine_plan_is_preferred(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        # Pretend heliaAOT produced a precise plan already.
        plan = MemoryPlan(
            engine="helia_aot",
            regions=(
                MemoryRegionUsage(
                    region="MRAM", capacity=0, used=12_000,
                    consumers=(MemoryConsumer("weights", 12_000, "weights"),),
                ),
                MemoryRegionUsage(
                    region="DTCM", capacity=0, used=4_096,
                    consumers=(MemoryConsumer("dtcm_arena", 4_096, "arena"),),
                ),
            ),
            model_weight_bytes=12_000,
        )
        ctx.engine_artifacts = EngineArtifacts(memory_plan=plan)
        PlanMemoryStage().run(ctx)

        assert ctx.memory_plan.engine == "helia_aot"
        # Capacities should now be populated from the SoC layout.
        mram = ctx.memory_plan.region("MRAM")
        dtcm = ctx.memory_plan.region("DTCM")
        assert mram.capacity > 0
        assert dtcm.capacity > 0
        assert dtcm.used == 4_096


class TestPlanMemoryOverflow:
    def test_oversubscribed_region_raises(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        # Apollo510 DTCM is 508 KB; request far more.
        plan = MemoryPlan(
            engine="helia_aot",
            regions=(
                MemoryRegionUsage(
                    region="DTCM", capacity=0, used=8 * 1024 * 1024,
                    consumers=(MemoryConsumer("giant_arena", 8 * 1024 * 1024, "arena"),),
                ),
            ),
        )
        ctx.engine_artifacts = EngineArtifacts(memory_plan=plan)

        with pytest.raises(PlatformError) as exc_info:
            PlanMemoryStage().run(ctx)

        msg = str(exc_info.value)
        assert "DTCM" in msg
        assert "over" in msg.lower()
        assert exc_info.value.hint is not None
        assert "arena" in exc_info.value.hint.lower()

    def test_fit_does_not_raise(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        PlanMemoryStage().run(ctx)  # Synthesised plan should fit.
        assert not ctx.memory_plan.has_overflow
