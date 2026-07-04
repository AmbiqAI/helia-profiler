"""Tests for the build-time arena placement guard (stage 4b)."""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.config import load_config
from helia_profiler.errors import BuildError
from helia_profiler.pipeline import PipelineContext
from helia_profiler.placement import Placement
from helia_profiler.platform import (
    MemoryRange,
    get_soc_for_board,
    soc_placement_ranges,
)
from helia_profiler.stages import verify_placement
from helia_profiler.stages.verify_placement import VerifyPlacementStage


# ---------------------------------------------------------------------------
# MemoryRange / soc_placement_ranges
# ---------------------------------------------------------------------------


class TestMemoryRange:
    def test_contains_half_open(self):
        r = MemoryRange(0x10060000, 0x100000)
        assert r.end == 0x10160000
        assert r.contains(0x10060000)  # inclusive start
        assert r.contains(0x1006D1F0)
        assert not r.contains(0x10160000)  # exclusive end
        assert not r.contains(0x10000000)


class TestSocPlacementRanges:
    def test_ap4_ranges(self):
        ranges = soc_placement_ranges(get_soc_for_board("apollo4p_blue_kbr_evb"))
        assert ranges[Placement.TCM] == MemoryRange(0x10000000, 0x60000)
        assert ranges[Placement.SRAM] == MemoryRange(0x10060000, 0x100000)
        # The known-good fixed arena is in SRAM; the mislocated one was in TCM.
        assert ranges[Placement.SRAM].contains(0x1006D1F0)
        assert ranges[Placement.TCM].contains(0x10025270)

    def test_ap5_ranges(self):
        ranges = soc_placement_ranges(get_soc_for_board("apollo510_evb"))
        assert ranges[Placement.TCM].start == 0x20000000
        assert ranges[Placement.SRAM].start == 0x20080000
        # AP5 arena=sram input pointer observed at 0x2009DBF0.
        assert ranges[Placement.SRAM].contains(0x2009DBF0)

    def test_ap3_ranges(self):
        ranges = soc_placement_ranges(get_soc_for_board("apollo3p_evb"))
        # apollo3p has a real 64 KB low-latency TCM at 0x10000000 (arena-
        # eligible via NSX_MEM_FAST_BSS's dedicated .tcm_bss section,
        # nsx-ambiq-sdk#29), distinct from the 700 KB main SRAM "RWMEM" at
        # 0x10011000. "MRAM" is read-only NOR flash.
        assert ranges[Placement.TCM] == MemoryRange(0x10000000, 64 * 1024)
        assert ranges[Placement.SRAM] == MemoryRange(0x10011000, 700 * 1024)
        assert ranges[Placement.SRAM].contains(0x10011000)
        # A small KWS-sized arena lands near the base of RWMEM.
        assert ranges[Placement.SRAM].contains(0x10011010)


# ---------------------------------------------------------------------------
# VerifyPlacementStage
# ---------------------------------------------------------------------------


def _ctx(
    tmp_path: Path,
    *,
    board: str = "apollo4p_blue_kbr_evb",
    engine: str = "helia-rt",
    toolchain: str = "armclang",
    arena_region: Placement | None = Placement.SRAM,
) -> PipelineContext:
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x1c\x00\x00\x00TFL3" + b"\x00" * 100)
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": engine},
            "target": {"board": board, "toolchain": toolchain},
            "work_dir": str(tmp_path / "work"),
        },
    )
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    ctx = PipelineContext(config=config, work_dir=work_dir)
    ctx.soc = get_soc_for_board(board)
    binary = tmp_path / "hpx_profiler.elf"
    binary.write_bytes(b"\x7fELF")
    ctx.binary_path = binary
    ctx.build_dir = tmp_path
    ctx.arena_region = arena_region
    return ctx


class TestShouldSkip:
    def test_skip_without_binary(self, tmp_path):
        ctx = _ctx(tmp_path)
        ctx.binary_path = None
        assert VerifyPlacementStage().should_skip(ctx) is True

    def test_skip_psram_arena(self, tmp_path):
        ctx = _ctx(tmp_path, arena_region=Placement.PSRAM)
        assert VerifyPlacementStage().should_skip(ctx) is True

    def test_skip_aot_engine(self, tmp_path):
        ctx = _ctx(tmp_path, engine="helia-aot")
        assert VerifyPlacementStage().should_skip(ctx) is True

    def test_no_skip_rt_sram(self, tmp_path):
        ctx = _ctx(tmp_path)
        assert VerifyPlacementStage().should_skip(ctx) is False


class TestRun:
    def test_correct_placement_passes(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path, arena_region=Placement.SRAM)
        # Arena correctly in SRAM (0x10060000-0x10160000).
        monkeypatch.setattr(
            verify_placement, "symbol_address", lambda *a, **k: (0x1006D1F0, "d")
        )
        VerifyPlacementStage().run(ctx)  # no raise

    def test_mislocated_arena_raises(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path, arena_region=Placement.SRAM)
        # Arena fell into TCM (the armclang scatter-gap bug): 0x10025270.
        monkeypatch.setattr(
            verify_placement, "symbol_address", lambda *a, **k: (0x10025270, "b")
        )
        with pytest.raises(BuildError) as exc:
            VerifyPlacementStage().run(ctx)
        msg = str(exc.value)
        assert "TCM" in msg
        assert "SRAM" in msg

    def test_unresolved_symbol_is_best_effort(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path, arena_region=Placement.SRAM)
        monkeypatch.setattr(
            verify_placement, "symbol_address", lambda *a, **k: None
        )
        VerifyPlacementStage().run(ctx)  # no raise

    def test_unmodelled_soc_is_best_effort(self, tmp_path, monkeypatch):
        ctx = _ctx(tmp_path, board="apollo3p_evb", arena_region=Placement.SRAM)
        # Simulate a SoC family whose memory model is not yet characterised:
        # even a wildly wrong address must not raise when ranges are unknown.
        monkeypatch.setattr(
            verify_placement, "soc_placement_ranges", lambda soc: {}
        )
        monkeypatch.setattr(
            verify_placement, "symbol_address", lambda *a, **k: (0x00001234, "b")
        )
        VerifyPlacementStage().run(ctx)  # no raise
