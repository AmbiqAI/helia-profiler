"""FrozenRun: resolved settings captured as a replayable, hash-identified plan."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import helia_profiler as hpx
from helia_profiler.errors import ConfigError
from helia_profiler.frozen_run import FrozenRun


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> hpx.Session:
    monkeypatch.setattr(
        "helia_profiler.hostenv.toolchain_probe.compiler_version",
        lambda toolchain, *, timeout_s: f"{toolchain} 15.2.1",
    )
    model = tmp_path / "model.tflite"
    model.write_bytes(b"TFL3" + bytes(64))
    return (
        hpx.Session()
        .with_model(model)
        .with_engine("helia-rt")
        .with_target(board="apollo510_evb", toolchain="arm-none-eabi-gcc")
    )


def test_a_saved_plan_reloads_and_resolves_to_the_same_config(session, tmp_path):
    plan = session.freeze()
    loaded = FrozenRun.load(plan.save(tmp_path / "plans" / "run.json"))

    assert loaded == plan and loaded.intent_hash == plan.intent_hash
    assert loaded.config() == session.resolve()
    assert plan.toolchain == hpx.ToolchainIdentity("arm-none-eabi-gcc", "arm-none-eabi-gcc 15.2.1")
    assert plan.runtime is not None and plan.runtime.name == "helia-rt"
    assert plan.runtime.source_commit and plan.runtime.unavailable_reason is None


def test_intent_hash_ignores_host_local_settings_and_tracks_real_ones(session):
    plan = session.freeze()
    local = session.with_target(jlink_serial="1160002255").with_output(dir="/elsewhere")

    assert local.freeze().intent_hash == plan.intent_hash
    assert session.with_profiling(iterations=7).freeze().intent_hash != plan.intent_hash


def test_intent_hash_follows_model_content_not_its_path(session, tmp_path):
    plan = session.freeze()
    moved = tmp_path / "moved.tflite"
    moved.write_bytes(Path(plan.model.path).read_bytes())

    assert session.freeze(moved).intent_hash == plan.intent_hash
    assert plan.config(model=moved).model.path == moved


def test_a_changed_model_is_refused_on_replay(session):
    plan = session.freeze()
    Path(plan.model.path).write_bytes(b"TFL3" + bytes(65))

    with pytest.raises(ConfigError, match="does not match the frozen model"):
        plan.config()


def test_a_plan_from_another_baseline_is_refused(session, monkeypatch):
    from dataclasses import replace

    plan = replace(session.freeze(), baseline_fingerprint="0" * 64)
    with pytest.raises(ConfigError, match="frozen against baseline"):
        plan.config()


def test_an_edited_plan_file_is_refused(session, tmp_path):
    path = session.freeze().save(tmp_path / "run.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["settings"]["profiling"]["iterations"] = 99
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ConfigError, match="intent hash does not match"):
        FrozenRun.load(path)


def test_an_unprobeable_compiler_is_recorded_with_its_reason(session, monkeypatch):
    def missing(toolchain, *, timeout_s):
        raise FileNotFoundError("arm-none-eabi-gcc")

    monkeypatch.setattr("helia_profiler.hostenv.toolchain_probe.compiler_version", missing)
    toolchain = session.freeze().toolchain

    assert toolchain.version is None
    assert toolchain.unavailable_reason and "compiler probe failed" in toolchain.unavailable_reason


def test_the_hpx_release_is_recorded_but_not_part_of_the_intent(session):
    from dataclasses import replace

    plan = session.freeze()
    assert plan.hpx_version == hpx.__version__
    assert replace(plan, hpx_version="9.9.9").intent_hash == plan.intent_hash
