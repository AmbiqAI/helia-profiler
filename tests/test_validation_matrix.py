"""Unit tests for the validation matrix (no hardware required)."""

from __future__ import annotations

import dataclasses

import pytest

from helia_profiler.config import Toolchain, Transport
from helia_profiler.engines import EngineType
from helia_profiler.validation import (
    BOARDS,
    ENGINES,
    MODELS,
    CaseSpec,
    ExecuTorchBackend,
    build_matrix,
    case_validity,
    load_model_file,
    models_from_paths,
)
from helia_profiler.validation.matrix import MemoryProfile


class TestRegistry:
    def test_four_mlperf_tiny_models(self):
        assert set(MODELS) == {"kws", "vww", "ic", "ad"}

    def test_model_fixture_paths_relative(self):
        for m in MODELS.values():
            assert m.fixture_path.startswith("tests/fixtures/mlperf_tiny/")
            assert m.fixture_path.endswith(".tflite")
            assert m.executorch is not None
            assert m.executorch.fixture_path.startswith("tests/fixtures/mlperf_tiny/")
            assert m.executorch.fixture_path.endswith(".pte")

    def test_apollo510_registered(self):
        assert "apollo510_evb" in BOARDS
        assert BOARDS["apollo510_evb"].jlink_device == "AP510NFA-CBR"
        assert BOARDS["apollo510_evb"].has_psram is True

    def test_apollo330_registered(self):
        assert "apollo330mP_evb" in BOARDS
        assert BOARDS["apollo330mP_evb"].jlink_device == "Apollo330P_510L"
        assert BOARDS["apollo330mP_evb"].has_psram is True

    def test_ap3_and_ap4_blue_registered(self):
        assert "apollo3p_evb" in BOARDS
        assert "apollo4p_blue_kxr_evb" in BOARDS
        assert Transport.USB_CDC not in BOARDS["apollo3p_evb"].transports
        assert Transport.USB_CDC in BOARDS["apollo4p_blue_kxr_evb"].transports

    def test_engines_include_tflm_cmsis_nn_baseline(self):
        assert set(ENGINES) == {
            EngineType.HELIA_RT,
            EngineType.HELIA_AOT,
            EngineType.TFLM,
            EngineType.EXECUTORCH,
        }

    def test_yaml_models_resolve_relative_paths_and_comparison_groups(self, tmp_path):
        model = tmp_path / "models" / "kws-pruned.tflite"
        model.parent.mkdir()
        model.write_bytes(b"model")
        registry = tmp_path / "variants.yml"
        registry.write_text(
            """
models:
  kws-pruned:
    path: models/kws-pruned.tflite
    name: KWS pruned
    comparison_group: kws
    arena_size: 65536
"""
        )

        loaded = load_model_file(registry)

        assert loaded["kws-pruned"].fixture_path == str(model.resolve())
        assert loaded["kws-pruned"].decision_group == "kws"
        assert loaded["kws-pruned"].arena_size == 65536

    def test_command_line_paths_share_the_requested_comparison_group(self, tmp_path):
        first = tmp_path / "KWS Base.tflite"
        second = tmp_path / "KWS Pruned.tflite"
        first.write_bytes(b"base")
        second.write_bytes(b"pruned")

        loaded = models_from_paths(
            [first, second],
            arena_size=98304,
            comparison_group="kws-variants",
        )

        assert set(loaded) == {"kws-base", "kws-pruned"}
        assert {model.decision_group for model in loaded.values()} == {"kws-variants"}
        cases = build_matrix(
            models=list(loaded),
            model_registry={**MODELS, **loaded},
            engines=["helia-rt"],
            boards=["apollo510_evb"],
            toolchains=["arm-none-eabi-gcc"],
            transports=["rtt"],
            memories=["auto"],
        )
        assert {case.model.id for case in cases} == {"kws-base", "kws-pruned"}


