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
    from helia_profiler.frozen_run import _ENVIRONMENT_VARIABLES

    for variable in (
        "SEGGER_RTT_PATH",
        "CMSIS_NN_PATH",
        "HELIART_DIST_PATH",
        "HELIART_SOURCE_PATH",
        *_ENVIRONMENT_VARIABLES,
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
    from helia_profiler.runtime_records import runtime

    record = runtime("helia-rt")
    assert record is not None and heliart is not None and heliart.version == record.version
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


def test_default_tflm_links_no_kernel_library(session):
    runtime = session.with_engine("tflm").freeze().runtime
    assert runtime is not None and runtime.kernels_module is None and runtime.kernels_commit is None


def test_heliaaot_records_the_installed_compiler(session, monkeypatch):
    monkeypatch.setattr(
        "helia_profiler.results.dependencies.installed_aot_source",
        lambda: {"package": "helia-aot", "version": "0.30.0", "commit": "abc123"},
    )
    runtime = session.with_engine("helia-aot").freeze().runtime
    assert runtime is not None
    assert (runtime.name, runtime.version, runtime.source_commit) == (
        "helia-aot",
        "0.30.0",
        "abc123",
    )


def test_an_overridden_kernel_provider_has_no_pinned_commit(session, tmp_path, monkeypatch):
    monkeypatch.setenv("CMSIS_NN_PATH", str(tmp_path))
    runtime = session.freeze().runtime
    assert runtime is not None and runtime.kernels_module == "nsx-cmsis-nn"
    assert runtime.kernels_commit is None


def test_source_variables_count_only_for_engines_that_read_them(session, tmp_path, monkeypatch):
    monkeypatch.setenv("HELIART_DIST_PATH", str(tmp_path))
    assert session.with_engine("tflm").freeze().local_inputs == ()
    assert [i.setting for i in session.freeze().local_inputs] == ["env.HELIART_DIST_PATH"]


def test_module_and_engine_path_inputs_are_recorded(session, tmp_path):
    module_dir = tmp_path / "nsx-sensors"
    module_dir.mkdir()
    plan = session.with_build(nsx_modules={"nsx-sensors": {"path": str(module_dir)}}).freeze()
    assert [(i.setting, i.kind) for i in plan.local_inputs] == [
        ("build.nsx_modules.nsx-sensors.path", "directory")
    ]
    module_dir.rmdir()
    with pytest.raises(ConfigError, match="does not exist on this host"):
        plan.config()


def test_toolchain_identity_drops_install_locations(session, compiler):
    compiler["output"] = (
        "clang version 19.1.5\nTarget: arm-none-eabi\n"
        "InstalledDir: /opt/LLVM-ET-Arm-19.1.5/bin\nConfiguration file: /opt/x-1.2/cfg\n"
    )
    assert session.freeze().toolchain.version == "clang version 19.1.5"


def test_a_non_utf8_plan_file_is_refused(tmp_path):
    path = tmp_path / "run.json"
    path.write_bytes(b"\xff\xfe{")
    with pytest.raises(ConfigError, match="Cannot read frozen run plan"):
        FrozenRun.load(path)


def test_tflm_modules_ignore_overrides_the_build_does_not_apply(session, tmp_path):
    from helia_profiler.deps.compatibility import load_compatibility_baseline

    module_dir = tmp_path / "nsx-tflite-micro"
    module_dir.mkdir()
    overridden = session.with_engine("tflm").with_build(
        nsx_modules={"nsx-tflite-micro": {"path": str(module_dir)}}
    )
    runtime = overridden.freeze().runtime
    expected = load_compatibility_baseline().module("nsx-tflite-micro").ref
    assert runtime is not None and runtime.source_commit == expected


def _pte_session(tmp_path: Path, *, kernel_provider: str | None = None) -> hpx.Session:
    import hashlib

    model = tmp_path / "model.pte"
    model.write_bytes(b"PTE0" + bytes(64))
    if kernel_provider is not None:
        sidecar = {
            "schema": "nsx-executorch.pte-manifest/1",
            "pte_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
            "kernel_provider": kernel_provider,
        }
        Path(f"{model}.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return (
        hpx.Session()
        .with_model(model)
        .with_engine("executorch")
        .with_target(board="apollo510_evb", toolchain="arm-none-eabi-gcc")
    )


def test_a_changed_set_or_unset_source_variable_is_refused_on_replay(
    session, tmp_path, monkeypatch
):
    first, second = tmp_path / "rt-a", tmp_path / "rt-b"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv("HELIART_SOURCE_PATH", str(first))
    plan = session.freeze()

    monkeypatch.setenv("HELIART_SOURCE_PATH", str(second))
    with pytest.raises(ConfigError, match="different run than the frozen plan: local inputs"):
        plan.config()
    monkeypatch.delenv("HELIART_SOURCE_PATH")
    with pytest.raises(ConfigError, match="local inputs"):
        plan.config()
    clean = session.freeze()
    monkeypatch.setenv("CMSIS_NN_PATH", str(first))
    with pytest.raises(ConfigError, match="env.CMSIS_NN_PATH"):
        clean.config()


def test_explicit_settings_beat_the_variables_they_replace(session, tmp_path, monkeypatch):
    rtt, source = tmp_path / "rtt", tmp_path / "rt"
    rtt.mkdir()
    source.mkdir()
    monkeypatch.setenv("SEGGER_RTT_PATH", str(tmp_path))
    monkeypatch.setenv("HELIART_DIST_PATH", str(tmp_path))
    plan = session.with_target(segger_rtt_path=str(rtt)).with_engine(
        "helia-rt", config={"source_path": str(source)}
    )
    assert [i.setting for i in plan.freeze().local_inputs] == [
        "target.segger_rtt_path",
        "engine.config.source_path",
    ]


def test_another_compiler_or_engine_runtime_is_refused_on_replay(session, compiler, monkeypatch):
    plan = session.freeze()
    compiler["output"] = "arm-none-eabi-gcc (Arm GNU Toolchain 14.3.Rel1) 14.3.1\n"
    with pytest.raises(ConfigError, match="toolchain: frozen"):
        plan.config()

    compiler["output"] = _GCC
    installed = {"package": "helia-aot", "version": "0.30.0", "commit": "abc123"}
    monkeypatch.setattr(
        "helia_profiler.results.dependencies.installed_aot_source", lambda: dict(installed)
    )
    aot_plan = session.with_engine("helia-aot").freeze()
    installed.update(version="0.31.0", commit="def456")
    with pytest.raises(ConfigError, match="engine runtime"):
        aot_plan.config()


def test_the_executorch_sidecar_is_frozen_beside_the_model(tmp_path):
    session = _pte_session(tmp_path, kernel_provider="ns")
    plan = session.freeze()
    sidecar = Path(f"{plan.model.path}.json")

    assert [(i.setting, i.kind) for i in plan.local_inputs] == [("model.sidecar", "file")]
    runtime = plan.runtime
    assert runtime is not None and runtime.kernels_module == "nsx-cmsis-nn"

    moved_dir = tmp_path / "moved"
    moved_dir.mkdir()
    moved = moved_dir / "model.pte"
    moved.write_bytes(Path(plan.model.path).read_bytes())
    with pytest.raises(ConfigError, match="model.sidecar"):
        plan.config(model=moved)
    Path(f"{moved}.json").write_bytes(sidecar.read_bytes())
    assert plan.config(model=moved).model.path == moved

    sidecar.write_text(sidecar.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ConfigError, match="model.sidecar"):
        plan.config()


def test_a_sidecar_that_appears_after_freezing_is_refused(tmp_path):
    bare = _pte_session(tmp_path).freeze()
    assert bare.local_inputs == () and bare.runtime is not None
    assert bare.runtime.kernels_module == "arm-cmsis-nn"
    _pte_session(tmp_path, kernel_provider="arm")
    with pytest.raises(ConfigError, match="model.sidecar"):
        bare.config()


def test_the_arm_provider_ignores_cmsis_nn_path(tmp_path, monkeypatch):
    monkeypatch.setenv("CMSIS_NN_PATH", str(tmp_path))
    assert _pte_session(tmp_path).freeze().local_inputs == ()
    ns = _pte_session(tmp_path).with_engine("executorch", backend="ns").freeze()
    assert [i.setting for i in ns.local_inputs] == ["env.CMSIS_NN_PATH"]


def test_a_missing_local_input_is_refused_at_freeze(session, tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        session.with_target(segger_rtt_path=str(tmp_path / "absent")).freeze()


def test_heliart_runtime_selectors_are_separate_from_kernel_overrides(
    session, tmp_path, monkeypatch
):
    from helia_profiler.runtime_records import runtime

    kernels = session.with_engine("helia-rt", config={"cmsis_nn_path": str(tmp_path)}).freeze()
    record = runtime("helia-rt")
    assert kernels.runtime is not None and record is not None
    assert kernels.runtime.version == record.version and kernels.runtime.kernels_commit is None

    monkeypatch.setenv("HELIART_DIST_PATH", str(tmp_path))
    local = session.freeze().runtime
    assert local is not None and local.version is None and local.source_commit is None
    assert local.unavailable_reason == "heliaRT selected by env.HELIART_DIST_PATH"


def test_the_frozen_alias_is_host_local(session):
    plan = session.with_options(frozen=True).freeze()
    assert plan.intent_hash == session.freeze().intent_hash
    assert "frozen" not in plan.to_dict()["settings"]
    assert plan.config(host={"frozen": True}).build.offline


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("model", "path", None),
        ("model", "size_bytes", "68"),
        ("toolchain", "version", 15),
        ("baseline", "id", None),
    ],
)
def test_a_plan_field_of_the_wrong_type_is_refused(session, tmp_path, section, key, value):
    data = session.freeze().to_dict()
    data[section][key] = value
    with pytest.raises(ConfigError, match=f"Malformed frozen run plan: {section}.{key} must be"):
        FrozenRun.from_dict(data)


