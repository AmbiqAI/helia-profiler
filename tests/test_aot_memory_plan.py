"""Tests for heliaAOT-specific memory plan extraction.

These tests build ``ConversionResult`` view stand-ins, so heliaAOT itself
need not be importable.
"""

from __future__ import annotations

import pytest

from helia_profiler.engines.helia_aot.manifest import (
    _AOT_MEMORY_TO_PLACEMENT,
    _extract_arena_regions,
    _extract_memory_plan,
)
from helia_profiler.errors import EngineError
from helia_profiler.placement import ArenaRole, Placement
from helia_profiler.results import ConsumerKind
from tests.aot_views import PlacementView, RegionView, Result


def _regions(*regions: RegionView) -> Result:
    return Result(regions=regions)


def _region(
    region_id: int,
    memory: str,
    role: str = "scratch",
    size: int = 1024,
    *,
    source_memory: str | None = None,
    **fields: object,
) -> RegionView:
    return RegionView(
        region_id,
        memory,
        source_memory or memory,
        role,
        size=size,
        **fields,  # ty: ignore[invalid-argument-type]
    )


class TestExtractMemoryPlan:
    def test_no_regions_returns_none(self):
        assert _extract_memory_plan(Result()) is None

    def test_only_empty_regions_returns_none(self):
        assert _extract_memory_plan(_regions(_region(0, "DTCM", size=0))) is None

    def test_internal_mode_uses_the_emitted_region_symbols(self):
        plan = _extract_memory_plan(
            _regions(
                _region(0, "DTCM", symbol="hpx_arena_dtcm_buffer"),
                _region(1, "MRAM", "constant", symbol="hpx_arena_const_mram__blob"),
            )
        )
        assert plan is not None
        assert [(c.name, c.kind, c.symbol) for r in plan.regions for c in r.consumers] == [
            ("mram_constant_arena_1", ConsumerKind.WEIGHTS, "hpx_arena_const_mram__blob"),
            ("dtcm_scratch_arena_0", ConsumerKind.ARENA, "hpx_arena_dtcm_buffer"),
        ]

    def test_external_mode_names_hpx_storage_for_writable_regions(self):
        plan = _extract_memory_plan(
            _regions(
                _region(0, "DTCM", symbol="kws_arena_dtcm_buffer"),
                _region(2, "SRAM", "persistent"),
                _region(3, "PSRAM", "constant", sidecar_file="kws_arena_3.bin"),
            ),
            allocate_arenas=False,
        )
        assert plan is not None
        assert {c.name: c.symbol for r in plan.regions for c in r.consumers} == {
            "dtcm_scratch_arena_0": "hpx_arena_buf_storage_0",
            "sram_persistent_arena_2": "hpx_arena_buf_storage_2",
            "psram_constant_arena_3": None,
        }

    def test_staged_constant_books_its_source_copy(self):
        plan = _extract_memory_plan(
            _regions(
                _region(
                    1,
                    "DTCM",
                    "constant",
                    size=512,
                    source_memory="MRAM",
                    symbol="hpx_arena_const_dtcm_buffer",
                    source_symbol="hpx_arena_const_dtcm__blob",
                )
            )
        )
        assert plan is not None
        dtcm, mram = plan.region("DTCM"), plan.region("MRAM")
        assert dtcm is not None and mram is not None
        assert [(c.name, c.symbol) for c in dtcm.consumers] == [
            ("dtcm_constant_arena_1", "hpx_arena_const_dtcm_buffer")
        ]
        assert [(c.name, c.kind, c.symbol) for c in mram.consumers] == [
            ("mram_constant_source_1", ConsumerKind.WEIGHTS, "hpx_arena_const_dtcm__blob")
        ]
        assert plan.model_weight_bytes == 512

    def test_capacity_is_left_to_plan_memory(self):
        plan = _extract_memory_plan(_regions(_region(0, "SRAM", size=10_000_000)))
        assert plan is not None
        assert [r.capacity for r in plan.regions] == [0]

    def test_regions_prevent_tensor_double_counting(self):
        result = Result(
            regions=(
                _region(0, "DTCM", size=16_492),
                _region(1, "DTCM", "constant", size=28_976, source_memory="MRAM"),
            ),
            placements={f"tensor_{i}": PlacementView(0, 0, 1_777, "DTCM") for i in range(58)},
        )

        plan = _extract_memory_plan(result)

        assert plan is not None
        dtcm = plan.region("DTCM")
        assert dtcm is not None
        assert dtcm.used == 45_468
        assert not dtcm.overflow
        assert {c.name: c.size for c in dtcm.consumers} == {
            "dtcm_scratch_arena_0": 16_492,
            "dtcm_constant_arena_1": 28_976,
        }

        mram = plan.region("MRAM")
        assert mram is not None
        assert mram.used == 28_976
        assert plan.model_weight_bytes == 28_976


