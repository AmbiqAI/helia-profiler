"""Unit tests for the validation matrix (no hardware required)."""

from __future__ import annotations

import pytest

from helia_profiler.engines import EngineType
from helia_profiler.validation import (
    BOARDS,
    ENGINES,
    MODELS,
    CaseSpec,
    build_matrix,
)


class TestRegistry:
    def test_four_mlperf_tiny_models(self):
        assert set(MODELS) == {"kws", "vww", "ic", "ad"}

    def test_model_fixture_paths_relative(self):
        for m in MODELS.values():
            assert m.fixture_path.startswith("tests/fixtures/mlperf_tiny/")
            assert m.fixture_path.endswith(".tflite")

    def test_apollo510_registered(self):
        assert "apollo510_evb" in BOARDS
        assert BOARDS["apollo510_evb"].jlink_device == "AP510NFA-CBR"

    def test_engines_are_rt_and_aot(self):
        assert set(ENGINES) == {EngineType.HELIA_RT, EngineType.HELIA_AOT}


class TestBuildMatrix:
    def test_full_matrix_default(self):
        cases = build_matrix()
        # 4 models × 2 engines × 2 power × 1 board = 16 cases
        assert len(cases) == 16

    def test_power_off_halves_matrix(self):
        assert len(build_matrix(power="off")) == 8

    def test_power_on_halves_matrix(self):
        assert len(build_matrix(power="on")) == 8

    def test_model_filter(self):
        cases = build_matrix(models=["kws"], power="off")
        assert len(cases) == 2  # kws × 2 engines
        assert {c.model.id for c in cases} == {"kws"}

    def test_engine_filter(self):
        cases = build_matrix(engines=["helia-aot"], power="off")
        assert len(cases) == 4  # 4 models × 1 engine
        assert all(c.engine is EngineType.HELIA_AOT for c in cases)

    def test_unknown_model_raises(self):
        with pytest.raises(ValueError, match="Unknown model"):
            build_matrix(models=["nope"])

    def test_unknown_engine_raises(self):
        with pytest.raises(ValueError, match="Unknown engine"):
            build_matrix(engines=["tflite"])

    def test_unknown_board_raises(self):
        with pytest.raises(ValueError, match="Unknown board"):
            build_matrix(boards=["apollo4p_evb"])

    def test_invalid_power_raises(self):
        with pytest.raises(ValueError, match="power must be"):
            build_matrix(power="maybe")

    def test_case_id_is_stable_and_unique(self):
        cases = build_matrix()
        ids = [c.case_id for c in cases]
        assert len(ids) == len(set(ids)), "case_id collision"

    def test_case_id_encodes_power(self):
        off = CaseSpec(
            model=MODELS["kws"],
            engine=EngineType.HELIA_RT,
            power=False,
            board=BOARDS["apollo510_evb"],
        )
        on = CaseSpec(
            model=MODELS["kws"],
            engine=EngineType.HELIA_RT,
            power=True,
            board=BOARDS["apollo510_evb"],
        )
        assert off.case_id == "apollo510_evb-kws-rt"
        assert on.case_id == "apollo510_evb-kws-rt-power"

    def test_deterministic_order(self):
        a = build_matrix()
        b = build_matrix()
        assert [c.case_id for c in a] == [c.case_id for c in b]
