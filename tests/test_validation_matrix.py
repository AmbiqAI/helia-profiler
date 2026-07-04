"""Unit tests for the validation matrix (no hardware required)."""

from __future__ import annotations

import pytest

from helia_profiler.config import Toolchain, Transport
from helia_profiler.engines import EngineType
from helia_profiler.placement import ModelLocation
from helia_profiler.validation import (
    BOARDS,
    ENGINES,
    MODELS,
    CaseSpec,
    build_matrix,
    case_validity,
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
        assert BOARDS["apollo510_evb"].has_psram is True

    def test_ap3_and_ap4_blue_registered(self):
        assert "apollo3p_evb" in BOARDS
        assert "apollo4p_blue_kxr_evb" in BOARDS
        assert Transport.USB_CDC not in BOARDS["apollo3p_evb"].transports
        assert Transport.USB_CDC in BOARDS["apollo4p_blue_kxr_evb"].transports

    def test_engines_are_rt_and_aot(self):
        assert set(ENGINES) == {EngineType.HELIA_RT, EngineType.HELIA_AOT}


class TestCaseValidity:
    def _case(self, **overrides) -> CaseSpec:
        kwargs = dict(
            model=MODELS["kws"],
            engine=EngineType.HELIA_RT,
            power=False,
            board=BOARDS["apollo510_evb"],
        )
        kwargs.update(overrides)
        return CaseSpec(**kwargs)

    def test_psram_with_swo_gives_reason(self):
        case = self._case(memory=ModelLocation.PSRAM, transport=Transport.SWO)
        assert case_validity(case) == "psram weights require the rtt transport"

    def test_psram_with_rtt_is_valid(self):
        case = self._case(memory=ModelLocation.PSRAM, transport=Transport.RTT)
        assert case_validity(case) is None

    def test_ordinary_case_is_valid(self):
        assert case_validity(self._case()) is None


class TestBuildMatrix:
    def test_full_matrix_default(self):
        cases = build_matrix()
        # Power is intentionally off by default for PR reliability validation:
        # AP3: 4 models × 2 engines × 3 toolchains × 3 transports × 5 memories = 360
        # AP4/AP5: each 4 × 2 × 3 × 4 transports × 5 memories = 480
        assert len(cases) == 1320

    def test_power_off_halves_matrix(self):
        assert len(build_matrix(power="off")) == 1320

    def test_power_on_halves_matrix(self):
        assert len(build_matrix(power="on")) == 1320

    def test_power_both_doubles_matrix(self):
        assert len(build_matrix(power="both")) == 2640

    def test_repeat_multiplies_matrix(self):
        assert len(build_matrix(power="off", repeat=3)) == 3960

    def test_model_filter(self):
        cases = build_matrix(models=["kws"], power="off")
        assert len(cases) == 330
        assert {c.model.id for c in cases} == {"kws"}

    def test_engine_filter(self):
        cases = build_matrix(engines=["helia-aot"], power="off")
        assert len(cases) == 660
        assert all(c.engine is EngineType.HELIA_AOT for c in cases)

    def test_axis_filters_can_select_one_board_case_with_two_passes(self):
        cases = build_matrix(
            models=["kws"],
            engines=["helia-rt"],
            power="off",
            boards=["apollo3p_evb"],
            toolchains=["gcc"],
            transports=["rtt"],
            memories=["auto"],
            jlink_serials={"apollo3p_evb": "1160000174"},
            repeat=2,
        )

        assert len(cases) == 2
        assert {c.toolchain for c in cases} == {Toolchain.ARM_NONE_EABI_GCC}
        assert {c.transport for c in cases} == {Transport.RTT}
        assert {c.memory for c in cases} == {ModelLocation.AUTO}
        assert {c.jlink_serial for c in cases} == {"1160000174"}

    def test_unknown_model_raises(self):
        with pytest.raises(ValueError, match="Unknown model"):
            build_matrix(models=["nope"])

    def test_unknown_engine_raises(self):
        with pytest.raises(ValueError, match="Unknown engine"):
            build_matrix(engines=["tflite"])

    def test_unknown_board_raises(self):
        with pytest.raises(ValueError, match="Unknown board"):
            build_matrix(boards=["not_a_board"])

    def test_invalid_transport_for_board_raises(self):
        with pytest.raises(ValueError, match="No requested transports"):
            build_matrix(boards=["apollo3p_evb"], transports=["usb_cdc"])

    def test_invalid_memory_raises(self):
        with pytest.raises(ValueError, match="Unknown memory"):
            build_matrix(memories=["itcm"])

    def test_invalid_power_raises(self):
        with pytest.raises(ValueError, match="power must be"):
            build_matrix(power="maybe")

    def test_invalid_repeat_raises(self):
        with pytest.raises(ValueError, match="repeat must be"):
            build_matrix(repeat=0)

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
        assert off.case_id == "apollo510_evb-kws-rt-arm-none-eabi-gcc-rtt-auto"
        assert on.case_id == "apollo510_evb-kws-rt-arm-none-eabi-gcc-rtt-auto-power"

    def test_case_id_encodes_repeat_attempt_when_stressing(self):
        repeated = CaseSpec(
            model=MODELS["kws"],
            engine=EngineType.HELIA_RT,
            power=False,
            board=BOARDS["apollo510_evb"],
            attempt=2,
            repeat_total=3,
        )
        assert repeated.case_id == "apollo510_evb-kws-rt-arm-none-eabi-gcc-rtt-auto-run02"

    def test_deterministic_order(self):
        a = build_matrix()
        b = build_matrix()
        assert [c.case_id for c in a] == [c.case_id for c in b]
