"""Tests for heliaAOT per-kind tensor placement rulesets.

The AOT greedy planner splits the model into three AIR tensor kinds —
``constant`` / ``persistent`` / ``scratch``.  ``_resolve_aot_tensor_rulesets``
maps compatibility ``model_location`` presets onto wildcard attribute rulesets
that pin each kind to a concrete memory. User-provided
``engine.config.aot_args.memory.tensors`` rules remain the precise AOT control.
"""

from __future__ import annotations

from helia_profiler.config import load_config
from helia_profiler.engines.helia_aot import (
    _EXPECTED_PRAGMA_SUFFIXES,
    _resolve_aot_placement_intent,
    _resolve_aot_tensor_rulesets,
)
from helia_profiler.placement import Placement
from helia_profiler.platform import get_soc_for_board


def _cfg(board: str, location: str, **engine_overrides):
    engine: dict = {"type": "helia-aot"}
    if engine_overrides:
        engine["config"] = engine_overrides
    cli = {
        "model": {"path": "m.tflite", "model_location": location},
        "engine": engine,
        "target": {"board": board},
    }
    return load_config(None, cli)


def _by_kind(rulesets):
    return {r["type"]: r["attributes"] for r in rulesets}


class TestPlacementIntent:
    def test_auto_returns_none(self):
        soc = get_soc_for_board("apollo4p_blue_kxr_evb")
        assert _resolve_aot_placement_intent(_cfg("apollo4p_blue_kxr_evb", "auto"), soc) == (
            Placement.TCM,
            Placement.MRAM,
        )

    def test_auto_with_aot_args_stays_planner_controlled(self):
        soc = get_soc_for_board("apollo4p_blue_kxr_evb")
        cfg = _cfg(
            "apollo4p_blue_kxr_evb",
            "auto",
            aot_args={"memory": {"tensors": [{"type": "scratch", "attributes": {"memory": "sram"}}]}},
        )
        assert _resolve_aot_placement_intent(cfg, soc) == (Placement.TCM, Placement.MRAM)


class TestRulesetsWithDtcm:
    board = "apollo4p_blue_kxr_evb"

    def test_auto_uses_profiler_default_rulesets(self):
        soc = get_soc_for_board(self.board)
        kinds = _by_kind(_resolve_aot_tensor_rulesets(_cfg(self.board, "auto"), soc))
        assert kinds["scratch"] == {"memory": "dtcm"}
        assert kinds["persistent"] == {"memory": "dtcm"}
        assert kinds["constant"] == {"memory": "mram"}

    def test_tcm_pins_all_three_to_dtcm(self):
        soc = get_soc_for_board(self.board)
        kinds = _by_kind(_resolve_aot_tensor_rulesets(_cfg(self.board, "tcm"), soc))
        assert kinds["scratch"] == {"memory": "dtcm"}
        assert kinds["persistent"] == {"memory": "dtcm"}
        # constants are read-only: cold in MRAM, staged into DTCM at runtime.
        assert kinds["constant"] == {"memory": "mram", "constant_destination_memory": "dtcm"}

    def test_sram_pins_all_three_to_sram(self):
        soc = get_soc_for_board(self.board)
        kinds = _by_kind(_resolve_aot_tensor_rulesets(_cfg(self.board, "sram"), soc))
        assert kinds["scratch"] == {"memory": "sram"}
        assert kinds["persistent"] == {"memory": "sram"}
        assert kinds["constant"] == {"memory": "mram", "constant_destination_memory": "sram"}

    def test_mram_keeps_constants_cold_arena_in_tcm(self):
        soc = get_soc_for_board(self.board)
        kinds = _by_kind(_resolve_aot_tensor_rulesets(_cfg(self.board, "mram"), soc))
        assert kinds["scratch"] == {"memory": "dtcm"}
        assert kinds["persistent"] == {"memory": "dtcm"}
        assert kinds["constant"] == {"memory": "mram"}  # cold XIP, no staging

    def test_psram_constants_xip_from_psram(self):
        soc = get_soc_for_board(self.board)
        kinds = _by_kind(_resolve_aot_tensor_rulesets(_cfg(self.board, "psram"), soc))
        assert kinds["scratch"] == {"memory": "sram"}
        assert kinds["persistent"] == {"memory": "sram"}
        assert kinds["constant"] == {"memory": "psram"}


class TestRulesetsWithDtcmAp3:
    """AP3P has a real 64 KB TCM (nsx-ambiq-sdk#29) — same shape as AP4/AP5."""

    board = "apollo3p_evb"

    def test_has_dtcm(self):
        soc = get_soc_for_board(self.board)
        assert soc.memory.dtcm_kb == 64

    def test_tcm_pins_all_three_to_dtcm(self):
        soc = get_soc_for_board(self.board)
        kinds = _by_kind(_resolve_aot_tensor_rulesets(_cfg(self.board, "tcm"), soc))
        assert kinds["scratch"] == {"memory": "dtcm"}
        assert kinds["persistent"] == {"memory": "dtcm"}
        assert kinds["constant"] == {"memory": "mram", "constant_destination_memory": "dtcm"}

    def test_mram_arena_prefers_dtcm(self):
        soc = get_soc_for_board(self.board)
        kinds = _by_kind(_resolve_aot_tensor_rulesets(_cfg(self.board, "mram"), soc))
        assert kinds["scratch"] == {"memory": "dtcm"}
        assert kinds["persistent"] == {"memory": "dtcm"}
        assert kinds["constant"] == {"memory": "mram"}  # cold XIP, no staging


class TestLegacyPresets:
    board = "apollo4p_blue_kxr_evb"

    def test_model_location_preset_sets_all_kinds(self):
        soc = get_soc_for_board(self.board)
        cfg = _cfg(self.board, "sram")
        kinds = _by_kind(_resolve_aot_tensor_rulesets(cfg, soc))
        assert kinds["scratch"] == {"memory": "sram"}
        assert kinds["persistent"] == {"memory": "sram"}
        assert kinds["constant"] == {"memory": "mram", "constant_destination_memory": "sram"}


def test_expected_pragma_suffixes_track_current_heliaaot_platform_header():
    assert "PUT_IN_DRAM" in _EXPECTED_PRAGMA_SUFFIXES
    assert "PUT_IN_DRAM_INIT" in _EXPECTED_PRAGMA_SUFFIXES
    assert "PUT_IN_ITCM_INIT" in _EXPECTED_PRAGMA_SUFFIXES
