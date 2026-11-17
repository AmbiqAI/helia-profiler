"""The installed compiler receives the selected catalog target."""

from pathlib import Path

import pytest
import yaml

pytest.importorskip("helia_aot")

from helia_profiler.config import load_config  # noqa: E402
from helia_profiler.engines.helia_aot.compile import (  # noqa: E402
    _compiler_target,
    _merged_aot_args,
    _resolve_aot_platform,  # noqa: E402
    _run_aot_compiler,
    effective_aot_args,
    effective_config_digest,
    effective_config_pairing_digest,
    without_knob_values,
)
from helia_profiler.engines.helia_aot.target import resolve_target, verify_conversion  # noqa: E402
from helia_profiler.errors import EngineError  # noqa: E402


def config(board="apollo510_evb", *, overlay=None, platform=None, override=None):
    target = {"board": board}
    if overlay is not None:
        target["platform_overlay"] = overlay
    engine = {}
    if platform is not None:
        engine["aot_args"] = {"platform": platform}
    if override is not None:
        engine["platform_name"] = override
    return load_config(
        None,
        {
            "model": {"path": str(Path(__file__).parent / "fixtures/kws_ref_model.tflite")},
            "target": target,
            "engine": {"type": "helia-aot", "config": engine},
        },
    )


@pytest.mark.parametrize("board", ["atomiq110_fpga_turbo", "apollo3p_evb_cygnus"])
def test_default_compiler_target_keeps_catalog_selection(board):
    assert _resolve_aot_platform(config(board)) == board


def test_overlay_board_needs_no_compatibility_alias(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text(
        yaml.safe_dump({"schema_version": 1, "boards": {"lab": {"based_on": "apollo510_evb"}}})
    )
    assert _resolve_aot_platform(config("lab", overlay=path)) == "lab"


def resolved(cfg):
    return resolve_target(_compiler_target(cfg, _merged_aot_args(cfg)))


@pytest.mark.parametrize("board", ["apollo510_evb", "atomiq110_fpga_turbo", "apollo3p_evb_cygnus"])
def test_installed_projection_retains_selection(board):
    cfg = config(board)
    target = resolved(cfg)
    record = target.record()
    selected = cfg.platform_registry.selections[board]
    assert record["schema"] == 1
    assert record["target"] == selected.target_id
    assert record["boot"] == selected.boot
    assert record["addons"] == list(selected.addons)
    assert record["catalog"]["catalog_digest"] == selected.catalog.catalog_digest


@pytest.mark.parametrize("platform", [{"facts": "registered"}, {"min_alignment": 32}])
def test_explicit_platform_declaration_keeps_compatibility(platform):
    target = resolved(config("atomiq110_fpga_turbo", platform=platform))
    assert target.name == "atomiq110" and target.record() is None


def test_explicit_named_override_wins_without_claiming_same_target():
    target = resolved(config("atomiq110_fpga_turbo", override="apollo510_evb"))
    assert target.name == "apollo510_evb" and target.record() is None


def test_explicit_catalog_override_records_a_separate_compiler_target():
    target = resolved(
        config("atomiq110_fpga_turbo", platform={"facts": "catalog"}, override="apollo510_evb")
    )
    assert target.record()["target"] == "apollo510_evb"
    assert target.record()["boot"] == "sbl"


@pytest.mark.parametrize("runtime", ["0.33.0", "1.6.0"])
def test_older_ordinary_route_and_explicit_catalog_refusal(monkeypatch, runtime):
    monkeypatch.setattr(
        "helia_profiler.engines.helia_aot.target.installed_version", lambda: runtime
    )
    assert _resolve_aot_platform(config("atomiq110_fpga_turbo")) == "atomiq110"
    with pytest.raises(EngineError, match="catalog-platform contract"):
        resolved(config(platform={"facts": "catalog"}))


def test_future_experimental_minor_refuses_without_hiding_named_route(monkeypatch):
    monkeypatch.setattr(
        "helia_profiler.engines.helia_aot.target.installed_version", lambda: "1.8.0"
    )
    with pytest.raises(EngineError, match="catalog-platform contract"):
        resolved(config())
    assert resolved(config(platform={"facts": "registered"})).record() is None


def test_sdk_only_target_keeps_named_compatibility():
    target = resolved(config("apollo5b_evb"))
    assert target.name == "apollo510_evb" and target.record() is None


@pytest.mark.parametrize(
    "platform", [{"facts": "catalog", "boot": "nbl"}, {"facts": "catalog", "addons": ["cygnus"]}]
)
def test_conflicting_physical_selection_refuses(platform):
    with pytest.raises(EngineError, match="frozen HPX target"):
        resolved(config(platform=platform))


def test_reservation_is_applied_once_and_retained():
    normal = resolved(config()).record()
    limited = resolved(config(platform={"facts": "catalog", "reserve": {"dtcm": 16384}})).record()
    dtcm = next(m for m in limited["memories"] if m["name"] == "DTCM")
    physical = next(m["bytes"] for m in normal["memories"] if m["name"] == "DTCM")
    assert dtcm["bytes"] == physical - 16384
    assert dtcm["derived_bytes"] == physical and dtcm["reserved"] == 16384


@pytest.mark.parametrize(
    "fields",
    [{"reserve": {"dtcm": 2**30}}, {"memories": {"dtcm": 1024}}, {"capabilities": ["mve"]}],
)
def test_invalid_budget_or_capability_requests_refuse(fields):
    with pytest.raises(EngineError):
        resolved(config(platform={"facts": "catalog", **fields}))


def native_overlay(path, *, capacity="384K", mve="int"):
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "socs": {
                    "lab_chip": {
                        "based_on": "apollo510",
                        "memories": {
                            "dtcm": {
                                "technology": "sram",
                                "attachment": "tcm",
                                "base": 0x20000000,
                                "size": capacity,
                            }
                        },
                        "cores": [
                            {
                                "id": "cm55_0",
                                "role": "application",
                                "core": "cortex-m55",
                                "clock_domain": "cpu",
                                "programmable_by": "customer",
                                "options": {
                                    "mve": mve,
                                    "fpu": "fpv5-sp-d16",
                                    "icache_kb": 64,
                                    "dcache_kb": 64,
                                },
                            }
                        ],
                    }
                },
                "parts": {"lab_part": {"based_on": "AP510NFA-CBR", "soc": "lab_chip"}},
                "boards": {"lab": {"based_on": "apollo510_evb", "part": "lab_part"}},
            }
        )
    )
    return path