class TestCaseValidity:
    def _case(self, **overrides: object) -> CaseSpec:
        # replace() on the frozen, __post_init__-free CaseSpec is exactly
        # CaseSpec(**{**defaults, **overrides}) — but each field is typed.
        base = CaseSpec(
            model=MODELS["kws"],
            engine=EngineType.HELIA_RT,
            power=False,
            board=BOARDS["apollo510_evb"],
        )
        return dataclasses.replace(base, **overrides)

    def test_psram_with_swo_gives_reason(self):
        case = self._case(memory=MemoryProfile.PSRAM, transport=Transport.SWO)
        assert case_validity(case) == "psram weights require the rtt transport"

    def test_psram_with_rtt_is_valid(self):
        case = self._case(memory=MemoryProfile.PSRAM, transport=Transport.RTT)
        assert case_validity(case) is None

    def test_ordinary_case_is_valid(self):
        assert case_validity(self._case()) is None

    def test_executorch_armclang_gives_reason(self):
        case = self._case(engine=EngineType.EXECUTORCH, toolchain=Toolchain.ARMCLANG)
        assert case_validity(case) == "ExecuTorch validation does not yet cover armclang"

    def test_executorch_atfe_is_valid(self):
        case = self._case(engine=EngineType.EXECUTORCH, toolchain=Toolchain.ATFE)
        assert case_validity(case) is None