def test_a_local_input_without_a_known_kind_is_refused(session, tmp_path):
    rtt = tmp_path / "rtt"
    rtt.mkdir()
    data = session.with_target(segger_rtt_path=str(rtt)).freeze().to_dict()
    data["local_inputs"][0]["kind"] = "missing"
    with pytest.raises(ConfigError, match="Malformed frozen run plan: local input"):
        FrozenRun.from_dict(data)


def test_identical_config_files_at_different_paths_share_an_intent(session, tmp_path):
    first, second = tmp_path / "a.yaml", tmp_path / "b.yaml"
    for path in (first, second):
        path.write_text("memory: {}\n", encoding="utf-8")
    plans = [
        session.with_engine("helia-aot", config_path=str(path)).freeze() for path in (first, second)
    ]
    assert plans[0].intent_hash == plans[1].intent_hash
    assert plans[0].settings["engine"]["config_path"] == str(first)


def test_a_heliart_variable_other_engines_ignore_does_not_block_replay(
    session, tmp_path, monkeypatch
):
    monkeypatch.setenv("HELIART_DIST_PATH", str(tmp_path))
    tflm = session.with_engine("tflm").freeze()
    assert tflm.qualification == "qualified" and tflm.overrides == ()
    monkeypatch.delenv("HELIART_DIST_PATH")
    assert tflm.config().engine.type.value == "tflm"