def test_overlay_facts_change_compiler_record_and_both_digests(tmp_path):
    path = native_overlay(tmp_path / "catalog.yaml")
    first = config("lab", overlay=path)
    before = resolved(first)
    record = before.record()
    assert "MVE" in record["capabilities"] and not record["supports_fp16"]
    assert next(m["bytes"] for m in record["memories"] if m["name"] == "DTCM") == 384 * 1024

    def digests(cfg):
        args = effective_aot_args(cfg)
        return tuple(
            f(args, module_name="m", prefix="p", aot_platform="lab")[0]
            for f in (effective_config_digest, effective_config_pairing_digest)
        )

    old_digests = digests(first)
    native_overlay(path, capacity="512K", mve="fp")
    with pytest.raises(EngineError, match="changed after target resolution"):
        resolved(first)
    second = config("lab", overlay=path)
    assert resolved(second).record()["supports_fp16"]
    assert all(a != b for a, b in zip(old_digests, digests(second)))


@pytest.mark.parametrize(
    "field,value", [("schema", 2), ("schema", True), ("target", "another-board"), ("boot", "nbl")]
)
def test_wrong_public_record_refuses(monkeypatch, field, value):
    import helia_aot.platforms as platforms

    original = platforms.platform_record
    monkeypatch.setattr(platforms, "platform_record", lambda p: {**original(p), field: value})
    with pytest.raises(EngineError):
        resolved(config())


def test_unknown_used_catalog_source_refuses(monkeypatch):
    import helia_aot.platforms as platforms

    original = platforms.platform_record

    def altered(platform):
        record = original(platform)
        record["catalog"]["sources"].append(
            {"label": "env:unknown.yaml", "schema_version": 1, "sha256": "a" * 64}
        )
        return record

    monkeypatch.setattr(platforms, "platform_record", altered)
    with pytest.raises(EngineError, match="unknown or changed catalog source"):
        resolved(config())


@pytest.fixture
def conversion(tmp_path):
    cfg = config()
    target = resolved(cfg)
    result = _run_aot_compiler(
        cfg, tmp_path, "hpx_model", "hpx", target.name, effective_aot_args(cfg), target=target
    )
    return cfg, target, result


def test_actual_conversion_keeps_the_preflight_record(conversion):
    _, target, result = conversion
    verify_conversion(target, result)
    assert result.plan.platform.facts == "catalog"


@pytest.mark.parametrize(
    "field,value",
    [("name", "another-board"), ("min_alignment", 32), ("boot", "nbl"), ("capabilities", ["MCU"])],
)
def test_changed_actual_conversion_plan_is_refused(conversion, field, value):
    from types import SimpleNamespace

    _, target, result = conversion
    platform = result.plan.platform.model_copy(update={field: value})
    altered = result.plan.model_copy(update={"platform": platform})
    with pytest.raises(EngineError, match="conversion plan differs"):
        verify_conversion(target, SimpleNamespace(plan=altered))


