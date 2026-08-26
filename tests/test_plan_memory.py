"""Tests for PlanMemoryStage and MemoryPlan dataclasses."""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.config import load_config
from helia_profiler.engines import EngineType
from helia_profiler.engines.base import ExecutorchArtifacts, HeliaAotArtifacts
from helia_profiler.errors import PlatformError
from helia_profiler.pipeline import PipelineContext
from helia_profiler.placement import MemoryRegion
from helia_profiler.platform import BoardDef, MemoryLayout, SocDef, SocFamily, CoreArch, PmuTier
from helia_profiler.results import ConsumerKind, MemoryConsumer, MemoryPlan, MemoryRegionUsage
from helia_profiler.stages.resolve_platform import ResolvePlatformStage
from helia_profiler.stages.plan_memory import PlanMemoryStage


def _make_ctx(tmp_path: Path, overrides: dict | None = None) -> PipelineContext:
    model = tmp_path / "model.tflite"
    # Write a non-trivial number of bytes so synthesised plans have
    # something to place.
    model.write_bytes(b"\x00" * 2048)
    base = {
        "model": {"path": str(model), "arena_size": 65536},
        "engine": {"type": "helia-rt"},
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
        r = MemoryRegionUsage(region=MemoryRegion.SRAM, capacity=1000, used=400)
        assert r.free == 600
        assert not r.overflow

    def test_overflow_detected(self):
        r = MemoryRegionUsage(region=MemoryRegion.DTCM, capacity=512, used=1024)
        assert r.free == 0
        assert r.overflow


class TestPlanMemorySynthesise:
    def test_executorch_plan_includes_all_explicit_runtime_buffers(self, tmp_path: Path):
        model = tmp_path / "model.pte"
        model.write_bytes(b"\x00\x00\x00\x00ET" + b"\x00" * 2048)
        config = load_config(
            None,
            {
                "model": {
                    "path": str(model),
                    "arena_size": 163840,
                    "arena_location": "tcm",
                    "weights_location": "mram",
                },
                "engine": {"type": "executorch"},
                "work_dir": str(tmp_path / "work"),
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path / "work")
        ResolvePlatformStage().run(ctx)
        ctx.engine_artifacts = ExecutorchArtifacts(
            engine_type=config.engine.type,
            engine_header="nsx_executorch.h",
            executorch_method_arena_size=65536,
            executorch_planned_arena_size=163840,
            executorch_temporary_arena_size=32768,
            executorch_input_size=12288,
            executorch_output_size=40,
        )

        PlanMemoryStage().run(ctx)

        assert ctx.memory_plan is not None
        dtcm = ctx.memory_plan.region("DTCM")
        sram = ctx.memory_plan.region("SRAM")
        mram = ctx.memory_plan.region("MRAM")
        assert dtcm is not None and sram is not None and mram is not None
        sizes = {consumer.name: consumer.size for consumer in dtcm.consumers}
        assert sizes == {
            "planned_arena": 163840,
            "method_arena": 65536,
            "temporary_arena": 32768,
            "input_buffer": 12288,
            "output_buffer": 40,
            # hpx-owned (#133 Phase 3): 16 KB boot stack + RTT statics
            # (32768 up + 16 down + 168 control block) land in DTCM on
            # the AP5 family.
            "boot_stack": 16384,
            "rtt_buffers": 32952,
        }
        assert {consumer.name for consumer in sram.consumers} == {"pmu_layer_records"}
        assert {consumer.name for consumer in mram.consumers} == {"pte_program"}

    def test_executorch_per_buffer_region_overrides(self, tmp_path: Path):
        model = tmp_path / "model.pte"
        model.write_bytes(b"\x00\x00\x00\x00ET" + b"\x00" * 2048)
        config = load_config(
            None,
            {
                "model": {
                    "path": str(model),
                    "arena_size": 163840,
                    "arena_location": "tcm",
                    "weights_location": "mram",
                },
                "engine": {"type": "executorch"},
                "work_dir": str(tmp_path / "work"),
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path / "work")
        ResolvePlatformStage().run(ctx)
        ctx.engine_artifacts = ExecutorchArtifacts(
            engine_type=config.engine.type,
            engine_header="nsx_executorch.h",
            executorch_method_arena_size=65536,
            executorch_planned_arena_size=163840,
            executorch_temporary_arena_size=32768,
            executorch_input_size=12288,
            executorch_output_size=40,
            executorch_method_arena_region="sram",
            executorch_temporary_arena_region="sram",
            executorch_io_region="sram",
        )
        PlanMemoryStage().run(ctx)

        assert ctx.memory_plan is not None
        dtcm = ctx.memory_plan.region("DTCM")
        sram = ctx.memory_plan.region("SRAM")
        assert dtcm is not None and sram is not None
        # Only the planned arena follows arena_location; the overridden
        # buffers are accounted in SRAM where the firmware places them.
        assert {c.name for c in dtcm.consumers} == {
            "planned_arena",
            "boot_stack",
            "rtt_buffers",
        }
        assert {c.name for c in sram.consumers} == {
            "method_arena",
            "temporary_arena",
            "input_buffer",
            "output_buffer",
            "pmu_layer_records",
        }

    def test_executorch_sram_places_complete_runtime_workspace_in_sram(self, tmp_path: Path):
        model = tmp_path / "model.pte"
        model.write_bytes(b"\x00\x00\x00\x00ET" + b"\x00" * 2048)
        config = load_config(
            None,
            {
                "model": {
                    "path": str(model),
                    "arena_size": 138240,
                    "arena_location": "sram",
                    "weights_location": "mram",
                },
                "engine": {"type": "executorch"},
                "work_dir": str(tmp_path / "work"),
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path / "work")
        ResolvePlatformStage().run(ctx)
        ctx.engine_artifacts = ExecutorchArtifacts(
            engine_type=config.engine.type,
            engine_header="nsx_executorch.h",
            executorch_method_arena_size=65536,
            executorch_planned_arena_size=138240,
            executorch_temporary_arena_size=32768,
            executorch_input_size=110592,
            executorch_output_size=8,
        )

        PlanMemoryStage().run(ctx)

        assert ctx.memory_plan is not None
        sram = ctx.memory_plan.region("SRAM")
        assert sram is not None
        sizes = {consumer.name: consumer.size for consumer in sram.consumers}
        assert sizes == {
            "planned_arena": 138240,
            "method_arena": 65536,
            "temporary_arena": 32768,
            "input_buffer": 110592,
            "output_buffer": 8,
            "pmu_layer_records": 4096 * 32,
        }

    def test_synth_plan_default_auto_places_both_in_tcm(self, tmp_path: Path):
        """With automatic placement (the default), both arena and
        a tiny model fit comfortably in DTCM on Apollo510."""
        ctx = _make_ctx(tmp_path)
        PlanMemoryStage().run(ctx)

        assert ctx.memory_plan is not None
        assert ctx.memory_plan.engine == "helia-rt"
        assert ctx.arena_region == "tcm"
        assert ctx.weights_region == "tcm"

        dtcm = ctx.memory_plan.region("DTCM")
        assert dtcm is not None
        assert any(c.kind == "weights" for c in dtcm.consumers)
        assert any(c.kind == "arena" and c.size == 65536 for c in dtcm.consumers)

    def test_unset_arena_size_uses_shared_default_for_auto(self, tmp_path: Path):
        """When arena_size is omitted, auto placement should still use the
        shared 256 KiB default that firmware generation emits."""
        model = tmp_path / "mid_model.tflite"
        model.write_bytes(b"\x00" * (300 * 1024))
        config = load_config(
            None,
            {
                "model": {"path": str(model), "arena_size": None},
                "engine": {"type": "helia-rt"},
                "work_dir": str(tmp_path / "work"),
            },
        )
        work_dir = tmp_path / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        ctx = PipelineContext(config=config, work_dir=work_dir)
        ResolvePlatformStage().run(ctx)

        PlanMemoryStage().run(ctx)

        assert ctx.arena_region == "tcm"
        assert ctx.weights_region == "sram"

        assert ctx.memory_plan is not None
        dtcm = ctx.memory_plan.region("DTCM")
        sram = ctx.memory_plan.region("SRAM")
        assert dtcm is not None and sram is not None
        assert any(c.kind == "arena" and c.size == 256 * 1024 for c in dtcm.consumers)
        assert any(c.kind == "weights" and c.size == 300 * 1024 for c in sram.consumers)

    def test_auto_keeps_weights_in_mram_when_arena_needs_sram(self, tmp_path: Path):
        model = tmp_path / "large_model.tflite"
        model.write_bytes(b"\x00" * (300 * 1024))
        ctx = _make_ctx(
            tmp_path,
            {
                "model": {
                    "path": str(model),
                    "arena_size": 600 * 1024,
                },
            },
        )

        PlanMemoryStage().run(ctx)

        assert ctx.arena_region == "sram"
        assert ctx.weights_region == "mram"

    def test_auto_keeps_mid_sized_weights_out_of_tcm_when_headroom_is_tight(self, tmp_path: Path):
        model = tmp_path / "mid_model_100k.tflite"
        model.write_bytes(b"\x00" * (100 * 1024))
        ctx = _make_ctx(
            tmp_path,
            {
                "model": {
                    "path": str(model),
                    "arena_size": 256 * 1024,
                },
            },
        )

        PlanMemoryStage().run(ctx)

        assert ctx.arena_region == "tcm"
        assert ctx.weights_region == "sram"

    def test_synth_plan_explicit_mram_keeps_weights_in_mram(self, tmp_path: Path):
        """Explicit MRAM weights retain automatic fast arena placement."""
        ctx = _make_ctx(
            tmp_path,
            {
                "model": {
                    "path": str(tmp_path / "model.tflite"),
                    "arena_size": 65536,
                    "weights_location": "mram",
                },
            },
        )
        PlanMemoryStage().run(ctx)

        assert ctx.arena_region == "tcm"
        assert ctx.weights_region == "mram"

        assert ctx.memory_plan is not None
        mram = ctx.memory_plan.region("MRAM")
        dtcm = ctx.memory_plan.region("DTCM")
        assert mram is not None and dtcm is not None
        assert any(c.kind == "weights" for c in mram.consumers)
        assert any(c.kind == "arena" and c.size == 65536 for c in dtcm.consumers)

    def test_synth_plan_explicit_mram_falls_back_to_sram_when_tcm_too_small(self, tmp_path: Path):
        """MRAM weights retain automatic SRAM fallback for a large arena."""
        ctx = _make_ctx(
            tmp_path,
            {
                "model": {
                    "path": str(tmp_path / "model.tflite"),
                    "arena_size": 600 * 1024,
                    "weights_location": "mram",
                },
            },
        )
        PlanMemoryStage().run(ctx)

        assert ctx.arena_region == "sram"
        assert ctx.weights_region == "mram"

        assert ctx.memory_plan is not None
        sram = ctx.memory_plan.region("SRAM")
        mram = ctx.memory_plan.region("MRAM")
        assert sram is not None and mram is not None
        assert any(c.kind == "arena" and c.size == 600 * 1024 for c in sram.consumers)
        assert any(c.kind == "weights" for c in mram.consumers)

    def test_synth_plan_psram_routes_weights(self, tmp_path: Path):
        ctx = _make_ctx(
            tmp_path,
            {
                "model": {
                    "path": str(tmp_path / "model.tflite"),
                    "arena_size": 65536,
                    "arena_location": "sram",
                    "weights_location": "psram",
                },
            },
        )
        PlanMemoryStage().run(ctx)

        assert ctx.arena_region == "sram"
        assert ctx.weights_region == "psram"

        assert ctx.memory_plan is not None
        psram = ctx.memory_plan.region("PSRAM")
        assert psram is not None
        assert any(c.kind == "weights" for c in psram.consumers)

    def test_explicit_weights_override_is_applied(self, tmp_path: Path):
        ctx = _make_ctx(
            tmp_path,
            {
                "model": {
                    "path": str(tmp_path / "model.tflite"),
                    "arena_size": 65536,
                    "weights_location": "sram",
                },
            },
        )
        PlanMemoryStage().run(ctx)

        assert ctx.arena_region == "tcm"
        assert ctx.weights_region == "sram"

    def test_explicit_arena_override_is_applied(self, tmp_path: Path):
        ctx = _make_ctx(
            tmp_path,
            {
                "model": {
                    "path": str(tmp_path / "model.tflite"),
                    "arena_size": 65536,
                    "arena_location": "sram",
                },
            },
        )
        PlanMemoryStage().run(ctx)

        assert ctx.arena_region == "sram"

    def test_explicit_weights_psram_requires_psram_board(self, tmp_path: Path):
        ctx = _make_ctx(
            tmp_path,
            {
                "model": {
                    "path": str(tmp_path / "model.tflite"),
                    "weights_location": "psram",
                },
            },
        )
        PlanMemoryStage().run(ctx)

        assert ctx.weights_region == "psram"

    def test_empty_regions_added_from_soc(self, tmp_path: Path):
        """Regions the SoC has but the plan does not use should still
        appear (with capacity, used=0) so reports can show them."""
        ctx = _make_ctx(tmp_path)
        PlanMemoryStage().run(ctx)

        # Apollo510 has DTCM, ITCM and PSRAM — even tflm default plan
        # doesn't populate them, but they should appear with capacity.
        assert ctx.memory_plan is not None
        dtcm = ctx.memory_plan.region("DTCM")
        assert dtcm is not None
        assert dtcm.capacity > 0


class TestPlanMemoryEngineProvided:
    def test_engine_plan_is_preferred(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        # Pretend heliaAOT produced a precise plan already.
        plan = MemoryPlan(
            engine=EngineType.HELIA_AOT,
            regions=(
                MemoryRegionUsage(
                    region=MemoryRegion.MRAM,
                    capacity=0,
                    used=12_000,
                    consumers=(MemoryConsumer("weights", 12_000, ConsumerKind.WEIGHTS),),
                ),
                MemoryRegionUsage(
                    region=MemoryRegion.DTCM,
                    capacity=0,
                    used=4_096,
                    consumers=(MemoryConsumer("dtcm_arena", 4_096, ConsumerKind.ARENA),),
                ),
            ),
            model_weight_bytes=12_000,
        )
        ctx.engine_artifacts = HeliaAotArtifacts(
            engine_header="model_model.h",
            aot_prefix="model",
            aot_module_name="aot-model",
            aot_cmake_target="nsx::aot_model",
            helia_aot_version="0.18.4",
            memory_plan=plan,
        )
        PlanMemoryStage().run(ctx)

        assert ctx.memory_plan is not None
        assert ctx.memory_plan.engine == "helia-aot"
        # Capacities should now be populated from the SoC layout.
        mram = ctx.memory_plan.region("MRAM")
        dtcm = ctx.memory_plan.region("DTCM")
        assert mram is not None and dtcm is not None
        assert mram.capacity > 0
        assert dtcm.capacity > 0
        # 4 KiB engine arena + hpx-owned boot_stack (16384) +
        # rtt_buffers (32952): the engine-supplied plan gains the shared
        # consumers too (#133 Phase 3).
        assert dtcm.used == 4_096 + 16_384 + 32_952


class TestPlanMemoryOverflow:
    def test_oversubscribed_region_raises(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        # Apollo510 DTCM is 508 KB; request far more.
        plan = MemoryPlan(
            engine=EngineType.HELIA_AOT,
            regions=(
                MemoryRegionUsage(
                    region=MemoryRegion.DTCM,
                    capacity=0,
                    used=8 * 1024 * 1024,
                    consumers=(MemoryConsumer("giant_arena", 8 * 1024 * 1024, ConsumerKind.ARENA),),
                ),
            ),
        )
        ctx.engine_artifacts = HeliaAotArtifacts(
            engine_header="model_model.h",
            aot_prefix="model",
            aot_module_name="aot-model",
            aot_cmake_target="nsx::aot_model",
            helia_aot_version="0.18.4",
            memory_plan=plan,
        )

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
        assert ctx.memory_plan is not None
        assert not ctx.memory_plan.has_overflow


class TestHpxOwnedConsumers:
    """#133 Phase 3 D1/D2: the consumers every firmware reserves that hpx
    decides host-side. Sizes pin the frozen template-mirror tables so
    drift is a reviewed edit."""

    def test_record_size_table_is_the_frozen_contract(self):
        from helia_profiler.engines import EngineType
        from helia_profiler.stages.plan_memory import PMU_RECORD_SIZE_BYTES

        assert PMU_RECORD_SIZE_BYTES == {
            EngineType.TFLM: 24,
            EngineType.HELIA_RT: 24,
            EngineType.HELIA_AOT: 20,
            EngineType.EXECUTORCH: 32,
        }

    def test_records_planned_for_every_engine_with_true_sizes(self, tmp_path):
        # apollo510 (default board): pmu_max_ops = 4096. TFLM/heliaRT book
        # the whole g_profiler OBJECT (records + the 252-byte header
        # characterized on ARMV8M_PMU parts) so the plan matches the
        # linked symbol byte-for-byte.
        for engine, expected in (
            ("helia-rt", 4096 * 24 + 252),
            ("tflm", 4096 * 24 + 252),
        ):
            ctx = _make_ctx(tmp_path, {"engine": {"type": engine}})
            PlanMemoryStage().run(ctx)
            assert ctx.memory_plan is not None
            sram = ctx.memory_plan.region("SRAM")
            assert sram is not None
            records = [c for c in sram.consumers if c.name == "pmu_layer_records"]
            assert [c.size for c in records] == [expected], engine

    def test_dwt_tier_records_book_the_smaller_object_header(self, tmp_path):
        """#180 review M2: the DWT-tier profiler object has NO config
        struct (has_armv8m_pmu gate) — vptr 4 + state 52 = 56, as
        derivable as the ARMV8M 252. apollo4p: 2048 records x 24 + 56."""
        ctx = _make_ctx(tmp_path, {"target": {"board": "apollo4p_evb"}})
        PlanMemoryStage().run(ctx)
        assert ctx.memory_plan is not None
        records = [
            c
            for r in ctx.memory_plan.regions
            for c in r.consumers
            if c.name == "pmu_layer_records"
        ]
        assert [c.size for c in records] == [2048 * 24 + 56]

    def test_apollo5b_records_region_is_per_engine_macro(self, tmp_path):
        """#180 review n4: apollo5b has NSX_MEM_SRAM (.shared -> SRAM) but
        no NSX_MEM_SRAM_BSS (falls to .bss -> DTCM). TFLM/heliaRT's
        g_profiler uses the former; AOT's g_layers the latter — the region
        must follow the ENGINE's macro."""
        ctx_rt = _make_ctx(
            tmp_path, {"target": {"board": "apollo5b_evb"}}
        )
        PlanMemoryStage().run(ctx_rt)
        assert ctx_rt.memory_plan is not None
        sram = ctx_rt.memory_plan.region("SRAM")
        assert sram is not None
        sram_names = {c.name for c in sram.consumers}
        assert "pmu_layer_records" in sram_names

        ctx_aot = _make_ctx(
            tmp_path,
            {
                "engine": {"type": "helia-aot"},
                "target": {"board": "apollo5b_evb"},
            },
        )
        PlanMemoryStage().run(ctx_aot)
        assert ctx_aot.memory_plan is not None
        dtcm = ctx_aot.memory_plan.region("DTCM")
        assert dtcm is not None
        dtcm_names = {c.name for c in dtcm.consumers}
        assert "pmu_layer_records" in dtcm_names

    def test_rtt_lands_in_dtcm_on_ap5_and_sram_on_ap4(self, tmp_path):
        ctx = _make_ctx(tmp_path)  # apollo510-family default, rtt
        PlanMemoryStage().run(ctx)
        assert ctx.memory_plan is not None
        dtcm = ctx.memory_plan.region("DTCM")
        assert dtcm is not None
        dtcm_names = {c.name for c in dtcm.consumers}
        assert "rtt_buffers" in dtcm_names  # the scarce-DTCM part gets it
        rtt = [c for c in dtcm.consumers if c.name == "rtt_buffers"]
        assert [c.size for c in rtt] == [32768 + 16 + 168]

        ctx4 = _make_ctx(tmp_path, {"target": {"board": "apollo4p_evb"}})
        PlanMemoryStage().run(ctx4)
        assert ctx4.memory_plan is not None
        sram = ctx4.memory_plan.region("SRAM")
        assert sram is not None
        sram_names = {c.name for c in sram.consumers}
        assert "rtt_buffers" in sram_names  # .sram_bss parts pin it to SRAM

    def test_boot_stack_is_family_keyed_not_stack_size_keyed(self, tmp_path):
        """AP2/3/3P startup hardcodes g_pui32Stack[1024] (4 KB) and
        ignores STACK_SIZE; AP4/AP5 declare [STACK_SIZE] uint32s (16 KB).
        And on AP3 the STACKMEM slot sits inside the verified SRAM
        window, not TCM."""
        from helia_profiler.results import ConsumerKind

        ctx = _make_ctx(tmp_path)
        PlanMemoryStage().run(ctx)
        assert ctx.memory_plan is not None
        dtcm = ctx.memory_plan.region("DTCM")
        assert dtcm is not None
        stack = [c for c in dtcm.consumers if c.name == "boot_stack"]
        assert [(c.size, c.kind) for c in stack] == [(16_384, ConsumerKind.STACK)]

        ctx3 = _make_ctx(tmp_path, {"target": {"board": "apollo3p_evb"}})
        PlanMemoryStage().run(ctx3)
        assert ctx3.memory_plan is not None
        sram = ctx3.memory_plan.region("SRAM")
        assert sram is not None
        stack3 = [c for c in sram.consumers if c.name == "boot_stack"]
        assert [c.size for c in stack3] == [4_096]

    def test_aot_extraction_failure_no_longer_fabricates_a_tflm_plan(
        self, tmp_path
    ):
        """#133 Phase 3 D5: a failed AOT extraction used to fall into the
        TFLM synthesiser, booking a tensor_arena and model_flatbuffer
        that do not exist in an AOT binary."""
        ctx = _make_ctx(tmp_path, {"engine": {"type": "helia-aot"}})
        assert (
            ctx.engine_artifacts is None
            or ctx.engine_artifacts.memory_plan is None
        )
        PlanMemoryStage().run(ctx)
        assert ctx.memory_plan is not None
        names = {
            c.name for r in ctx.memory_plan.regions for c in r.consumers
        }
        assert "tensor_arena" not in names
        assert "model_flatbuffer" not in names
        # hpx-owned consumers still apply (they're engine-independent),
        # with the AOT record size:
        sram = ctx.memory_plan.region("SRAM")
        assert sram is not None
        records = [c for c in sram.consumers if c.name == "pmu_layer_records"]
        assert [c.size for c in records] == [4096 * 20]

    def test_ap3_bss_consumers_route_to_main_sram_not_dtcm(self, tmp_path):
        """#179 review B-1: AP3's gcc script sends .bss to RWMEM (main
        SRAM) — TCM is only 64 KB. Booking records/USB into DTCM refused
        VALID builds with a spurious 'shrink your arena' PlatformError."""
        ctx = _make_ctx(
            tmp_path,
            {
                "model": {"path": str(tmp_path / "model.tflite"), "arena_size": 32768},
                "target": {"board": "apollo3p_evb", "transport": "usb_cdc"},
            },
        )
        # arena at 32 KB in 64 KB TCM + records + usb would have "overflowed"
        # DTCM under the inverted routing; it must pass now.
        PlanMemoryStage().run(ctx)
        assert ctx.memory_plan is not None
        dtcm = ctx.memory_plan.region("DTCM")
        sram = ctx.memory_plan.region("SRAM")
        assert dtcm is not None and sram is not None
        dtcm_names = {c.name for c in dtcm.consumers}
        sram_names = {c.name for c in sram.consumers}
        assert "pmu_layer_records" in sram_names
        assert "usb_buffers" in sram_names
        assert "pmu_layer_records" not in dtcm_names
        assert "usb_buffers" not in dtcm_names

    def test_usb_buffers_booked_on_usb_cdc_transport(self, tmp_path):
        """#179 review m1: the USB branch was untested (a mutation of the
        size constant survived)."""
        ctx = _make_ctx(
            tmp_path, {"target": {"transport": "usb_cdc"}}
        )
        PlanMemoryStage().run(ctx)
        assert ctx.memory_plan is not None
        usb = [
            c
            for r in ctx.memory_plan.regions
            for c in r.consumers
            if c.name == "usb_buffers"
        ]
        assert [c.size for c in usb] == [4096 + 1024]
        # and no rtt_buffers on a USB run:
        names = {c.name for r in ctx.memory_plan.regions for c in r.consumers}
        assert "rtt_buffers" not in names

    def test_engine_supplied_records_entry_is_not_duplicated(self, tmp_path):
        """#179 review m1: the idempotence-by-name guard was untested (its
        removal survived the suite)."""
        from helia_profiler.results import (
            MemoryConsumer,
            MemoryPlan,
            MemoryRegionUsage,
        )
        from helia_profiler.stages.plan_memory import _add_hpx_owned_consumers

        engine_plan = MemoryPlan(
            engine=EngineType.HELIA_AOT,
            regions=(
                MemoryRegionUsage(
                    region=MemoryRegion.SRAM,
                    capacity=0,
                    used=1234,
                    consumers=(
                        MemoryConsumer(
                            name="pmu_layer_records", size=1234, kind=ConsumerKind.OTHER
                        ),
                    ),
                ),
            ),
        )
        ctx = _make_ctx(tmp_path, {"engine": {"type": "helia-aot"}})
        merged = _add_hpx_owned_consumers(engine_plan, ctx)
        records = [
            c
            for r in merged.regions
            for c in r.consumers
            if c.name == "pmu_layer_records"
        ]
        assert [c.size for c in records] == [1234]  # engine's entry kept, once
