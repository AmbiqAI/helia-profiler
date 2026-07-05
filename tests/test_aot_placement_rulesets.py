"""Tests for heliaAOT per-kind tensor placement rulesets.

The AOT greedy planner splits the model into three AIR tensor kinds —
``constant`` / ``persistent`` / ``scratch``.  ``_resolve_aot_tensor_rulesets``
maps compatibility ``model_location`` presets onto wildcard attribute rulesets
that pin each kind to a concrete memory. User-provided
``engine.config.aot_args.memory.tensors`` rules remain the precise AOT control.
"""

from __future__ import annotations

from pathlib import Path

from helia_profiler.config import load_config
from helia_profiler.engines.helia_aot.compile import (
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


class TestRunAotCompilerUsesConfigRegistry:
    """``_run_aot_compiler`` must resolve the SoC via ``config.platform_registry``.

    A custom board registered only in the profiler config (not the built-in
    platform registry) previously resolved to the wrong SoC or raised
    ``ValueError`` because ``get_soc_for_board`` was called without
    ``registry=config.platform_registry``.
    """

    def _install_fake_helia_aot(self, monkeypatch):
        import sys
        import types

        class _FakeSection:
            def __init__(self):
                self.path = None
                self.name = None
                self.prefix = None
                self.type = None

        class _FakeConvertArgs:
            def __init__(self, **_kwargs):
                self.model = _FakeSection()
                self.module = _FakeSection()
                self.platform = _FakeSection()
                self.force = False

        class _FakeCodegenCtx:
            pass

        class _FakeAotConverter:
            def __init__(self, config):
                self._config = config

            def convert(self):
                module_dir = Path(self._config.module.path) / self._config.module.name
                (module_dir / "src").mkdir(parents=True, exist_ok=True)
                (module_dir / "includes-api").mkdir(parents=True, exist_ok=True)
                return _FakeCodegenCtx()

        fake_defines = types.ModuleType("helia_aot.cli.defines")
        fake_defines.ConvertArgs = _FakeConvertArgs
        fake_cli = types.ModuleType("helia_aot.cli")
        fake_cli.defines = fake_defines
        fake_converter_mod = types.ModuleType("helia_aot.converter")
        fake_converter_mod.AotConverter = _FakeAotConverter
        fake_top_defines = types.ModuleType("helia_aot.defines")

        class _ModuleTypeEnum:
            nsx = "nsx"

        fake_top_defines.ModuleType = _ModuleTypeEnum
        fake_helia_aot = types.ModuleType("helia_aot")
        fake_helia_aot.cli = fake_cli
        fake_helia_aot.converter = fake_converter_mod
        fake_helia_aot.defines = fake_top_defines

        monkeypatch.setitem(sys.modules, "helia_aot", fake_helia_aot)
        monkeypatch.setitem(sys.modules, "helia_aot.cli", fake_cli)
        monkeypatch.setitem(sys.modules, "helia_aot.cli.defines", fake_defines)
        monkeypatch.setitem(sys.modules, "helia_aot.converter", fake_converter_mod)
        monkeypatch.setitem(sys.modules, "helia_aot.defines", fake_top_defines)

    def test_custom_board_resolves_soc_via_config_registry(self, tmp_path, monkeypatch):
        from helia_profiler.engines.helia_aot import compile as compile_mod

        self._install_fake_helia_aot(monkeypatch)

        cli = {
            "model": {"path": "m.tflite", "model_location": "tcm"},
            "engine": {"type": "helia-aot"},
            "target": {
                "board": "apollo510_custom_board",
                "custom_socs": {
                    "apollo510_custom": {
                        "based_on": "apollo510",
                        "jlink_device": "AP510-CUSTOM",
                    }
                },
                "custom_boards": {
                    "apollo510_custom_board": {
                        "soc": "apollo510_custom",
                        "channel": "dev",
                        "starter_profile_board": "apollo510_evb",
                    }
                },
            },
        }
        config = load_config(None, cli)

        seen: dict[str, object] = {}
        real_get_soc_for_board = compile_mod.get_soc_for_board

        def spy_get_soc_for_board(board, *, registry=None):
            seen["board"] = board
            seen["registry"] = registry
            return real_get_soc_for_board(board, registry=registry)

        monkeypatch.setattr(compile_mod, "get_soc_for_board", spy_get_soc_for_board)

        codegen_ctx = compile_mod._run_aot_compiler(
            config,
            output_dir=tmp_path / "out",
            module_name="profiler_module",
            prefix="hpx",
            aot_platform="apollo510_evb",
        )

        assert codegen_ctx is not None
        # The custom board only exists in config.platform_registry; without
        # threading it through, get_soc_for_board would raise ValueError.
        assert seen["board"] == "apollo510_custom_board"
        assert seen["registry"] is config.platform_registry
        soc = config.platform_registry.socs["apollo510_custom"]
        assert soc.jlink_device == "AP510-CUSTOM"
