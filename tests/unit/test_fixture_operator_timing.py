"""Per-operator timing binds to a fixture build only when the profile run describes it."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from helia_profiler.engines import EngineType
from helia_profiler.fixture import FixedFixture, FixtureBuild, FixtureTimingScope
from helia_profiler.fixture_analysis import Int8Tensor
from helia_profiler.fixture_operator_timing import bind_operator_timing
from helia_profiler.fixture_runtime import FixtureFile
from helia_profiler.fixture_target import supported_fixture_target
from helia_profiler.results.models import (
    EngineInfo,
    FirmwareMeta,
    LayerResult,
    ModelInfo,
    PlatformInfo,
    PmuResult,
    ProfileResult,
    RunMetadata,
    ToolchainInfo,
)

# Excerpts of real hpx profile captures (KWS on Apollo510 EVB; AD and a
# busy-loop clean window from earlier runs).
PROFILES = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "operator_timing" / "profiles.json").read_text()
)
KWS_SHA = PROFILES["kws_aot"]["model_sha256"]
ATFE = ToolchainInfo("atfe", "clang version 22.1.0")


def profile(name: str, **override) -> ProfileResult:
    raw = {**PROFILES[name], **override}
    layers = [
        LayerResult(layer["id"], layer["op"], cycles=layer["cycles"], overflow=layer["overflow"])
        for layer in raw["layers"]
    ]
    meta = FirmwareMeta(
        system_clock_hz=raw["system_clock_hz"],
        clean_infer_avg_cycles=raw["clean_infer_avg_cycles"],
    )
    metadata = RunMetadata(
        config_snapshot=raw["config"],
        platform=PlatformInfo(board=raw["board"], cpu_clock_name=raw["cpu_clock_name"]),
        model=ModelInfo("model.tflite", 1, raw["model_sha256"]),
        toolchain=ToolchainInfo(**raw["toolchain"]),
        engine=EngineInfo(**raw["engine"]),
        firmware=meta,
    )
    return ProfileResult(
        PmuResult(meta, layers=layers, overflow_detected=raw["overflow_detected"]),
        metadata=metadata,
    )


def fixture_for(sha: str, tmp_path) -> FixedFixture:
    return FixedFixture(
        FixtureFile(tmp_path / "model", sha),
        FixtureFile(tmp_path / "input", sha),
        FixtureFile(tmp_path / "expected", sha),
        Int8Tensor((1, 49, 10, 1), 0.125, -4, 0),
        Int8Tensor((1, 12), 0.00390625, -128, 1),
    )


def build_for(
    engine: EngineType, fixture: FixedFixture, *, prepared_runtime: bool = False
) -> FixtureBuild:
    tmp_path = fixture.model.path.parent
    file = FixtureFile(tmp_path / "f", "0" * 64)
    return FixtureBuild(
        fixture.identity,
        tmp_path,
        file,
        (),
        file,
        True,
        file if prepared_runtime else None,
        file,
        None,
        None,
        FixtureTimingScope.RESTORE_AND_INVOKE,
        engine,
        "build-identity",
        100,
        5,
        "intent",
        supported_fixture_target(),
        ATFE,
    )


def test_real_aot_capture_binds_within_one_percent(tmp_path):
    fixture = fixture_for(KWS_SHA, tmp_path)
    result = bind_operator_timing(
        build_for(EngineType.HELIA_AOT, fixture),
        fixture,
        profile("kws_aot"),
    )
    assert result.accepted and result.reason is None
    assert result.operators is not None and len(result.operators) == 13
    assert result.operators[0].op == "CONV_2D:0" and result.operators[0].source_index == 0
    assert sum(op.share for op in result.operators) == pytest.approx(1.0)
    assert result.agreement_pct == pytest.approx(0.0959, abs=1e-3)
    assert result.tolerance_pct == 1.0
    assert result.unattributed_cycles == pytest.approx(1955904 - 1957780, abs=1)
    assert result.engine_version == "0.22.0" and result.runtime_stack == "same"


def test_tflm_prepared_runtime_needs_explicit_label(tmp_path):
    fixture, run = fixture_for(KWS_SHA, tmp_path), profile("kws_tflm")
    build = build_for(EngineType.TFLM, fixture, prepared_runtime=True)
    refused = bind_operator_timing(build, fixture, run)
    assert refused.operators is None and refused.reason == "runtime_stack_mismatch"
    labelled = bind_operator_timing(build, fixture, run, allow_runtime_difference=True)
    assert labelled.accepted and labelled.agreement_pct == pytest.approx(0.458, abs=1e-3)
    assert labelled.runtime_stack == "profile_baseline_differs_from_fixture_runtime"


def test_short_inference_uses_two_percent(tmp_path):
    raw = PROFILES["ad_aot_short"]
    run = profile(
        "ad_aot_short",
        board="apollo510_evb",
        engine={"type": "helia-aot", "version": "0.23.0"},
        config={**raw["config"], "model": {"arena_location": "sram", "weights_location": "mram"}},
    )
    fixture = fixture_for(raw["model_sha256"], tmp_path)
    result = bind_operator_timing(build_for(EngineType.HELIA_AOT, fixture), fixture, run)
    assert result.accepted, result.reason
    assert result.agreement_pct is not None and 1.0 < result.agreement_pct < 2.0
    assert result.tolerance_pct == 2.0


def test_long_inference_outside_one_percent_is_refused(tmp_path):
    raw = PROFILES["kws_aot"]
    layers = [dict(layer, cycles=layer["cycles"] * 1.012) for layer in raw["layers"]]
    fixture = fixture_for(KWS_SHA, tmp_path)
    result = bind_operator_timing(
        build_for(EngineType.HELIA_AOT, fixture),
        fixture,
        profile("kws_aot", layers=layers),
    )
    assert result.operators is None
    assert result.reason == "layer_sum_disagrees_with_clean_window"
    assert result.agreement_pct == pytest.approx(1.297, abs=0.01)


def test_busy_loop_clean_window_is_refused(tmp_path):
    raw = PROFILES["kws_rt_busy_loop"]
    run = profile(
        "kws_rt_busy_loop",
        engine={"type": "helia-aot", "version": "0.22.0"},
        toolchain={"compiler": "atfe", "compiler_version": "clang version 22.1.0"},
        config={**raw["config"], "model": {"arena_location": "sram", "weights_location": "mram"}},
    )
    fixture = fixture_for(raw["model_sha256"], tmp_path)
    result = bind_operator_timing(build_for(EngineType.HELIA_AOT, fixture), fixture, run)
    assert result.operators is None and result.reason == "clean_window_not_inference"


@pytest.mark.parametrize(
    "override,reason",
    [
        ({"model_sha256": "1" * 64}, "model_mismatch"),
        ({"model_sha256": ""}, "profile_model_identity_unrecorded"),
        ({"engine": {"type": "tflm", "version": None}}, "engine_mismatch"),
        ({"toolchain": {"compiler": "gcc", "compiler_version": "15.2.1"}}, "compiler_mismatch"),
        (
            {"toolchain": {"compiler": "atfe", "compiler_version": "clang version 21.1.0"}},
            "compiler_mismatch",
        ),
        ({"board": "apollo510b_evb"}, "board_mismatch"),
        ({"cpu_clock_name": "hp"}, "clock_mismatch"),
        ({"system_clock_hz": 192_000_000}, "clock_mismatch"),
        (
            {"config": {"model": {"arena_location": "tcm", "weights_location": "mram"}}},
            "placement_mismatch",
        ),
        (
            {"config": {"model": {"arena_location": "sram", "weights_location": "tcm"}}},
            "placement_mismatch",
        ),
        ({"overflow_detected": True}, "counter_overflow"),
        ({"clean_infer_avg_cycles": None}, "clean_window_unavailable"),
        ({"layers": []}, "per_layer_unavailable"),
    ],
)
def test_identity_or_counter_mismatch_is_null_with_reason(tmp_path, override, reason):
    if "config" in override:
        override["config"]["profiling"] = {"clean_window_probe": "infer"}
    fixture = fixture_for(KWS_SHA, tmp_path)
    result = bind_operator_timing(
        build_for(EngineType.HELIA_AOT, fixture),
        fixture,
        profile("kws_aot", **override),
    )
    assert result.operators is None and not result.accepted
    assert result.reason == reason


def test_layer_overflow_is_refused(tmp_path):
    layers = [dict(layer) for layer in PROFILES["kws_aot"]["layers"]]
    layers[3]["overflow"] = True
    fixture = fixture_for(KWS_SHA, tmp_path)
    result = bind_operator_timing(
        build_for(EngineType.HELIA_AOT, fixture),
        fixture,
        profile("kws_aot", layers=layers),
    )
    assert result.reason == "counter_overflow"


def test_fixture_toolchain_unrecorded_is_refused(tmp_path):
    fixture = fixture_for(KWS_SHA, tmp_path)
    build = replace(build_for(EngineType.HELIA_AOT, fixture), toolchain=None)
    result = bind_operator_timing(build, fixture, profile("kws_aot"))
    assert result.reason == "toolchain_unrecorded"


def test_fixture_other_than_the_build_is_refused(tmp_path):
    fixture = fixture_for(KWS_SHA, tmp_path)
    other = replace(fixture, output_tensor=Int8Tensor((1, 12), 0.00390625, -127, 1))
    result = bind_operator_timing(
        build_for(EngineType.HELIA_AOT, other), fixture, profile("kws_aot")
    )
    assert result.reason == "fixture_mismatch" and result.operators is None


def test_tflm_backend_other_than_cmsis_nn_is_refused(tmp_path):
    fixture = fixture_for(KWS_SHA, tmp_path)
    raw = PROFILES["kws_tflm"]
    run = profile("kws_tflm", config={**raw["config"], "engine": {"backend": "reference"}})
    build = build_for(EngineType.TFLM, fixture, prepared_runtime=True)
    result = bind_operator_timing(build, fixture, run, allow_runtime_difference=True)
    assert result.reason == "backend_mismatch" and result.operators is None


def test_non_finite_layer_cycles_are_refused(tmp_path):
    layers = [dict(layer) for layer in PROFILES["kws_aot"]["layers"]]
    layers[2]["cycles"] = float("nan")
    fixture = fixture_for(KWS_SHA, tmp_path)
    result = bind_operator_timing(
        build_for(EngineType.HELIA_AOT, fixture), fixture, profile("kws_aot", layers=layers)
    )
    assert result.reason == "layer_cycles_unavailable" and result.operators is None