def test_a_prebuilt_heliart_links_no_separate_kernel_module(session, tmp_path, monkeypatch):
    monkeypatch.setenv("HELIART_DIST_PATH", str(tmp_path))
    prebuilt = session.freeze().runtime
    assert prebuilt is not None and (prebuilt.kernels_module, prebuilt.kernels_commit) == (
        None,
        None,
    )
    monkeypatch.setenv("HELIART_SOURCE_PATH", str(tmp_path))
    source = session.freeze().runtime
    assert source is not None and source.kernels_module == "nsx-cmsis-nn"
    assert source.unavailable_reason == "heliaRT selected by env.HELIART_SOURCE_PATH"


def test_a_corrupted_sidecar_is_a_config_error_on_replay(tmp_path):
    plan = _pte_session(tmp_path, kernel_provider="ns").freeze()
    Path(f"{plan.model.path}.json").write_text("{", encoding="utf-8")
    with pytest.raises(ConfigError, match="differs from the frozen file"):
        plan.config()
    bare_dir = tmp_path / "bare"
    bare_dir.mkdir()
    bare = _pte_session(bare_dir).freeze()
    Path(f"{bare.model.path}.json").write_text("{", encoding="utf-8")
    with pytest.raises(ConfigError, match="was not there when the plan was frozen"):
        bare.config()


