"""The conditions a run records (#648): derived once, written everywhere, compared."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from helia_profiler.engines import EngineType
from helia_profiler.engines.base import ArenaRegion, HeliaAotArtifacts
from helia_profiler.errors import EngineError
from helia_profiler.evaluation.comparability import assess_comparability
from helia_profiler.evaluation.compare import RunArtifacts
from helia_profiler.placement import ArenaRole, Placement
from helia_profiler.report import write_report
from helia_profiler.report.conditions import run_conditions
from helia_profiler.results import (
    Conditions,
    FirmwareMeta,
    LayerResult,
    PmuResult,
    PresetResult,
    PsramInfo,
    load_result_manifest,
)
from helia_profiler.results.run_summary import RUN_SUMMARY_SCHEMA_VERSION, RunSummary
from tests.contracts.conftest import make_pmu_ctx
from tests.pipeline_context_helpers import set_profile_result


def _pmu(**meta) -> PmuResult:
    layers = [
        LayerResult(id=0, op="CONV_2D", counters={"ARM_PMU_CPU_CYCLES": 1200.0}, cycles=1200.0),
        LayerResult(id=1, op="SOFTMAX", counters={"ARM_PMU_CPU_CYCLES": 300.0}, cycles=300.0),
    ]
    return PmuResult(
        meta=FirmwareMeta(system_clock_hz=96_000_000, presets=("basic_cpu",), **meta),
        presets={"basic_cpu": PresetResult(name="basic_cpu", layers=layers)},
        layers=layers,
    )


def _ctx(tmp_path: Path, *, engine: str = "helia-rt", extra: dict | None = None, **meta):
    ctx = make_pmu_ctx(tmp_path, board="apollo510_evb", engine=engine, extra=extra)
    set_profile_result(ctx, _pmu(**meta))
    return ctx


def test_conditions_come_from_the_config_and_the_capture(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, measured_clock_hz=249_600_000)

    conditions = run_conditions(ctx)

    assert conditions == Conditions(
        target="apollo510_evb",
        cpu_hz=ctx_platform_mhz(ctx) * 1_000_000,
        weights_memory="mram",
        weights_cache="on",
        cache_regime="warm",
        external_memory_clock=None,
        application_load="none",
    )


@pytest.mark.parametrize("board", ["apollo3p_evb", "apollo4p_evb"])
def test_ordinary_capture_on_a_target_without_dcache_omits_regime(tmp_path, board):
    ctx = make_pmu_ctx(tmp_path, board=board, engine="helia-rt")
    set_profile_result(ctx, _pmu())
    conditions = run_conditions(ctx).to_dict()
    assert conditions["weights_cache"] == "off"
    assert "cache_regime" not in conditions


def test_cpu_hz_is_the_configured_clock_whatever_the_device_measured(tmp_path: Path) -> None:
    configured = ctx_platform_mhz(_ctx(tmp_path)) * 1_000_000
    assert configured > 0
    for measured in (None, 0, 96_338_160, 96_240_576):
        assert run_conditions(_ctx(tmp_path, measured_clock_hz=measured)).cpu_hz == configured


def ctx_platform_mhz(ctx) -> int:
    assert ctx.run_metadata.platform is not None
    return ctx.run_metadata.platform.cpu_clock_mhz


def test_weights_memory_follows_the_planned_placement(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.weights_region = Placement.TCM
    assert run_conditions(ctx).weights_memory == "dtcm"
    ctx.weights_region = None
    assert run_conditions(ctx).weights_memory is None


def test_external_memory_clock_is_present_only_with_psram(tmp_path: Path) -> None:
    reported = PsramInfo(
        size_bytes=1 << 23,
        clock_hz=96_000_000,
        capabilities=0,
        state=1,
        last_init_status=0,
        xip_enabled=False,
        timing_status=0,
        rxdqs_delay=0,
    )
    assert run_conditions(_ctx(tmp_path, psram=reported)).external_memory_clock == 96

    configured = _ctx(tmp_path, extra={"target": {"psram": {"clock_hz": 125_000_000}}})
    configured.weights_region = Placement.PSRAM
    assert run_conditions(configured).external_memory_clock == 125

    assert run_conditions(_ctx(tmp_path)).external_memory_clock is None


def _aot_artifacts(
    tmp_path: Path,
    *,
    aot_memory_intent_json: str = "{}",
    report_files: tuple[Path, ...] = (),
    target_name: str = "apollo510_evb",
) -> HeliaAotArtifacts:
    return HeliaAotArtifacts(
        engine_type=EngineType.HELIA_AOT,
        engine_header="hpx_model.h",
        aot_prefix="hpx",
        aot_module_name="hpx_model",
        aot_cmake_target="nsx::hpx_model",
        helia_aot_version="0.33.0",
        aot_platform_name=target_name,
        aot_memory_intent_json=aot_memory_intent_json,
        report_files=report_files,
    )


@pytest.mark.parametrize(
    ("placement", "stream_evidence", "dcache", "expected"),
    [
        (Placement.PSRAM, True, "on", "cold"),
        (Placement.PSRAM, False, "on", "warm"),
        (Placement.MRAM, True, "on", "warm"),
        (Placement.PSRAM, True, "off", None),
    ],
)
def test_aot_cache_regime(tmp_path, placement, stream_evidence, dcache, expected):
    ctx = _ctx(
        tmp_path,
        engine="helia-aot",
        extra={
            "model": {"weights_location": placement.value},
            "profiling": {"stream_evidence": stream_evidence},
        },
        dcache=dcache,
    )
    region = ArenaRegion(0, "weights", 64, 16, ArenaRole.CONSTANT, placement.value, placement)
    ctx.engine_artifacts = dataclasses.replace(
        _aot_artifacts(tmp_path), aot_allocate_arenas=False, aot_arena_regions=[region]
    )
    recorded = run_conditions(ctx).to_dict()
    if expected is None:
        assert "cache_regime" not in recorded
    else:
        assert recorded["cache_regime"] == expected


def test_per_tensor_psram_placement_determines_actual_cache_preparation(tmp_path):
    ctx = _ctx(
        tmp_path,
        engine="helia-aot",
        extra={"model": {"weights_location": "mram"}, "profiling": {"stream_evidence": True}},
    )
    region = ArenaRegion(0, "weights", 64, 16, ArenaRole.CONSTANT, "psram", Placement.PSRAM)
    ctx.engine_artifacts = dataclasses.replace(
        _aot_artifacts(tmp_path), aot_allocate_arenas=False, aot_arena_regions=[region]
    )
    conditions = run_conditions(ctx)
    assert conditions.weights_memory == "mram"
    assert conditions.cache_regime == "cold"


@pytest.mark.parametrize(("other", "expected"), [(Placement.TCM, "cold"), (Placement.MRAM, None)])
def test_mixed_psram_cache_preparation(tmp_path, other, expected):
    from tests.contracts.test_firmware_render_snapshots import _render

    ctx = _ctx(tmp_path, engine="helia-aot", extra={"profiling": {"stream_evidence": True}})
    regions = [
        ArenaRegion(i, "weights", 64, 16, ArenaRole.CONSTANT, where.value, where, "weights.bin")
        for i, where in enumerate((Placement.PSRAM, other))
    ]
    ctx.engine_artifacts = dataclasses.replace(
        _aot_artifacts(tmp_path), aot_allocate_arenas=False, aot_arena_regions=regions
    )
    rendered = _render(
        "apollo510",
        "rtt",
        "helia-aot",
        overrides={
            "allocate_arenas": False,
            "stream_evidence": True,
            "sweep_evidence": True,
            "arena_regions": regions,
        },
    )
    assert f'"{expected or "unknown"}");' in rendered
    failure = rendered.index("if (am_hal_cachectrl_dcache_invalidate(&hpx_range, true) != 0U)")
    error = rendered.index("HPX_ERROR=psram_cache_invalidation_failed:region=%d", failure)
    assert rendered.index("hpx_park();", error) < rendered.index("uint32_t infer_start_cyc", error)
    assert run_conditions(ctx).cache_regime == expected


def test_aot_target_and_user_constant_rule(tmp_path: Path) -> None:
    ctx = _ctx(
        tmp_path,
        engine="helia-aot",
        extra={
            "engine": {"config": {"platform_name": "x"}},
            "model": {"weights_location": "mram"},
        },
    )
    ctx.engine_artifacts = _aot_artifacts(tmp_path, target_name="x")
    assert run_conditions(ctx).target == "x"
    assert run_conditions(ctx).weights_memory == "mram"

    # The three spellings heliaAOT matches as a type-only constant rule.
    for rule in (
        {"type": "constant", "attributes": {"memory": "sram"}},
        {"type": "CONSTANT", "attributes": {"memory": "sram"}},
        {"type": "constant", "id": None, "attributes": {"memory": "sram"}},
    ):
        intent = {"tensors": [rule]}
        ctx.engine_artifacts = _aot_artifacts(tmp_path, aot_memory_intent_json=json.dumps(intent))
        assert run_conditions(ctx).weights_memory is None, rule

    specific = {"tensors": [{"type": "constant", "id": "0:w", "attributes": {"memory": "sram"}}]}
    ctx.engine_artifacts = _aot_artifacts(tmp_path, aot_memory_intent_json=json.dumps(specific))
    assert run_conditions(ctx).weights_memory == "mram"


def test_aot_conditions_require_prepared_target_evidence(tmp_path):
    ctx = _ctx(tmp_path, engine="helia-aot")
    ctx.engine_artifacts = dataclasses.replace(_aot_artifacts(tmp_path), aot_platform_name=None)
    with pytest.raises(EngineError, match="prepared compiler target"):
        run_conditions(ctx)


def test_aot_weights_memory_is_the_constant_rule_the_compiler_received(tmp_path: Path) -> None:
    """The rule, not the planner's region: Ethos-U moves auto-placed weights to MRAM."""
    auto = _ctx(tmp_path, engine="helia-aot", extra={"model": {"arena_size": 1024}})
    auto.engine_artifacts = _aot_artifacts(tmp_path)
    auto.weights_region = Placement.TCM
    assert run_conditions(auto).weights_memory == "dtcm"

    npu = _ctx(
        tmp_path,
        engine="helia-aot",
        extra={"model": {"arena_size": 1024}, "engine": {"backend": "ethos_u"}},
    )
    npu.engine_artifacts = _aot_artifacts(tmp_path)
    npu.weights_region = Placement.TCM
    assert run_conditions(npu).weights_memory == "mram"

    pinned = _ctx(
        tmp_path,
        engine="helia-aot",
        extra={
            "model": {"arena_size": 1024, "weights_location": "tcm"},
            "engine": {"backend": "ethos_u"},
        },
    )
    pinned.engine_artifacts = _aot_artifacts(tmp_path)
    assert run_conditions(pinned).weights_memory == "dtcm"

    # A user catch-all rule's staging attribute applies under hpx's rule.
    staged = _ctx(tmp_path, engine="helia-aot", extra={"model": {"weights_location": "mram"}})
    intent = {"tensors": [{"type": "*", "attributes": {"constant_destination_memory": "sram"}}]}
    staged.engine_artifacts = _aot_artifacts(tmp_path, aot_memory_intent_json=json.dumps(intent))
    assert run_conditions(staged).weights_memory == "sram"


