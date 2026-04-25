"""Tests for heliaAOT-specific memory plan extraction.

These tests use a lightweight fake CodeGenContext that mimics the
``helia_aot.memory.defines.MemoryPlan`` surface without requiring
heliaAOT itself to be importable (which pulls in TVM / flatbuffers).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from helia_profiler.engines.helia_aot import _extract_memory_plan


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


class TestExtractMemoryPlan:
    def test_missing_plan_returns_none(self):
        assert _extract_memory_plan(_FakeCodegenCtx(None)) is None

    def test_empty_plan_returns_empty_regions(self):
        plan = _FakeAotPlan(arena_usages={}, tensor_allocs={})
        result = _extract_memory_plan(_FakeCodegenCtx(plan))
        assert result is not None
        assert result.engine == "helia_aot"
        assert result.regions == ()

    def test_arena_region_becomes_consumer(self):
        plan = _FakeAotPlan(
            arena_usages={"DTCM": _FakeArenaUsage(total_size=65_536, used=12_000)},
            tensor_allocs={},
        )
        result = _extract_memory_plan(_FakeCodegenCtx(plan))
        assert result is not None
        dtcm = result.region("DTCM")
        assert dtcm is not None
        assert dtcm.capacity == 65_536
        assert dtcm.used == 12_000
        names = [c.name for c in dtcm.consumers]
        kinds = [c.kind for c in dtcm.consumers]
        assert "dtcm_arena" in names
        assert "arena" in kinds
        assert not dtcm.overflow

    def test_weight_tensors_aggregated_per_region(self):
        plan = _FakeAotPlan(
            arena_usages={
                "MRAM": _FakeArenaUsage(total_size=0, used=0),
            },
            tensor_allocs={
                "w0": _FakeTensorAllocation(memory="MRAM", size=1000),
                "w1": _FakeTensorAllocation(memory="MRAM", size=2500),
                "w2": _FakeTensorAllocation(memory="PSRAM", size=4000),
            },
        )
        result = _extract_memory_plan(_FakeCodegenCtx(plan))
        assert result is not None

        mram = result.region("MRAM")
        assert mram is not None
        weight_consumers = [c for c in mram.consumers if c.kind == "weights"]
        assert len(weight_consumers) == 1
        assert weight_consumers[0].size == 3500
        # 2 tensors → consumer name records the count
        assert weight_consumers[0].name.startswith("2_")

        # PSRAM had no arena_usage entry; weights-only regions are only
        # surfaced via arena_usages.  Here PSRAM is not in arena_usages so
        # it will not appear in the plan.
        assert result.region("PSRAM") is None

    def test_total_weight_bytes_and_overflow(self):
        plan = _FakeAotPlan(
            arena_usages={
                "DTCM": _FakeArenaUsage(total_size=8_192, used=16_384),
            },
            tensor_allocs={
                "w0": _FakeTensorAllocation(memory="DTCM", size=10),
            },
        )
        result = _extract_memory_plan(_FakeCodegenCtx(plan))
        assert result is not None
        assert result.model_weight_bytes == 10
        # DTCM is oversubscribed (used > capacity).
        assert result.has_overflow is True