def test_a_relative_source_path_is_recorded_resolved(session, tmp_path, monkeypatch):
    (tmp_path / "rtt").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEGGER_RTT_PATH", "rtt")
    plan = session.freeze()
    assert [i.path for i in plan.local_inputs] == [str((tmp_path / "rtt").resolve())]
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "rtt").mkdir(parents=True)
    monkeypatch.chdir(elsewhere)
    with pytest.raises(ConfigError, match="local inputs"):
        plan.config()


def test_a_non_object_plan_is_refused():
    with pytest.raises(ConfigError, match="must be an object"):
        FrozenRun.from_dict([])  # ty: ignore[invalid-argument-type]


def test_compiler_environment_is_part_of_the_intent_and_checked_on_replay(session, monkeypatch):
    monkeypatch.setenv("CFLAGS", "-O0")
    monkeypatch.setenv("CMAKE_C_COMPILER_LAUNCHER", "ccache")
    plan = session.freeze()
    assert dict(plan.environment) == {"CFLAGS": "-O0"}
    assert not plan.portable
    assert FrozenRun.from_dict(plan.to_dict()) == plan

    monkeypatch.setenv("CFLAGS", "-O3")
    with pytest.raises(ConfigError, match="environment: frozen"):
        plan.config()
    monkeypatch.delenv("CFLAGS")
    with pytest.raises(ConfigError, match="environment"):
        plan.config()
    assert session.freeze().intent_hash != plan.intent_hash


def test_rtt_sources_count_only_for_the_rtt_transport(session, tmp_path, monkeypatch):
    monkeypatch.setenv("SEGGER_RTT_PATH", str(tmp_path / "absent"))
    uart = session.with_target(transport="uart", segger_rtt_path=str(tmp_path / "gone"))
    assert uart.freeze().local_inputs == ()


def test_a_sidecar_selected_provider_decides_qualification(tmp_path, monkeypatch):
    checkout = tmp_path / "cmsis"
    checkout.mkdir()
    monkeypatch.setenv("CMSIS_NN_PATH", str(checkout))
    plan = _pte_session(tmp_path, kernel_provider="ns").freeze()

    assert "env.CMSIS_NN_PATH" in [i.setting for i in plan.local_inputs]
    assert plan.qualification != "qualified" and "nsx-cmsis-nn" in plan.overrides
    assert plan.runtime is not None and plan.runtime.kernels_commit is None


def test_an_unused_heliart_distribution_is_not_an_input(session, tmp_path, monkeypatch):
    source = tmp_path / "rt-src"
    source.mkdir()
    absent = str(tmp_path / "no-dist")
    explicit = session.with_engine(
        "helia-rt", config={"source_path": str(source), "dist_path": absent}
    ).freeze()
    assert [i.setting for i in explicit.local_inputs] == ["engine.config.source_path"]
    assert explicit.runtime is not None
    assert explicit.runtime.unavailable_reason == "heliaRT selected by engine.config.source_path"

    monkeypatch.setenv("HELIART_SOURCE_PATH", str(source))
    from_env = session.with_engine("helia-rt", config={"dist_path": absent}).freeze()
    assert [i.setting for i in from_env.local_inputs] == ["env.HELIART_SOURCE_PATH"]


def test_a_power_judgement_variable_is_part_of_the_intent(session, monkeypatch):
    plan = session.freeze()
    monkeypatch.setenv("HPX_POWER_ALLOW_NEGATIVE", "1")
    assert session.freeze().portable
    with pytest.raises(ConfigError, match="environment"):
        plan.config()