def test_a_legacy_board_alias_records_the_aot_platform(tmp_path: Path) -> None:
    ctx = make_pmu_ctx(tmp_path, board="apollo5b_evb")
    set_profile_result(ctx, _pmu())
    assert run_conditions(ctx).target == "apollo510_evb"


def _publish(tmp_path: Path, ctx) -> Path:
    out = tmp_path / "out"
    lock = tmp_path / "nsx.lock"
    lock.write_bytes(b"schema_version: 4\ntargets: {}\n")
    ctx.dependency_lock_path = lock
    ctx.run_metadata.run_id = "run"
    ctx.run_metadata.timestamp = "2026-11-06T00:00:00+00:00"
    ctx.run_metadata.hpx_version = "0.0.0"
    ctx.config = dataclasses.replace(
        ctx.config, output=dataclasses.replace(ctx.config.output, dir=out)
    )
    write_report(ctx)
    return out


@pytest.mark.parametrize("cache_mode", ["on", "off"])
def test_every_artifact_records_the_same_conditions(tmp_path: Path, cache_mode: str) -> None:
    ctx = _ctx(tmp_path, measured_clock_hz=249_600_000)
    ctx.config = dataclasses.replace(
        ctx.config, profiling=dataclasses.replace(ctx.config.profiling, dcache=cache_mode)
    )
    set_profile_result(
        ctx,
        dataclasses.replace(
            ctx.captured_pmu, meta=dataclasses.replace(ctx.captured_pmu.meta, dcache=cache_mode)
        ),
    )
    out = _publish(tmp_path, ctx)

    expected = {
        "target": "apollo510_evb",
        "cpu_hz": ctx_platform_mhz(ctx) * 1_000_000,
        "weights_memory": "mram",
        "weights_cache": cache_mode,
        "application_load": "none",
    }
    if cache_mode == "on":
        expected["cache_regime"] = "warm"
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    metadata = json.loads((out / "run_metadata.json").read_text(encoding="utf-8"))
    assert summary["schema_version"] == RUN_SUMMARY_SCHEMA_VERSION == 23
    assert summary["conditions"] == expected
    assert list(summary["conditions"]) == list(expected)
    assert metadata["conditions"] == expected
    manifest = load_result_manifest(out / "result_manifest.json", verify=True)
    assert manifest.comparability["weights_memory"] == "mram"
    assert manifest.comparability["weights_cache"] == cache_mode
    assert manifest.comparability["external_memory_clock"] is None
    assert manifest.comparability["application_load"] == "none"
    assert ctx.run_metadata.conditions == Conditions.from_dict(summary["conditions"])
    assert RunSummary.from_dict(summary).conditions == expected


