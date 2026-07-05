"""Tests for heliaAOT-specific memory plan extraction.

These tests use a lightweight fake CodeGenContext that mimics the
``helia_aot.memory.defines.MemoryPlan`` surface without requiring
heliaAOT itself to be importable (which pulls in TVM / flatbuffers).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from helia_profiler.engines.helia_aot.manifest import (
    _AOT_MEMORY_TO_PLACEMENT,
    _extract_arena_regions,
    _extract_memory_plan,
)
from helia_profiler.placement import ArenaRole, Placement


@dataclass
class _FakeArenaUsage:
    total_size: int
    used: int


@dataclass
class _FakeTensorAllocation:
    memory: str
    size: int


@dataclass
class _FakeAotPlan:
    arena_usages: dict[str, _FakeArenaUsage]
    tensor_allocs: dict[str, _FakeTensorAllocation]


class _FakeCodegenCtx:
    def __init__(self, plan: _FakeAotPlan | None):
        if plan is not None:
            self.memory_plan = plan


# ---------------------------------------------------------------------------
# _extract_arena_regions — placement normalisation
# ---------------------------------------------------------------------------


@dataclass
class _FakeArena:
    region_id: int
    memory: str
    size: int
    alignment: int
    role: str
    source_memory: str | None = None


@dataclass
class _FakeRenderPlan:
    scratch_arenas: list[_FakeArena]
    persistent_arenas: list[_FakeArena]
    constant_arenas: list[_FakeArena]


class _FakeCodegenCtxWithPlan:
    def __init__(self, render_plan: _FakeRenderPlan | None):
        self.render_plan = render_plan


class _FakeCodegenCtxWithMemoryAndRenderPlan:
    def __init__(self, memory_plan: _FakeAotPlan, render_plan: _FakeRenderPlan):
        self.memory_plan = memory_plan
        self.render_plan = render_plan


class TestExtractMemoryPlan:
    def test_missing_plan_returns_none(self):
        assert _extract_memory_plan(_FakeCodegenCtx(None)) is None

    def test_memory_plan_without_render_plan_returns_none(self):
        plan = _FakeAotPlan(
            arena_usages={"DTCM": _FakeArenaUsage(total_size=65_536, used=12_000)},
            tensor_allocs={"x": _FakeTensorAllocation(memory="DTCM", size=12_000)},
        )

        assert _extract_memory_plan(_FakeCodegenCtx(plan)) is None


class TestExtractArenaRegions:
    """Verify that AOT physical memory names are normalised to logical
    placement names (tcm/sram/mram/psram) consumed by firmware templates."""

    def test_dtcm_normalises_to_tcm(self):
        plan = _FakeRenderPlan(
            scratch_arenas=[_FakeArena(0, "DTCM", 20960, 16, "scratch")],
            persistent_arenas=[],
            constant_arenas=[],
        )
        regions = _extract_arena_regions(_FakeCodegenCtxWithPlan(plan), "hpx")
        assert len(regions) == 1
        assert regions[0].memory == "dtcm"
        assert regions[0].placement is Placement.TCM
        assert regions[0].role is ArenaRole.SCRATCH

    def test_itcm_normalises_to_tcm(self):
        plan = _FakeRenderPlan(
            scratch_arenas=[_FakeArena(0, "ITCM", 4096, 16, "scratch")],
            persistent_arenas=[],
            constant_arenas=[],
        )
        regions = _extract_arena_regions(_FakeCodegenCtxWithPlan(plan), "hpx")
        assert regions[0].placement is Placement.TCM

    def test_sram_stays_sram(self):
        plan = _FakeRenderPlan(
            scratch_arenas=[_FakeArena(0, "SRAM", 65536, 16, "scratch")],
            persistent_arenas=[],
            constant_arenas=[],
        )
        regions = _extract_arena_regions(_FakeCodegenCtxWithPlan(plan), "hpx")
        assert regions[0].placement is Placement.SRAM

    def test_psram_stays_psram(self):
        plan = _FakeRenderPlan(
            scratch_arenas=[],
            persistent_arenas=[],
            constant_arenas=[_FakeArena(1, "PSRAM", 100000, 64, "constant")],
        )
        regions = _extract_arena_regions(_FakeCodegenCtxWithPlan(plan), "hpx")
        assert regions[0].placement is Placement.PSRAM
        assert regions[0].role is ArenaRole.CONSTANT

    def test_mram_stays_mram(self):
        plan = _FakeRenderPlan(
            scratch_arenas=[],
            persistent_arenas=[],
            constant_arenas=[_FakeArena(0, "MRAM", 50000, 16, "constant")],
        )
        regions = _extract_arena_regions(_FakeCodegenCtxWithPlan(plan), "hpx")
        assert regions[0].placement is Placement.MRAM

    def test_unknown_memory_skipped(self):
        """An unrecognised memory name is dropped (rather than silently
        mis-placing) — surfaces upstream as an arena-binding gap."""
        plan = _FakeRenderPlan(
            scratch_arenas=[_FakeArena(0, "HBM", 1024, 16, "scratch")],
            persistent_arenas=[],
            constant_arenas=[],
        )
        regions = _extract_arena_regions(_FakeCodegenCtxWithPlan(plan), "hpx")
        assert regions == []

    def test_missing_render_plan_returns_empty(self):
        ctx = _FakeCodegenCtxWithPlan(None)
        assert _extract_arena_regions(ctx, "hpx") == []

    def test_all_known_physical_names_mapped(self):
        """Every entry in _AOT_MEMORY_TO_PLACEMENT should map to a
        :class:`Placement` member recognised by the firmware templates."""
        for phys, logical in _AOT_MEMORY_TO_PLACEMENT.items():
            assert isinstance(logical, Placement), (
                f"physical '{phys}' maps to '{logical!r}' which is not a Placement member"
            )


class TestExtractMemoryPlanFromRenderPlan:
    def test_render_plan_arenas_prevent_tensor_double_counting(self):
        memory_plan = _FakeAotPlan(
            arena_usages={
                "DTCM": _FakeArenaUsage(total_size=65_536, used=16_492),
                "MRAM": _FakeArenaUsage(total_size=2_048_000, used=0),
            },
            tensor_allocs={
                f"tensor_{i}": _FakeTensorAllocation(memory="DTCM", size=1_777)
                for i in range(58)
            },
        )
        render_plan = _FakeRenderPlan(
            scratch_arenas=[_FakeArena(0, "DTCM", 16_492, 16, "scratch")],
            persistent_arenas=[],
            constant_arenas=[_FakeArena(1, "DTCM", 28_976, 16, "constant", "MRAM")],
        )

        result = _extract_memory_plan(
            _FakeCodegenCtxWithMemoryAndRenderPlan(memory_plan, render_plan)
        )

        assert result is not None
        dtcm = result.region("DTCM")
        assert dtcm is not None
        assert dtcm.used == 45_468
        assert not dtcm.overflow
        assert {c.name: c.size for c in dtcm.consumers} == {
            "dtcm_scratch_arena_0": 16_492,
            "dtcm_constant_arena_1": 28_976,
        }

        mram = result.region("MRAM")
        assert mram is not None
        assert mram.used == 28_976
        assert result.model_weight_bytes == 28_976