class TestBuildMatrix:
    def test_full_matrix_default(self):
        cases = build_matrix()
        # Power is intentionally off by default for PR reliability validation:
        # Existing engines contribute 2700 cases. ExecuTorch adds 256 cases
        # on each Cortex-M55 board: 4 models × 2 providers × {gcc, atfe} ×
        # 4 × 4 (armclang is excluded until validated separately).
        # PSRAM is omitted because the ExecuTorch adapter does not support it.
        assert len(cases) == 3212

    def test_power_on_keeps_executorch_unpowered(self):
        # ExecuTorch remains unpowered until its dedicated firmware implements
        # the GPIO READY/GO/gate protocol, so power="on" flips only the 2700
        # non-ExecuTorch cases and leaves the case count unchanged.
        cases = build_matrix(power="on")
        assert len(cases) == 3212
        assert sum(1 for case in cases if case.power) == 2700

    def test_power_both_adds_powered_variants(self):
        # "both" adds a powered variant for each powerable case:
        # 3212 unpowered + 2700 powered.
        cases = build_matrix(power="both")
        assert len(cases) == 5912
        assert sum(1 for case in cases if case.power) == 2700

    def test_repeat_multiplies_matrix(self):
        assert len(build_matrix(power="off", repeat=3)) == 9636

    def test_model_filter(self):
        cases = build_matrix(models=["kws"], power="off")
        assert len(cases) == 803
        assert {c.model.id for c in cases} == {"kws"}

    def test_engine_filter(self):
        cases = build_matrix(engines=["helia-aot"], power="off")
        assert len(cases) == 900
        assert all(c.engine is EngineType.HELIA_AOT for c in cases)

    def test_tflm_engine_filter(self):
        cases = build_matrix(engines=["tflm"], power="off")
        assert len(cases) == 900
        assert all(c.engine is EngineType.TFLM for c in cases)

    def test_executorch_expands_both_providers_for_all_models(self):
        cases = build_matrix(
            engines=["executorch"],
            power="off",
            boards=["apollo330mP_evb"],
            toolchains=["gcc"],
            transports=["rtt"],
            memories=["auto"],
        )

        assert len(cases) == 8
        assert {case.cmsis_nn_backend for case in cases} == {
            ExecuTorchBackend.ARM,
            ExecuTorchBackend.NS,
        }
        for case in cases:
            backend = case.cmsis_nn_backend
            assert backend is not None
            assert f"executorch-{backend.value}" in case.case_id

    @pytest.mark.parametrize(
        ("selected", "expected"),
        [
            (["arm"], ExecuTorchBackend.ARM),
            (["ns"], ExecuTorchBackend.NS),
        ],
    )
    def test_executorch_provider_can_be_selected_independently(self, selected, expected):
        cases = build_matrix(
            models=["kws"],
            engines=["executorch"],
            executorch_backends=selected,
            power="off",
            boards=["apollo330mP_evb"],
            toolchains=["gcc"],
            transports=["rtt"],
            memories=["auto"],
        )

        assert len(cases) == 1
        assert cases[0].cmsis_nn_provider is expected
        assert f"executorch-{expected.value}" in cases[0].case_id

    @pytest.mark.parametrize(
        ("engine", "expected"),
        [
            (EngineType.TFLM, ExecuTorchBackend.ARM),
            (EngineType.HELIA_RT, ExecuTorchBackend.NS),
            (EngineType.HELIA_AOT, ExecuTorchBackend.NS),
        ],
    )
    def test_fixed_engine_provider_is_explicit(self, engine, expected):
        case = CaseSpec(
            model=MODELS["kws"],
            engine=engine,
            power=False,
            board=BOARDS["apollo510_evb"],
        )

        assert case.cmsis_nn_provider is expected
        assert f"-{engine.short_slug}-{expected.value}-" in case.case_id

    def test_executorch_is_limited_to_m55_and_skips_armclang(self):
        assert not build_matrix(
            engines=["executorch"],
            boards=["apollo3p_evb"],
            toolchains=["gcc"],
        )
        # An explicit armclang request still enumerates the cases so the
        # harness records the case_validity() skip reason; only the
        # board-default axis drops armclang for ExecuTorch.
        explicit_armclang = build_matrix(
            engines=["executorch"],
            boards=["apollo330mP_evb"],
            toolchains=["armclang"],
        )
        assert explicit_armclang
        assert all(
            case_validity(case) == "ExecuTorch validation does not yet cover armclang"
            for case in explicit_armclang
        )
        assert not build_matrix(
            engines=["executorch"],
            boards=["apollo330mP_evb"],
            memories=["psram"],
        )

    def test_executorch_enumerates_gcc_and_atfe(self):
        cases = build_matrix(
            models=["kws"],
            engines=["executorch"],
            boards=["apollo510_evb"],
            transports=["rtt"],
            memories=["auto"],
        )
        assert {c.toolchain for c in cases} == {
            Toolchain.ARM_NONE_EABI_GCC,
            Toolchain.ATFE,
        }

    def test_executorch_cases_remain_unpowered(self):
        cases = build_matrix(
            models=["kws"],
            engines=["executorch"],
            power="on",
            boards=["apollo510_evb"],
            toolchains=["gcc"],
            transports=["rtt"],
            memories=["auto"],
        )

        assert len(cases) == 2
        assert all(not case.power for case in cases)

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
        assert {c.memory for c in cases} == {MemoryProfile.AUTO}
        assert {c.jlink_serial for c in cases} == {"1160000174"}

    def test_power_serial_mapping_is_applied_only_to_powered_cases(self):
        cases = build_matrix(
            models=["kws"],
            engines=["helia-rt"],
            power="both",
            boards=["apollo510_evb"],
            toolchains=["gcc"],
            transports=["rtt"],
            memories=["auto"],
            power_serials={"apollo510_evb": "25QG"},
        )

        powered = next(case for case in cases if case.power)
        unpowered = next(case for case in cases if not case.power)
        assert powered.power_serial == "25QG"
        assert unpowered.power_serial is None

    def test_power_boards_restricts_power_to_selected_board(self):
        cases = build_matrix(
            models=["kws"],
            engines=["helia-rt"],
            power="on",
            power_boards=["apollo510_evb"],
            boards=["apollo510_evb", "apollo330mP_evb"],
            toolchains=["gcc"],
            transports=["rtt"],
            memories=["auto"],
            power_serials={"apollo510_evb": "H8MS"},
        )

        assert len(cases) == 2
        by_board = {case.board.id: case for case in cases}
        assert by_board["apollo510_evb"].power is True
        assert by_board["apollo510_evb"].power_serial == "H8MS"
        assert by_board["apollo330mP_evb"].power is False
        assert by_board["apollo330mP_evb"].power_serial is None

    def test_power_gpio_mapping_is_applied_only_to_powered_cases(self):
        cases = build_matrix(
            models=["kws"],
            engines=["helia-rt"],
            power="both",
            boards=["apollo330mP_evb"],
            toolchains=["gcc"],
            transports=["rtt"],
            memories=["auto"],
            power_gpio_pins={"apollo330mP_evb": (5, 6, 7)},
        )

        powered = next(case for case in cases if case.power)
        unpowered = next(case for case in cases if not case.power)
        assert powered.power_gpio_pins == (5, 6, 7)
        assert unpowered.power_gpio_pins is None

    def test_unknown_model_raises(self):
        with pytest.raises(ValueError, match="Unknown model"):
            build_matrix(models=["nope"])

    def test_unknown_engine_raises(self):
        with pytest.raises(ValueError, match="Unknown engine"):
            build_matrix(engines=["tflite"])

    def test_unknown_board_raises(self):
        with pytest.raises(ValueError, match="Unknown board"):
            build_matrix(boards=["not_a_board"])

    def test_unknown_power_board_raises(self):
        with pytest.raises(ValueError, match="Unknown power board"):
            build_matrix(power_boards=["not_a_board"])

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
        assert off.case_id == "apollo510_evb-kws-rt-ns-arm-none-eabi-gcc-rtt-auto"
        assert on.case_id == "apollo510_evb-kws-rt-ns-arm-none-eabi-gcc-rtt-auto-power"

    def test_case_id_encodes_repeat_attempt_when_stressing(self):
        repeated = CaseSpec(
            model=MODELS["kws"],
            engine=EngineType.HELIA_RT,
            power=False,
            board=BOARDS["apollo510_evb"],
            attempt=2,
            repeat_total=3,
        )
        assert repeated.case_id == "apollo510_evb-kws-rt-ns-arm-none-eabi-gcc-rtt-auto-run02"

    def test_deterministic_order(self):
        a = build_matrix()
        b = build_matrix()
        assert [c.case_id for c in a] == [c.case_id for c in b]