def test_the_aot_plan_and_report_are_pinned_into_the_bundle(tmp_path: Path) -> None:
    plan = tmp_path / "hpx_plan.json"
    plan.write_text('{"schema_version": 1}', encoding="utf-8")
    report = tmp_path / "hpx_report.json"
    report.write_text('{"schema_version": 1}', encoding="utf-8")
    ctx = _ctx(tmp_path, engine="helia-aot")
    ctx.engine_artifacts = _aot_artifacts(tmp_path, report_files=(plan, report))

    out = _publish(tmp_path, ctx)

    assert (out / "hpx_plan.json").read_text(encoding="utf-8") == '{"schema_version": 1}'
    manifest = load_result_manifest(out / "result_manifest.json", verify=True)
    named = {artifact.path: artifact for artifact in manifest.artifacts}
    assert named["hpx_plan.json"].name == "helia-aot.plan"
    assert named["hpx_plan.json"].producer == "helia-aot"
    assert named["hpx_report.json"].name == "helia-aot.report"


def test_round_trip_keeps_the_heliaoptimus_field_order() -> None:
    conditions = Conditions(target="apollo510_evb", cpu_hz=1, weights_memory="dtcm")
    assert list(conditions.to_dict()) == [
        "target",
        "cpu_hz",
        "weights_memory",
        "weights_cache",
        "application_load",
    ]
    with_psram = Conditions(target="t", external_memory_clock=48)
    assert list(with_psram.to_dict())[4] == "external_memory_clock"
    assert Conditions.from_dict(with_psram.to_dict()) == with_psram


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"target": ""},
        {"target": "t", "cpu_hz": "fast"},
        {"target": "t", "cpu_hz": True},
        {"target": "t", "weights_memory": 1},
        {"target": "t", "external_memory_clock": 48.0},
        {"target": "t", "weights_cache": None},
    ],
)
def test_a_malformed_block_is_refused(data: dict) -> None:
    with pytest.raises(ValueError, match="conditions\\."):
        Conditions.from_dict(data)


