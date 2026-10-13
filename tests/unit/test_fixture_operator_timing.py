"""Per-operator timing binds to a fixture build only when the profile run describes it."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from helia_profiler.engines import EngineType
from helia_profiler.fixture import (
    FixedFixture,
    FixtureBuild,
    FixtureIO,
    FixtureTimingScope,
    TypedFixture,
)
from helia_profiler.fixture_analysis import FixtureTensor, Int8Tensor, PerTensorQuantization
from helia_profiler.fixture_operator_timing import bind_operator_timing
from helia_profiler.fixture_runtime import FixtureFile
from helia_profiler.fixture_target import supported_fixture_target
from helia_profiler.placement import Placement
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
        measured_clock_hz=raw.get("measured_clock_hz"),
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


def typed_fixture_for(sha: str, tmp_path, *, output_name: str = "scores") -> TypedFixture:
    return TypedFixture(
        FixtureFile(tmp_path / "model", sha),
        (
            FixtureIO(
                FixtureTensor("mfcc", 0, "int8", (1, 49, 10, 1), PerTensorQuantization(0.125, -4)),
                FixtureFile(tmp_path / "input", sha),
            ),
        ),
        (
            FixtureIO(
                FixtureTensor(output_name, 1, "int8", (1, 12), PerTensorQuantization(2**-8, -128)),
                FixtureFile(tmp_path / "expected", sha),
            ),
        ),
    )


def build_for(
    engine: EngineType, fixture: FixedFixture | TypedFixture, *, prepared_runtime: bool = False
) -> FixtureBuild:
    tmp_path = fixture.model.path.parent
    file = FixtureFile(tmp_path / "f", "0" * 64)
    manifest = None
    if prepared_runtime:
        import hashlib

        path = tmp_path / "manifest.json"
        path.write_text("{}")
        manifest = FixtureFile(path, hashlib.sha256(path.read_bytes()).hexdigest())
    return FixtureBuild(
        fixture.identity,
        tmp_path,
        file,
        (),
        file,
        True,
        manifest,
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
        arena_location=Placement.SRAM,
        weights_location=Placement.MRAM,
    )


def approximate_timing(build, fixture, run, **kwargs):
    return bind_operator_timing(build, fixture, run, allow_runtime_difference=True)


def test_real_aot_capture_binds_within_one_percent(tmp_path):
    fixture = fixture_for(KWS_SHA, tmp_path)
    result = approximate_timing(
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
    assert result.engine_version == "0.22.0" and result.runtime_stack == "unknown"


def test_tflm_prepared_runtime_needs_explicit_label(tmp_path):
    fixture, run = fixture_for(KWS_SHA, tmp_path), profile("kws_tflm")
    build = build_for(EngineType.TFLM, fixture, prepared_runtime=True)
    refused = bind_operator_timing(build, fixture, run)
    assert refused.operators is None and refused.reason == "runtime_stack_unknown"
    labelled = approximate_timing(build, fixture, run, allow_runtime_difference=True)
    assert labelled.accepted and labelled.agreement_pct == pytest.approx(0.458, abs=1e-3)
    assert labelled.runtime_stack == "unknown"


def test_short_inference_uses_two_percent(tmp_path):
    raw = PROFILES["ad_aot_short"]
    run = profile(
        "ad_aot_short",
        board="apollo510_evb",
        engine={"type": "helia-aot", "version": "0.23.0"},
        config={**raw["config"], "model": {"arena_location": "sram", "weights_location": "mram"}},
    )
    fixture = fixture_for(raw["model_sha256"], tmp_path)
    result = approximate_timing(build_for(EngineType.HELIA_AOT, fixture), fixture, run)
    assert result.accepted, result.reason
    assert result.agreement_pct is not None and 1.0 < result.agreement_pct < 2.0
    assert result.tolerance_pct == 2.0


def test_long_inference_outside_one_percent_is_refused(tmp_path):
    raw = PROFILES["kws_aot"]
    layers = [dict(layer, cycles=layer["cycles"] * 1.012) for layer in raw["layers"]]
    fixture = fixture_for(KWS_SHA, tmp_path)
    result = approximate_timing(
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
    result = approximate_timing(build_for(EngineType.HELIA_AOT, fixture), fixture, run)
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
        ({"measured_clock_hz": 250_000_000}, "measured_clock_mismatch"),
        ({"measured_clock_hz": 90_000_000}, "measured_clock_mismatch"),
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
    result = approximate_timing(
        build_for(EngineType.HELIA_AOT, fixture),
        fixture,
        profile("kws_aot", **override),
    )
    assert result.operators is None and not result.accepted
    assert result.reason == reason


@pytest.mark.parametrize("measured", [None, 0, 96_000_000, 91_200_000, 100_800_000])
def test_measured_clock_absent_zero_or_within_tolerance_still_binds(tmp_path, measured):
    fixture = fixture_for(KWS_SHA, tmp_path)
    result = approximate_timing(
        build_for(EngineType.HELIA_AOT, fixture),
        fixture,
        profile("kws_aot", measured_clock_hz=measured),
    )
    assert result.accepted, result.reason


def test_measured_clock_shares_the_capture_tolerance(tmp_path, monkeypatch):
    from helia_profiler import capture, fixture_operator_timing
    from helia_profiler.results.models import DEVICE_CLOCK_TOLERANCE

    assert DEVICE_CLOCK_TOLERANCE == 0.05
    assert capture.DEVICE_CLOCK_TOLERANCE is DEVICE_CLOCK_TOLERANCE
    # The check reads the shared constant, not a copy of its value.
    monkeypatch.setattr(fixture_operator_timing, "DEVICE_CLOCK_TOLERANCE", 0.25)
    fixture = fixture_for(KWS_SHA, tmp_path)
    result = approximate_timing(
        build_for(EngineType.HELIA_AOT, fixture),
        fixture,
        profile("kws_aot", measured_clock_hz=110_000_000),
    )
    assert result.accepted, result.reason


def test_layer_overflow_is_refused(tmp_path):
    layers = [dict(layer) for layer in PROFILES["kws_aot"]["layers"]]
    layers[3]["overflow"] = True
    fixture = fixture_for(KWS_SHA, tmp_path)
    result = approximate_timing(
        build_for(EngineType.HELIA_AOT, fixture),
        fixture,
        profile("kws_aot", layers=layers),
    )
    assert result.reason == "counter_overflow"


def test_fixture_toolchain_unrecorded_is_refused(tmp_path):
    fixture = fixture_for(KWS_SHA, tmp_path)
    build = replace(build_for(EngineType.HELIA_AOT, fixture), toolchain=None)
    result = approximate_timing(build, fixture, profile("kws_aot"))
    assert result.reason == "toolchain_unrecorded"


def test_fixture_other_than_the_build_is_refused(tmp_path):
    fixture = fixture_for(KWS_SHA, tmp_path)
    other = replace(fixture, output_tensor=Int8Tensor((1, 12), 0.00390625, -127, 1))
    result = approximate_timing(build_for(EngineType.HELIA_AOT, other), fixture, profile("kws_aot"))
    assert result.reason == "fixture_mismatch" and result.operators is None


def test_tflm_backend_other_than_cmsis_nn_is_refused(tmp_path):
    fixture = fixture_for(KWS_SHA, tmp_path)
    raw = PROFILES["kws_tflm"]
    run = profile("kws_tflm", config={**raw["config"], "engine": {"backend": "reference"}})
    build = build_for(EngineType.TFLM, fixture, prepared_runtime=True)
    result = approximate_timing(build, fixture, run, allow_runtime_difference=True)
    assert result.reason == "backend_mismatch" and result.operators is None


def test_non_finite_layer_cycles_are_refused(tmp_path):
    layers = [dict(layer) for layer in PROFILES["kws_aot"]["layers"]]
    layers[2]["cycles"] = float("nan")
    fixture = fixture_for(KWS_SHA, tmp_path)
    result = approximate_timing(
        build_for(EngineType.HELIA_AOT, fixture), fixture, profile("kws_aot", layers=layers)
    )
    assert result.reason == "layer_cycles_unavailable" and result.operators is None


def test_typed_fixture_binds_only_to_its_own_build(tmp_path):
    fixture = typed_fixture_for(KWS_SHA, tmp_path)
    build = build_for(EngineType.HELIA_AOT, fixture)
    result = approximate_timing(build, fixture, profile("kws_aot"))
    assert result.accepted and result.operators is not None and len(result.operators) == 13
    renamed = typed_fixture_for(KWS_SHA, tmp_path, output_name="logits")
    refused = approximate_timing(build, renamed, profile("kws_aot"))
    assert refused.operators is None and refused.reason == "fixture_mismatch"
    legacy = fixture_for(KWS_SHA, tmp_path)
    assert approximate_timing(build, legacy, profile("kws_aot")).reason == "fixture_mismatch"


@pytest.mark.parametrize(
    "backend,reason",
    [
        (None, None),
        ("helia", None),
        ("ethos_u", "backend_mismatch"),
        ("cmsis_nn", "backend_mismatch"),
    ],
)
def test_heliart_fixture_binds_only_to_a_helia_backend_profile(tmp_path, backend, reason):
    fixture = fixture_for(KWS_SHA, tmp_path)
    raw = PROFILES["kws_aot"]
    run = profile(
        "kws_aot",
        engine={"type": "helia-rt", "version": "1.21.2"},
        config={**raw["config"], "engine": {"backend": backend}},
    )
    build = build_for(EngineType.HELIA_RT, fixture, prepared_runtime=True)
    assert bind_operator_timing(build, fixture, run).reason == (reason or "runtime_stack_unknown")
    labelled = approximate_timing(build, fixture, run, allow_runtime_difference=True)
    assert labelled.reason == reason
    if reason is None:
        assert labelled.accepted
        assert labelled.runtime_stack == "unknown"


@pytest.mark.parametrize("profile_weights", ["mram", "psram"])
def test_psram_build_cannot_inherit_ordinary_profile_timing(tmp_path, profile_weights):
    from helia_profiler.fixture_psram import FixturePsramPayload, FixturePsramWeights

    fixture = fixture_for(KWS_SHA, tmp_path)
    build = replace(
        build_for(EngineType.HELIA_AOT, fixture),
        psram_weights=FixturePsramWeights((FixturePsramPayload(0, 4, 2987074),)),
    )
    run = profile("kws_aot")
    snapshot = dict(run.metadata.config_snapshot)
    snapshot["model"] = {**snapshot["model"], "weights_location": profile_weights}
    run = replace(run, metadata=replace(run.metadata, config_snapshot=snapshot))
    result = approximate_timing(build, fixture, run)
    assert not result.accepted
    assert result.reason == "psram_placement_not_bound_by_profile"


@pytest.mark.parametrize(
    "arena,weights",
    [
        (Placement.TCM, Placement.MRAM),
        (Placement.SRAM, Placement.SRAM),
        (Placement.TCM, Placement.SRAM),
    ],
)
@pytest.mark.parametrize("same", [False, True])
def test_operator_timing_compares_effective_build_banks(tmp_path, arena, weights, same):
    fixture = fixture_for(KWS_SHA, tmp_path)
    build = replace(
        build_for(EngineType.HELIA_AOT, fixture), arena_location=arena, weights_location=weights
    )
    run = profile("kws_aot")
    if same:
        snapshot = dict(run.metadata.config_snapshot)
        snapshot["model"] = {
            **snapshot["model"],
            "arena_location": arena.value,
            "weights_location": weights.value,
        }
        run = replace(run, metadata=replace(run.metadata, config_snapshot=snapshot))
    result = approximate_timing(build, fixture, run)
    assert result.accepted is same
    assert result.reason == (None if same else "placement_mismatch")


def test_legacy_build_without_banks_cannot_inherit_profile_placement(tmp_path):
    fixture = fixture_for(KWS_SHA, tmp_path)
    build = replace(
        build_for(EngineType.HELIA_AOT, fixture), arena_location=None, weights_location=None
    )
    assert (
        approximate_timing(build, fixture, profile("kws_aot")).reason
        == "build_placement_unrecorded"
    )


def test_recorded_engine_version_difference_is_not_same_runtime(tmp_path):
    from helia_profiler.fixture import EngineSource

    fixture = fixture_for(KWS_SHA, tmp_path)
    build = replace(
        build_for(EngineType.HELIA_AOT, fixture),
        engine_source=EngineSource("helia-aot", "0.23.0", "1" * 40),
    )
    result = bind_operator_timing(build, fixture, profile("kws_aot"))
    assert result.runtime_stack == "different"
    assert not result.accepted


def test_missing_engine_identity_is_unknown_not_same(tmp_path):
    fixture = fixture_for(KWS_SHA, tmp_path)
    result = bind_operator_timing(
        build_for(EngineType.HELIA_AOT, fixture), fixture, profile("kws_aot")
    )
    assert result.runtime_stack == "unknown"
    assert not result.accepted


def evidenced_pair(tmp_path):
    """Synthetic records with observed source/provider pins, not requested refs."""
    import hashlib
    from dataclasses import asdict

    from helia_profiler.deps.compatibility import QualificationState
    from helia_profiler.fixture import EngineSource
    from helia_profiler.results.dependencies import (
        ContentDigest,
        DependencyLockMode,
        DependencyLockState,
        DependencyModule,
        DependencyProvenance,
        DependencyWorkspace,
    )

    fixture = fixture_for(KWS_SHA, tmp_path)
    source = EngineSource("helia-aot", "0.22.0", "a" * 40)
    digest = ContentDigest("sha256", "b" * 64)
    inputs = {
        "engine": {
            "type": "helia-aot",
            "backend": None,
            "config": {"aot_args": {"optimize": True}},
            "config_path_hash": None,
            "resolved_backend": None,
            "resolved_variant": None,
            "resolved_version": None,
            "toolchain_tag": None,
            "cmake_vars": {},
            "source": asdict(source),
        }
    }
    deps = DependencyProvenance(
        DependencyWorkspace(1, "fp", "baseline", "base-fp", digest, inputs, tmp_path),
        DependencyLockState(DependencyLockMode.REUSED, False, True, True, 1, digest, digest),
        (
            DependencyModule(
                "ns-cmsis-nn",
                "ns-cmsis-nn",
                "git",
                "requested",
                None,
                "c" * 40,
                digest,
                "https://example.org/kernels",
                "unused/path",
            ),
        ),
        (),
        QualificationState.QUALIFIED,
    )
    path = tmp_path / "state.json"
    path.write_text(json.dumps(deps.to_dict()))
    build = replace(
        build_for(EngineType.HELIA_AOT, fixture),
        engine_source=source,
        dependency_state=FixtureFile(path, hashlib.sha256(path.read_bytes()).hexdigest()),
    )
    run = profile("kws_aot")
    run = replace(run, metadata=replace(run.metadata, dependencies=deps))
    return build, fixture, run


def test_fully_evidenced_identity_matches_without_guessing(tmp_path):
    build, fixture, run = evidenced_pair(tmp_path)
    result = bind_operator_timing(build, fixture, run)
    assert result.accepted and result.runtime_stack == "same"
    assert result.operators is not None and len(result.operators) == 13


@pytest.mark.parametrize("change", ["source", "settings", "provider"])
def test_same_package_version_with_different_runtime_facts_is_different(tmp_path, change):
    build, fixture, run = evidenced_pair(tmp_path)
    deps = run.metadata.dependencies
    inputs = json.loads(json.dumps(deps.workspace.inputs))
    modules = deps.modules
    if change == "source":
        inputs["engine"]["source"]["commit"] = "d" * 40
    elif change == "settings":
        inputs["engine"]["config"]["aot_args"]["optimize"] = False
    else:
        modules = (replace(modules[0], peeled_commit="d" * 40),)
    deps = replace(deps, workspace=replace(deps.workspace, inputs=inputs), modules=modules)
    run = replace(run, metadata=replace(run.metadata, dependencies=deps))
    refused = bind_operator_timing(build, fixture, run)
    assert not refused.accepted and refused.runtime_stack == "different"
    labelled = bind_operator_timing(build, fixture, run, allow_runtime_difference=True)
    assert labelled.accepted and labelled.runtime_stack == "different"


@pytest.mark.parametrize("missing", ["source", "config", "config_path_hash", "modules"])
def test_missing_runtime_facts_remain_unknown(tmp_path, missing):
    build, fixture, run = evidenced_pair(tmp_path)
    deps = run.metadata.dependencies
    inputs = json.loads(json.dumps(deps.workspace.inputs))
    if missing != "modules":
        del inputs["engine"][missing]
    deps = replace(
        deps,
        workspace=replace(deps.workspace, inputs=inputs),
        modules=() if missing == "modules" else deps.modules,
    )
    run = replace(run, metadata=replace(run.metadata, dependencies=deps))
    result = bind_operator_timing(build, fixture, run)
    assert result.runtime_stack == "unknown" and result.reason == "runtime_stack_unknown"
    labelled = bind_operator_timing(build, fixture, run, allow_runtime_difference=True)
    assert labelled.accepted and labelled.runtime_stack == "unknown"


@pytest.mark.parametrize("damage", ["tampered", "absent", "nonobject", "duplicate"])
@pytest.mark.parametrize("missing_profile", [False, True])
def test_explicit_allowance_never_waives_provenance_integrity(tmp_path, damage, missing_profile):
    import hashlib

    build, fixture, run = evidenced_pair(tmp_path)
    path = build.dependency_state.path
    if damage == "absent":
        path.unlink()
    elif damage == "tampered":
        path.write_text("{}")
    else:
        path.write_text("[]" if damage == "nonobject" else '{"workspace": {}, "workspace": {}}')
        build = replace(
            build, dependency_state=FixtureFile(path, hashlib.sha256(path.read_bytes()).hexdigest())
        )
    if missing_profile:
        run = replace(run, metadata=replace(run.metadata, dependencies=None))
    result = bind_operator_timing(build, fixture, run, allow_runtime_difference=True)
    assert result.reason == "runtime_provenance_unverified" and not result.accepted


def test_differing_fact_is_not_hidden_by_another_missing_fact(tmp_path):
    build, fixture, run = evidenced_pair(tmp_path)
    deps = run.metadata.dependencies
    inputs = json.loads(json.dumps(deps.workspace.inputs))
    del inputs["engine"]["source"]
    inputs["engine"]["config"] = {"aot_args": {"optimize": False}}
    run = replace(
        run,
        metadata=replace(
            run.metadata,
            dependencies=replace(deps, workspace=replace(deps.workspace, inputs=inputs)),
        ),
    )
    assert bind_operator_timing(build, fixture, run).runtime_stack == "different"