def test_saved_argument_normalization_does_not_read_overlay(tmp_path):
    path = tmp_path / "absent.yaml"
    normalized = without_knob_values(
        {"platform": {"name": "lab", "facts": "catalog", "overlay": str(path)}}
    )
    assert normalized["platform"]["name"] == "lab"
    assert not path.exists()


def test_session_analysis_uses_the_same_overlay_as_prepare(tmp_path, monkeypatch):
    import helia_aot

    from helia_profiler import Session
    from helia_profiler.engines.helia_aot.adapter import HeliaAOTAdapter

    path = native_overlay(tmp_path / "catalog.yaml", mve="fp")
    cfg = config("lab", overlay=path)
    seen = []
    original = helia_aot.AotConverter

    def compiler(*, config):
        seen.append(config.platform.model_dump(mode="json"))
        return original(config=config)

    monkeypatch.setattr(helia_aot, "AotConverter", compiler)
    session = Session.from_dict(
        {
            "model": {"path": str(cfg.model.path)},
            "target": {"board": "lab", "platform_overlay": str(path)},
            "engine": {"type": "helia-aot"},
        }
    )
    analysis = session.analyze()
    artifacts = HeliaAOTAdapter().prepare(session.resolve(), tmp_path / "prepare")
    assert artifacts.compiled_analysis is not None
    assert analysis.total_macs == artifacts.compiled_analysis.total_macs
    assert analysis.total_ops == artifacts.compiled_analysis.total_ops
    assert seen[0] == seen[1]
    assert seen[0]["name"] == "lab" and seen[0]["facts"] == "catalog"
    assert any(p.name == "hpx_platform.json" for p in artifacts.report_files)


def test_omitted_overlay_identity_is_not_accepted(tmp_path, monkeypatch):
    import helia_aot.platforms as platforms

    path = native_overlay(tmp_path / "catalog.yaml")
    original = platforms.platform_record

    def changed(platform):
        record = original(platform)
        record["catalog"]["sources"] = []
        return record

    monkeypatch.setattr(platforms, "platform_record", changed)
    with pytest.raises(EngineError, match="omits the selected overlay"):
        resolved(config("lab", overlay=path))


def test_non_primary_instance_cannot_be_silently_replaced():
    from dataclasses import replace
    from types import MappingProxyType

    cfg = config()
    selections = dict(cfg.platform_registry.selections)
    selections[cfg.target.board] = replace(selections[cfg.target.board], core="another-instance")
    altered = replace(
        cfg,
        platform_registry=replace(cfg.platform_registry, selections=MappingProxyType(selections)),
    )
    with pytest.raises(EngineError, match="core/implementation"):
        resolved(altered)


def test_analysis_context_mismatch_refuses():
    from helia_profiler.errors import ConfigError
    from helia_profiler.evaluation.engine_analysis import analyze_for_engine

    cfg = config()
    with pytest.raises(ConfigError, match="Analysis arguments differ"):
        analyze_for_engine(cfg.model.path, engine="helia-aot", board="apollo510b_evb", config=cfg)


@pytest.mark.parametrize("remove", [False, True])
def test_report_retains_prepared_target_after_compiler_yaml_changes(tmp_path, remove):
    from helia_profiler.engines.helia_aot.adapter import HeliaAOTAdapter
    from helia_profiler.pipeline import PipelineContext
    from helia_profiler.report.conditions import run_conditions
    from helia_profiler.results import FirmwareMeta, PmuResult
    from tests.pipeline_context_helpers import set_profile_result

    path = tmp_path / "compiler.yaml"
    path.write_text("{}\n")
    cfg = load_config(
        None,
        {
            "model": {"path": str(config().model.path)},
            "target": {"board": "atomiq110_fpga_turbo"},
            "engine": {"type": "helia-aot", "config_path": str(path)},
        },
    )
    ctx = PipelineContext(config=cfg, work_dir=tmp_path / "work")
    ctx.engine_artifacts = HeliaAOTAdapter().prepare(cfg, ctx.work_dir)
    set_profile_result(ctx, PmuResult(meta=FirmwareMeta()))
    if remove:
        path.unlink()
    else:
        path.write_text("platform: {facts: registered}\n")
    assert run_conditions(ctx).target == "atomiq110_fpga_turbo"


def test_explicit_catalog_override_works_for_sdk_only_physical_target():
    target = resolved(
        config("apollo5b_evb", platform={"facts": "catalog"}, override="apollo510_evb")
    )
    assert target.record()["target"] == "apollo510_evb"