def _run(tmp_path: Path, conditions: dict | None) -> RunArtifacts:
    summary: dict = {"schema_version": RUN_SUMMARY_SCHEMA_VERSION}
    if conditions is not None:
        summary["conditions"] = conditions
    return RunArtifacts(
        path=tmp_path,
        summary=summary,
        metadata={"hpx_version": "0.0.0", "schema_version": 1},
        layers=[],
    )


def test_a_differing_weights_memory_is_a_different_measurement(tmp_path: Path) -> None:
    base = {"target": "apollo510_evb", "weights_memory": "dtcm", "weights_cache": "on"}
    other = {**base, "weights_memory": "mram"}

    assessment = assess_comparability(_run(tmp_path, base), _run(tmp_path, other))

    (issue,) = assessment.issues
    assert issue.code == "dimension.weights_memory_differs"
    assert issue.context == {"baseline": "dtcm", "candidate": "mram"}
    assert "different memory path" in issue.message
    assert assess_comparability(_run(tmp_path, base), _run(tmp_path, base)).issues == ()
    assert assess_comparability(_run(tmp_path, base), _run(tmp_path, None)).issues == ()


def test_every_recorded_condition_but_the_clock_is_a_dimension(tmp_path: Path) -> None:
    base = {
        "target": "apollo510_evb",
        "cpu_hz": 250_000_000,
        "weights_memory": "dtcm",
        "weights_cache": "on",
        "external_memory_clock": 48,
        "application_load": "none",
    }
    other = {
        **base,
        "cpu_hz": 96_000_000,
        "weights_cache": "off",
        "external_memory_clock": 96,
        "application_load": "ble",
    }

    assessment = assess_comparability(_run(tmp_path, base), _run(tmp_path, other))

    assert [issue.code for issue in assessment.issues] == [
        "dimension.weights_cache_differs",
        "dimension.external_memory_clock_differs",
        "dimension.application_load_differs",
    ]
    assert assessment.run_metrics_comparable


