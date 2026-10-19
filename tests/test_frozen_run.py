"""FrozenRun: resolved settings captured as a replayable, hash-identified plan."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

import helia_profiler as hpx
from helia_profiler.errors import ConfigError
from helia_profiler.frozen_run import FrozenRun

_GCC = "arm-none-eabi-gcc (Arm GNU Toolchain 15.2.Rel1) 15.2.1 20251203\nCopyright (C) 2025\n"


@pytest.fixture(autouse=True)
def compiler(monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:
    reply: dict[str, str | None] = {"output": _GCC}
    monkeypatch.setattr(
        "helia_profiler.hostenv._proc.tool_output",
        lambda command, *, timeout_s: reply["output"],
    )
    for variable in (
        "SEGGER_RTT_PATH",
        "CMSIS_NN_PATH",
        "HELIART_DIST_PATH",
        "HELIART_SOURCE_PATH",
    ):
        monkeypatch.delenv(variable, raising=False)
    return reply


@pytest.fixture
def session(tmp_path: Path) -> hpx.Session:
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
    assert plan.toolchain == hpx.ToolchainIdentity(
        "arm-none-eabi-gcc", "arm-none-eabi-gcc (Arm GNU Toolchain 15.2.Rel1) 15.2.1 20251203"
    )
    assert plan.qualification == "qualified" and plan.overrides == () and plan.portable


def test_host_local_settings_are_not_stored_and_come_from_the_replaying_host(session, tmp_path):
    local = (
        session.with_target(jlink_serial="1160002255")
        .with_power(serial="ABC")
        .with_output(dir=str(tmp_path / "out"))
    )
    plan = local.freeze()

    assert plan.intent_hash == session.freeze().intent_hash
    stored = plan.to_dict()["settings"]
    assert "jlink_serial" not in stored["target"] and "output" not in stored
    replayed = plan.config(host={"target": {"jlink_serial": "999"}, "output": {"dir": "elsewhere"}})
    assert replayed.target.jlink_serial == "999" and str(replayed.output.dir) == "elsewhere"
    with pytest.raises(ConfigError, match="profiling is not a host-local setting"):
        plan.config(host={"profiling": {"iterations": 1}})


def test_a_setting_that_changes_the_run_changes_the_intent(session):
    assert session.with_profiling(iterations=7).freeze().intent_hash != session.freeze().intent_hash


def test_intent_follows_model_content_not_its_path(session, tmp_path):
    plan = session.freeze()
    moved = tmp_path / "moved.tflite"
    moved.write_bytes(Path(plan.model.path).read_bytes())

    assert session.freeze(moved).intent_hash == plan.intent_hash
    assert plan.config(model=moved).model.path == moved


def test_a_changed_model_or_baseline_is_refused_on_replay(session):
    plan = session.freeze()
    with pytest.raises(ConfigError, match="frozen against baseline"):
        replace(plan, baseline_fingerprint="0" * 64).config()
    Path(plan.model.path).write_bytes(b"TFL3" + bytes(65))
    with pytest.raises(ConfigError, match="does not match the frozen model"):
        plan.config()


def test_local_sources_are_content_identified_and_checked_on_replay(session, tmp_path):
    rtt = tmp_path / "rtt"
    rtt.mkdir()
    aot = tmp_path / "aot.yaml"
    aot.write_text("memory: {}\n", encoding="utf-8")
    plan = session.with_target(segger_rtt_path=str(rtt)).freeze()
    aot_plan = session.with_engine("helia-aot", config_path=str(aot)).freeze()

    assert [(i.setting, i.kind) for i in plan.local_inputs] == [
        ("target.segger_rtt_path", "directory")
    ]
    assert not plan.portable and plan.intent_hash != session.freeze().intent_hash
    file_input = aot_plan.local_inputs[0]
    assert file_input.setting == "engine.config_path" and file_input.kind == "file"
    aot.write_text("memory: {planner: greedy}\n", encoding="utf-8")
    assert session.with_engine("helia-aot", config_path=str(aot)).freeze().intent_hash != (
        aot_plan.intent_hash
    )
    with pytest.raises(ConfigError, match="differs from the frozen file"):
        aot_plan.config()


def test_source_environment_is_recorded(session, tmp_path, monkeypatch):
    monkeypatch.setenv("CMSIS_NN_PATH", str(tmp_path))
    plan = session.freeze()

    assert ("env.CMSIS_NN_PATH", "directory") in [(i.setting, i.kind) for i in plan.local_inputs]
    assert plan.qualification != "qualified"


def test_the_engine_identity_is_what_a_profile_build_uses(session):
    from helia_profiler.deps.compatibility import load_compatibility_baseline

    baseline = load_compatibility_baseline()
    tflm = session.with_engine("tflm", backend="cmsis_nn").freeze().runtime
    heliart = session.freeze().runtime

    assert tflm == hpx.FrozenRuntime(
        "tflm",
        None,
        baseline.module("nsx-tflite-micro").ref,
        "arm-cmsis-nn",
        baseline.module("arm-cmsis-nn").ref,
    )
    assert heliart is not None and heliart.version == "2.1.0"
    assert heliart.source_commit == baseline.module("nsx-helia-rt").ref
    assert heliart.kernels_commit == baseline.module("nsx-cmsis-nn").ref


def test_toolchain_identity_keeps_every_version_line(session, compiler):
    compiler["output"] = (
        "Product: Hardware Success Kit (Early Access)\n"
        "Component: Arm Compiler for Embedded 6.23\nTool: armclang [5ee90200]\n"
    )
    assert session.freeze().toolchain.version == "Component: Arm Compiler for Embedded 6.23"
    compiler["output"] = None
    toolchain = session.freeze().toolchain
    assert toolchain.version is None and "could not run" in (toolchain.unavailable_reason or "")


def test_an_edited_or_malformed_plan_file_is_refused(session, tmp_path):
    path = session.freeze().save(tmp_path / "run.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["settings"]["profiling"]["iterations"] = 99
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError, match="intent hash does not match"):
        FrozenRun.load(path)
    data["settings"] = []
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError, match="Malformed frozen run plan"):
        FrozenRun.load(path)


def test_to_dict_is_a_copy(session):
    plan = session.freeze()
    data = plan.to_dict()
    data["model"]["sha256"] = "0" * 64
    data["settings"]["profiling"]["iterations"] = 1

    assert plan.model.sha256 != "0" * 64
    assert plan.settings["profiling"]["iterations"] != 1


def test_the_hpx_release_is_recorded_but_not_part_of_the_intent(session):
    plan = session.freeze()
    assert plan.hpx_version == hpx.__version__
    assert replace(plan, hpx_version="9.9.9").intent_hash == plan.intent_hash