class TestExtractArenaRegions:
    """Verify that AOT physical memory names are normalised to logical
    placement names (tcm/sram/mram/psram) consumed by firmware templates."""

    def test_dtcm_normalises_to_tcm(self):
        regions = _extract_arena_regions(_regions(_region(0, "DTCM", size=20960)), "hpx")
        assert len(regions) == 1
        assert regions[0].memory == "dtcm"
        assert regions[0].name == "hpx_arena_dtcm"
        assert regions[0].size == 20960
        assert regions[0].placement is Placement.TCM
        assert regions[0].role is ArenaRole.SCRATCH

    def test_itcm_normalises_to_tcm(self):
        regions = _extract_arena_regions(_regions(_region(0, "ITCM")), "hpx")
        assert regions[0].placement is Placement.TCM

    def test_sram_stays_sram(self):
        regions = _extract_arena_regions(_regions(_region(0, "SRAM")), "hpx")
        assert regions[0].placement is Placement.SRAM

    def test_psram_stays_psram(self):
        region = _region(1, "PSRAM", "constant", alignment=64, sidecar_file="hpx_arena_1.bin")
        regions = _extract_arena_regions(_regions(region), "hpx")
        assert regions[0].placement is Placement.PSRAM
        assert regions[0].role is ArenaRole.CONSTANT
        assert regions[0].alignment == 64
        assert regions[0].blob_filename == "hpx_arena_1.bin"

    def test_mram_stays_mram(self):
        regions = _extract_arena_regions(_regions(_region(0, "MRAM", "constant")), "hpx")
        assert regions[0].placement is Placement.MRAM
        assert regions[0].blob_filename is None

    @pytest.mark.parametrize("memory,role", [("HBM", "scratch"), ("SRAM", "unknown")])
    def test_unsupported_trailing_arena_is_refused(self, memory, role):
        result = _regions(_region(0, "DTCM", size=16), _region(1, memory, role))
        with pytest.raises(EngineError, match="unsupported"):
            _extract_arena_regions(result, "hpx")

    def test_trailing_dram_arena_is_preserved(self):
        result = _regions(_region(0, "DTCM", size=16), _region(1, "DRAM"))
        regions = _extract_arena_regions(result, "hpx")
        assert [r.region_id for r in regions] == [0, 1]
        assert regions[1].memory == "dram"
        assert regions[1].placement is Placement.SRAM

    def test_no_regions_returns_empty(self):
        assert _extract_arena_regions(Result(), "hpx") == []

    def test_all_known_physical_names_mapped(self):
        for phys, logical in _AOT_MEMORY_TO_PLACEMENT.items():
            assert isinstance(logical, Placement), (
                f"physical '{phys}' maps to '{logical!r}' which is not a Placement member"
            )


@pytest.mark.parametrize("runtime,source", [("HBM", "HBM"), ("SRAM", "HBM")])
def test_unknown_mapping_invalidates_whole_aot_plan(runtime, source):
    result = _regions(
        _region(0, "SRAM"),
        _region(1, runtime, "constant", size=128, source_memory=source),
    )
    assert _extract_memory_plan(result) is None
