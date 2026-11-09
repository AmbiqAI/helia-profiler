"""Explicit catalog inputs, legacy precedence, and frozen-source boundaries."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

import helia_profiler as hpx
from helia_profiler.config import load_config
from helia_profiler.config.platform import config_platform
from helia_profiler.errors import ConfigError, EngineError
from helia_profiler.platform import build_platform_registry, get_soc_for_board
from helia_profiler.runs.plan import FrozenRun, freeze_config
from tests.runs.helpers import ATFE_OUTPUT


@pytest.fixture(autouse=True)
def compiler(monkeypatch):
    monkeypatch.setattr("helia_profiler.hostenv._proc.tool_output", lambda *a, **k: ATFE_OUTPUT)
    from helia_profiler.runs.plan import _ENVIRONMENT_VARIABLES, _SOURCE_VARIABLES

    for key in (*_ENVIRONMENT_VARIABLES, *_SOURCE_VARIABLES):
        monkeypatch.delenv(key, raising=False)


def _overlay(tmp_path, **sections):
    path = tmp_path / "platform.yaml"
    document = {"schema_version": 1, **sections}
    if not sections:
        document["boards"] = {"lab": {"based_on": "apollo510_evb"}}
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def _config(path, tmp_path, board="lab", **target):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"TFL3" + bytes(64))
    return load_config(
        None,
        {
            "model": {"path": str(model)},
            "target": {"board": board, "platform_overlay": path, "toolchain": "atfe", **target},
        },
    )


def test_native_board_uses_catalog_facts_and_separate_hpx_policy(tmp_path):
    path = _overlay(tmp_path)
    before = build_platform_registry()
    cfg = _config(path, tmp_path)
    snapshot = config_platform(cfg)
    assert snapshot.board.name == "lab"
    assert snapshot.board.profile_source_board == "apollo510_evb"
    assert snapshot.board.default_sync_gpio_pin == 29
    assert snapshot.soc.memory.dtcm_kb == 512
    assert snapshot.load_address == 0x00410000
    assert not snapshot.soc.is_builtin and snapshot.expected_linker is None
    assert snapshot.selection.origin == "hpx-custom"
    assert snapshot.selection.target_id == "lab"
    assert snapshot.selection.catalog is not None
    assert snapshot.selection.catalog.sources[0].label == "overlay:platform.yaml"
    assert (
        cfg.platform_registry.overlay_source.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    )
    assert "lab" not in before.boards and build_platform_registry() == before


def test_board_and_soc_lineages_are_resolved_independently(tmp_path):
    path = _overlay(
        tmp_path,
        socs={"lab_chip": {"based_on": "apollo510"}},
        parts={"lab_part": {"based_on": "AP510NFA-CBR", "soc": "lab_chip"}},
        boards={"lab": {"based_on": "apollo510_evb", "part": "lab_part"}},
    )
    cfg = _config(path, tmp_path)
    snapshot = config_platform(cfg)
    assert snapshot.soc.name == "lab_chip"
    assert snapshot.soc.memory_bases_like == "apollo510"
    assert snapshot.board.soc == "lab_chip" and snapshot.board.default_sync_gpio_pin == 29
    assert snapshot.expected_linker is None


def test_overlay_composition_with_builtin_leaves_is_still_custom(tmp_path):
    path = _overlay(tmp_path, compositions={"lab": {"board": "apollo3p_evb", "addons": ["cygnus"]}})
    snapshot = config_platform(_config(path, tmp_path))
    assert snapshot.selection.target_id == "apollo3p_evb"
    assert snapshot.selection.addons == ("cygnus",)
    assert snapshot.soc.memory.psram_kb == 8192
    assert snapshot.board.profile_source_board == "apollo3p_evb"
    assert not snapshot.soc.is_builtin and snapshot.expected_linker is None


def test_legacy_declarations_apply_after_native_fact_resolution(tmp_path):
    path = _overlay(tmp_path)
    cfg = _config(
        path,
        tmp_path,
        board="child",
        custom_boards={
            "child": {"based_on": "lab", "default_sync_gpio_pin": 7},
        },
    )
    snapshot = config_platform(cfg)
    assert snapshot.board.default_sync_gpio_pin == cfg.power.sync_gpio_pin == 7
    assert snapshot.board.profile_source_board == "apollo510_evb"
    assert snapshot.selection.origin == "hpx-custom"
    assert snapshot.selection.catalog is not None
    assert snapshot.selection.catalog.sources
    assert snapshot.load_address == 0x00410000


def test_legacy_custom_soc_can_reference_native_soc(tmp_path):
    path = _overlay(tmp_path, socs={"lab_chip": {"based_on": "apollo510"}})
    cfg = _config(
        path,
        tmp_path,
        board="child",
        custom_socs={
            "custom_chip": {"based_on": "lab_chip", "jlink_device": "ExplicitProbe"},
        },
        custom_boards={
            "child": {"based_on": "apollo510_evb", "soc": "custom_chip"},
        },
    )
    assert (
        get_soc_for_board("child", registry=cfg.platform_registry).jlink_device == "ExplicitProbe"
    )
    assert config_platform(cfg).expected_linker is None


@pytest.mark.parametrize("entrypoint", ["load", "yaml-session", "dict-session", "saved-session"])
def test_paths_are_anchored_when_resolved_from_the_working_directory(
    tmp_path, monkeypatch, entrypoint
):
    work = tmp_path / "work"
    work.mkdir()
    _overlay(work)
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    intent = {
        "model": {"path": "model.tflite"},
        "target": {"board": "lab", "platform_overlay": "platform.yaml"},
    }
    yaml_path = config_dir / "profile.yaml"
    yaml_path.write_text(yaml.safe_dump(intent))
    monkeypatch.chdir(work)
    if entrypoint == "load":
        cfg = load_config(yaml_path, {})
    elif entrypoint == "yaml-session":
        cfg = hpx.Session.from_yaml(yaml_path).resolve()
    elif entrypoint == "dict-session":
        cfg = hpx.Session.from_dict(intent).resolve()
    else:
        saved = hpx.Session.from_dict(intent).save(config_dir / "session.json")
        cfg = hpx.Session.load(saved).resolve()
    assert cfg.target.platform_overlay == work / "platform.yaml"
    assert cfg.platform_registry.overlay_source is not None
    assert cfg.platform_registry.overlay_source.path == cfg.target.platform_overlay


@pytest.mark.parametrize("change", ["edit", "delete"])
def test_resolved_and_saved_facts_do_not_reopen_overlay_but_replay_checks_it(tmp_path, change):
    path = _overlay(tmp_path)
    cfg = _config(path, tmp_path)
    expected = config_platform(cfg)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if change == "edit":
        path.write_text("invalid replacement")
    else:
        path.unlink()
    plan = freeze_config(cfg)
    assert config_platform(cfg) == expected
    assert plan.local_inputs[-1].sha256 == digest
    loaded = FrozenRun.load(plan.save(tmp_path / "plan.json"))
    assert loaded.platform == expected and loaded.intent_hash == plan.intent_hash
    with pytest.raises(ConfigError, match="missing or differs"):
        loaded.config()


def test_unchanged_overlay_roundtrips_with_same_intent_and_resolved_config(tmp_path):
    cfg = _config(_overlay(tmp_path), tmp_path)
    plan = freeze_config(cfg)
    loaded = FrozenRun.load(plan.save(tmp_path / "plan.json"))
    assert loaded.config() == cfg
    assert loaded.intent_hash == freeze_config(loaded.config()).intent_hash


def test_new_absent_field_does_not_invalidate_retained_builtin_plan(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    build = json.loads((root / "examples/results/run-api-synthetic/build.json").read_text())
    old = FrozenRun.from_dict(build["plan"])
    assert "platform_overlay" not in old.settings["target"]
    assert old.runtime is not None and old.runtime.name == "helia-aot"
    source = {"version": old.runtime.version, "commit": old.runtime.source_commit}
    monkeypatch.setattr("helia_profiler.results.dependencies.installed_aot_source", lambda: source)
    model = root / "examples/results/run-api-synthetic/inputs/tiny_cnn.tflite"
    cfg = old.config(model=model)
    assert cfg.target.platform_overlay is None
    assert freeze_config(cfg).intent_hash == old.intent_hash


def test_two_overlay_configs_do_not_mutate_each_other(tmp_path):
    path = _overlay(tmp_path)
    first = _config(path, tmp_path)
    path.write_text(path.read_text() + "# a new source revision\n")
    second = _config(path, tmp_path)
    assert first.platform_registry.overlay_source != second.platform_registry.overlay_source
    assert freeze_config(first).intent_hash != freeze_config(second).intent_hash
    assert config_platform(first).soc == config_platform(second).soc


@pytest.mark.parametrize("invalid", ["missing", "bad-yaml", "builtin-shadow", "not-path"])
def test_overlay_errors_are_config_diagnostics(tmp_path, invalid):
    path = _overlay(tmp_path)
    if invalid == "missing":
        path.unlink()
    elif invalid == "bad-yaml":
        path.write_text("[unfinished")
    elif invalid == "builtin-shadow":
        path.write_text(
            "schema_version: 1\nboards:\n  apollo510_evb:\n    based_on: apollo510b_evb\n"
        )
    else:
        path = 123
    with pytest.raises(ConfigError, match="platform_overlay"):
        _config(path, tmp_path)


def test_direct_config_cannot_silently_ignore_an_overlay_path(tmp_path):
    cfg = _config(_overlay(tmp_path), tmp_path)
    with pytest.raises(ConfigError, match="resolved platform registry"):
        replace(cfg, platform_registry=build_platform_registry())


def test_aot_native_route_still_requires_explicit_supported_override(tmp_path):
    from helia_profiler.engines.helia_aot.compile import _resolve_aot_platform

    cfg = _config(_overlay(tmp_path), tmp_path)
    with pytest.raises(EngineError, match="platform mapping"):
        _resolve_aot_platform(cfg)
    cfg = replace(cfg, engine=replace(cfg.engine, config={"platform_name": "apollo510_evb"}))
    assert _resolve_aot_platform(cfg) == "apollo510_evb"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {
                "memories": {
                    "dtcm": {
                        "technology": "sram",
                        "attachment": "tcm",
                        "base": 0x21000000,
                        "size": "512K",
                    }
                }
            },
            "geometry",
        ),
        (
            {
                "memories": {
                    "dtcm": {
                        "technology": "sram",
                        "attachment": "tcm",
                        "base": 0x20000000,
                        "size": 513,
                    }
                }
            },
            "whole KiB",
        ),
        ({"sdk": {"trace_clock_mhz": 48}}, "trace clock"),
        ({"clock_domains": {"cpu": {"lp": {"mhz": 120}}}}, "CPU clocks"),
    ],
)
def test_unsupported_native_projection_refuses_instead_of_borrowing_policy(
    tmp_path, changes, message
):
    path = _overlay(
        tmp_path,
        socs={"lab_chip": {"based_on": "apollo510", **changes}},
        parts={"lab_part": {"based_on": "AP510NFA-CBR", "soc": "lab_chip"}},
        boards={"lab": {"based_on": "apollo510_evb", "part": "lab_part"}},
    )
    with pytest.raises(ConfigError, match=message):
        _config(path, tmp_path)


def test_overlay_file_change_during_resolution_refuses(tmp_path, monkeypatch):
    import helia_profiler.platform.overlay as adapter

    path = _overlay(tmp_path)
    original = adapter.load_overlay

    def changing(source, **kwargs):
        catalog = original(source, **kwargs)
        path.write_text(path.read_text() + "\n# changed\n")
        return catalog

    monkeypatch.setattr(adapter, "load_overlay", changing)
    with pytest.raises(ConfigError, match="changed while"):
        _config(path, tmp_path)


def test_builtin_name_with_explicit_overlay_cannot_enter_fixed_fixture(tmp_path):
    from helia_profiler.fixtures.build import _validate
    from tests.fixtures_api.test_fixed_fixture import fixture

    overlay_cfg = _config(_overlay(tmp_path), tmp_path, board="apollo510_evb")
    cfg, pinned = fixture(tmp_path)
    cfg = replace(
        cfg,
        target=replace(cfg.target, platform_overlay=overlay_cfg.target.platform_overlay),
        platform_registry=overlay_cfg.platform_registry,
    )
    with pytest.raises(ConfigError, match="custom target declarations"):
        _validate(cfg, pinned)


def test_native_resolution_reuses_nsx_profile_and_retains_requested_identity(tmp_path, monkeypatch):
    from helia_profiler.firmware.project import _get_starter_profile, _resolve_module_specs
    from helia_profiler.pipeline import PipelineContext
    from helia_profiler.stages.resolve_platform import ResolvePlatformStage
    from tests.test_firmware import _fake_starter_profiles

    monkeypatch.setattr(
        "helia_profiler.firmware.project.nsx_cli.starter_profile",
        lambda board: _fake_starter_profiles().get(board),
    )
    cfg = _config(_overlay(tmp_path), tmp_path)
    ctx = PipelineContext(config=cfg, work_dir=tmp_path / "work")
    ResolvePlatformStage().run(ctx)
    assert ctx.resolved_board.name == "lab"
    assert ctx.resolved_soc.name == "apollo510"
    profile = _get_starter_profile("lab", profile_board=ctx.resolved_board.profile_source_board)
    assert profile == _fake_starter_profiles()["apollo510_evb"]
    specs = _resolve_module_specs(
        "lab", profile_board=ctx.resolved_board.profile_source_board, registry=cfg.platform_registry
    )
    assert any(spec.name == "nsx-board-apollo510-evb" for spec in specs)


def test_native_board_can_receive_same_name_legacy_policy_override(tmp_path):
    cfg = _config(
        _overlay(tmp_path),
        tmp_path,
        custom_boards={"lab": {"based_on": "lab", "default_sync_gpio_pin": 27}},
    )
    snap = config_platform(cfg)
    assert snap.board.name == "lab" and snap.board.default_sync_gpio_pin == 27
    assert snap.board.profile_source_board == "apollo510_evb"
    assert not snap.soc.is_builtin and snap.expected_linker is None


@pytest.mark.parametrize(
    "soc,board",
    [("apollo510", "apollo510_evb"), ("apollo3p", "apollo3p_evb"), ("apollo5b", "apollo5b_evb")],
)
def test_unrelated_overlay_preserves_legacy_builtin_bases(tmp_path, soc, board):
    path = _overlay(tmp_path)
    declarations = {
        "custom_socs": {"child_chip": {"based_on": soc}},
        "custom_boards": {"child": {"based_on": board, "soc": "child_chip"}},
    }
    native = _config(path, tmp_path, board="child", **declarations)
    plain = load_config(
        None,
        {
            "model": {"path": native.model.path},
            "target": {"board": "child", "toolchain": "atfe", **declarations},
        },
    )
    first, second = config_platform(native), config_platform(plain)
    assert first.soc == second.soc and first.board == second.board
    assert first.soc.jlink_device
    assert first.soc.capabilities.memory == second.soc.capabilities.memory


@pytest.mark.parametrize("board", ["apollo510_evb", "apollo3p_evb", "apollo5b_evb"])
def test_unrelated_overlay_preserves_legacy_board_only_base(tmp_path, board):
    custom = {"child": {"based_on": board}}
    native = _config(_overlay(tmp_path), tmp_path, board="child", custom_boards=custom)
    plain = load_config(
        None,
        {
            "model": {"path": native.model.path},
            "target": {"board": "child", "toolchain": "atfe", "custom_boards": custom},
        },
    )
    assert config_platform(native).soc == config_platform(plain).soc
    assert config_platform(native).board == config_platform(plain).board


@pytest.mark.parametrize("override,expected", [(None, 0), (0, 0), (4096, 4096)])
def test_overlay_composition_keeps_board_capacity_separate_from_soc_default(
    tmp_path, override, expected
):
    path = _overlay(tmp_path, compositions={"lab": {"board": "apollo3p_evb", "addons": ["cygnus"]}})
    cfg = _config(
        path,
        tmp_path,
        board="child",
        custom_boards={
            "inherited": {"based_on": "lab"},
            "child": {"based_on": "inherited", "psram_kb": override},
        },
    )
    assert get_soc_for_board("lab", registry=cfg.platform_registry).memory.psram_kb == 8192
    assert get_soc_for_board("inherited", registry=cfg.platform_registry).memory.psram_kb == 8192
    snap = config_platform(cfg)
    assert snap.soc.memory.psram_kb == expected
    assert not snap.soc.is_builtin and snap.expected_linker is None