class TestCaseValidityGuards:
    def _case(self, **overrides: object) -> CaseSpec:
        from helia_profiler.validation.matrix import BOARDS, MODELS, CaseSpec
        from helia_profiler.engines import EngineType

        # replace() on the frozen, __post_init__-free CaseSpec is exactly
        # CaseSpec(**{**defaults, **overrides}) — but each field is typed.
        base = CaseSpec(
            model=MODELS["kws"],
            engine=EngineType.HELIA_RT,
            power=False,
            board=BOARDS["apollo3p_evb"],
            transport=Transport.RTT,
            memory=MemoryProfile.AUTO,
        )
        return dataclasses.replace(base, **overrides)

    def test_tcm_arena_plus_weights_too_large_for_dtcm_is_skipped(self, tmp_path):
        # Hermetic: a synthetic 53 KB "model" whose weights + KWS's 32 KB
        # arena exceed Apollo3's 64 KB DTCM (the real fixture is LFS-managed
        # and absent on CI runners, where the guard deliberately stays silent).
        import dataclasses

        fixture = tmp_path / "model.tflite"
        fixture.write_bytes(b"\x00" * (53 * 1024))
        model = dataclasses.replace(self._case().model, fixture_path=str(fixture))
        case = self._case(memory=MemoryProfile.TCM, model=model)
        reason = case_validity(case)
        assert reason is not None and "DTCM" in reason

    def test_tcm_guard_silent_when_fixture_missing(self):
        import dataclasses

        model = dataclasses.replace(self._case().model, fixture_path="does/not/exist.tflite")
        case = self._case(memory=MemoryProfile.TCM, model=model)
        assert case_validity(case) is None

    def test_tcm_fits_on_larger_dtcm(self):
        from helia_profiler.validation.matrix import BOARDS

        # Apollo510 has 512 KB DTCM — same arena fits.
        case = self._case(board=BOARDS["apollo510_evb"], memory=MemoryProfile.TCM)
        assert case_validity(case) is None

    def test_ap3_psram_power_pin_conflict_is_skipped(self):
        case = self._case(power=True, memory=MemoryProfile.PSRAM)
        reason = case_validity(case)
        assert reason is not None and "MSPI0" in reason

    def test_ap5_psram_power_is_allowed(self):
        from helia_profiler.validation.matrix import BOARDS

        case = self._case(board=BOARDS["apollo510_evb"], power=True, memory=MemoryProfile.PSRAM)
        assert case_validity(case) is None