@pytest.mark.parametrize(
    "rule",
    [
        {"type": "constant", "attributes": {"memory": "sram"}},
        {"type": "CONSTANT", "attributes": {"memory": "sram"}},
        {"type": "constant", "id": None, "attributes": {"memory": "sram"}},
        {"type": "constant", "id": "0:w", "attributes": {"memory": "sram"}},
        {"type": "constant", "id": ["0:w"], "attributes": {"memory": "sram"}},
        {"type": "scratch", "attributes": {"memory": "sram"}},
    ],
)
def test_the_wildcard_rule_matches_as_heliaaot_matches(rule: dict) -> None:
    """hpx drops its constant rule exactly when heliaAOT would let the user's rule win it."""
    from helia_profiler.engines.helia_aot.compile import (
        _merge_aot_tensor_rulesets,
        user_wildcard_kinds,
    )

    attributes = pytest.importorskip("helia_aot.attributes.attributes")
    defines = pytest.importorskip("helia_aot.attributes.defines")
    hpx_rule = {"type": "constant", "attributes": {"memory": "dtcm"}}
    merged = [defines.AttributeRuleset(**r) for r in _merge_aot_tensor_rulesets([hpx_rule], [rule])]
    # heliaAOT picks the user's rule for an unnamed constant exactly when the rule is a
    # type-only match; hpx must record no weights memory in exactly that case.
    winner = attributes.winning_rule_index(merged, "constant", "t0", "memory")
    user_wins_everywhere = merged[winner].attributes["memory"] == "sram" and rule.get("id") is None
    assert ("constant" in user_wildcard_kinds([rule])) == user_wins_everywhere
