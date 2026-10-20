"""CompiledBuild: a frozen plan's compiled fixture, saved, verified and reloaded."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import helia_profiler as hpx
from helia_profiler._fixture_build import (
    FixtureMethod,
    FixtureOutput,
    FixtureTimingScope,
    TypedFixture,
)
from helia_profiler._fixture_record import compiled_fixture_identity
from helia_profiler.compiled_build import (
    COMPILED_BUILD_SCHEMA,
    COMPILED_BUILD_SCHEMA_VERSION,
    CompiledBuild,
    _binding,
)
from helia_profiler.errors import ConfigError
from helia_profiler.fixture import FixtureFile, fixture_run_identity
from helia_profiler.frozen_run import _ENVIRONMENT_VARIABLES, FrozenRun
from helia_profiler.lifecycle import Lifecycle
from helia_profiler.placement import Placement
from helia_profiler.results.models import ToolchainInfo
from tests.run_api_helpers import (
    ATFE_OUTPUT,
    ATFE_VERSION,
    atfe_build,
    compiled,
    plan_for,
    reseal,
)
from tests.unit.test_fixture_record import _build, _pin

_METHOD = FixtureMethod(FixtureTimingScope.INVOKE_ONLY)


@pytest.fixture(autouse=True)
def compiler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "helia_profiler.hostenv._proc.tool_output", lambda command, *, timeout_s: ATFE_OUTPUT
    )
    for variable in (
        "SEGGER_RTT_PATH",
        "CMSIS_NN_PATH",
        "HELIART_DIST_PATH",
        "HELIART_SOURCE_PATH",
        *_ENVIRONMENT_VARIABLES,
    ):
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "run"


@pytest.fixture
def build(root: Path):
    return atfe_build(_build(root))


@pytest.fixture
def plan(tmp_path: Path, build) -> FrozenRun:
    return plan_for(build, tmp_path / "model.tflite")


@pytest.fixture
def fixture(build) -> TypedFixture:
    return TypedFixture(
        build.model, build.inputs, tuple(FixtureOutput(t) for t in build.output_tensors)
    )


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch, build) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def fake(config, fixture, **options):
        seen.append({"config": config, "fixture": fixture, **options})
        profiling = config.profiling
        return reseal(build, iterations=profiling.iterations, warmups=profiling.warmup)

    monkeypatch.setattr("helia_profiler._fixture_build.build_fixed_fixture", fake)
    return seen


@pytest.mark.parametrize(
    "lifecycle,expected", [(Lifecycle.CARRY, (100, 5)), (Lifecycle.SINGLE, (1, 0))]
)
def test_build_applies_the_lifecycle_and_compiles(plan, build, fixture, calls, lifecycle, expected):
    record = plan.build(fixture, method=_METHOD, lifecycle=lifecycle, observe_ram=True)

    (call,) = calls
    profiling = call["config"].profiling
    assert (profiling.iterations, profiling.warmup) == expected
    assert call["compile"] is True and call["observe_ram"] is True
    assert call["method"] is _METHOD and call["fixture"] is fixture
    built = reseal(build, iterations=expected[0], warmups=expected[1])
    assert record == compiled(plan, built, lifecycle)
    assert record.plan is plan and call["runtime"] is None


def test_build_verifies_what_it_compiled(plan, build, fixture, monkeypatch):
    def wrong_counts(config, fixture, **options):
        return reseal(build, iterations=7, warmups=0)

    monkeypatch.setattr("helia_profiler._fixture_build.build_fixed_fixture", wrong_counts)
    with pytest.raises(ConfigError, match="runs 100 and 5"):
        plan.build(fixture, method=_METHOD, lifecycle=Lifecycle.CARRY)


def test_build_refuses_an_unknown_lifecycle(plan, fixture, calls):
    with pytest.raises(ConfigError, match="Unknown lifecycle 'bogus'"):
        plan.build(fixture, method=_METHOD, lifecycle="bogus")
    assert calls == []


def test_build_asks_the_engine_for_the_lifecycle(plan, fixture, calls, monkeypatch):
    def refuse(engine, lifecycle):
        raise ConfigError(f"{engine.value} refuses {lifecycle.value}")

    monkeypatch.setattr("helia_profiler.compiled_build.require_lifecycle", refuse)
    with pytest.raises(ConfigError, match="helia-aot refuses single"):
        hpx.build(plan, fixture, method=_METHOD, lifecycle=Lifecycle.SINGLE)
    assert not calls


def test_the_engine_refuses_a_lifecycle_it_does_not_run(plan, fixture, calls, monkeypatch):
    from types import SimpleNamespace

    from helia_profiler.errors import EngineError

    adapter = SimpleNamespace(
        name="fake",
        lifecycles=frozenset({Lifecycle.SINGLE}),
        lifecycle_refusal=lambda lifecycle: "it reloads the program on every call",
    )
    monkeypatch.setattr("helia_profiler.lifecycle.get_adapter", lambda engine: adapter)
    with pytest.raises(EngineError, match="reloads the program on every call"):
        plan.build(fixture, method=_METHOD, lifecycle=Lifecycle.CARRY)
    assert not calls


def test_host_settings_reach_the_build(plan, fixture, calls):
    plan.build(fixture, method=_METHOD, host={"target": {"jlink_serial": "777"}})
    assert calls[0]["config"].target.jlink_serial == "777"


def test_a_single_invocation_is_refused_for_power(plan, fixture, calls):
    from types import SimpleNamespace

    with pytest.raises(
        ConfigError, match="not a Lifecycle.SINGLE one, which runs the model stateless"
    ):
        power_method: Any = SimpleNamespace(power_sync=object())
        plan.build(fixture, method=power_method, lifecycle=Lifecycle.SINGLE)
    assert not calls


def test_a_deeply_nested_record_is_a_config_error(tmp_path, root):
    path = tmp_path / "deep.json"
    path.write_text("[" * 100000, encoding="utf-8")
    with pytest.raises(ConfigError, match="Cannot read compiled build"):
        CompiledBuild.load(path, roots={"run": root})


def test_build_refuses_a_fixture_of_another_model(plan, fixture, calls, tmp_path):
    other = tmp_path / "other.tflite"
    other.write_bytes(b"other")
    changed = replace(fixture, model=FixtureFile(other, hashlib.sha256(b"other").hexdigest()))
    with pytest.raises(ConfigError, match="does not match the plan's model"):
        hpx.build(plan, changed, method=_METHOD)
    assert not calls


def test_identity_states_the_lifecycle_that_ran(plan, build):
    base = fixture_run_identity(build)
    identity = compiled(plan, build, Lifecycle.SINGLE).identity

    assert base.run.lifecycle == "carry"
    assert identity.run.lifecycle == "single"
    assert base.intent_hash is None and identity.intent_hash == plan.intent_hash
    assert replace(identity, run=base.run, intent_hash=None) == base
    assert identity.observation_hash is None


def _single(build):
    once = replace(build, iterations=1, warmups=0)
    return replace(once, build_identity=compiled_fixture_identity(once))


def test_save_and_load_round_trip_after_relocation(plan, build, root, tmp_path):
    build = _single(build)
    record = compiled(plan, build, Lifecycle.SINGLE)
    identity = record.identity
    path = record.save(tmp_path / "records" / "kws.json", roots={"run": root})

    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert text == json.dumps(data, indent=2, sort_keys=True) + "\n"
    assert str(root) not in text
    assert data["schema"] == COMPILED_BUILD_SCHEMA
    assert data["schema_version"] == COMPILED_BUILD_SCHEMA_VERSION
    assert data["lifecycle"] == "single"
    assert data["plan"] == plan.to_dict()
    assert isinstance(data["build"], dict)

    moved = tmp_path / "moved"
    shutil.move(root, moved)
    loaded = CompiledBuild.load(path, roots={"run": moved})

    assert loaded.lifecycle is Lifecycle.SINGLE
    assert loaded.plan.to_dict() == plan.to_dict()
    assert loaded.build.build_identity == build.build_identity
    assert loaded.build.flat_binary is not None
    assert loaded.build.flat_binary.path == moved / "app" / "main.bin"
    assert loaded.identity == identity
    assert loaded.save(tmp_path / "again.json", roots={"run": moved}).read_text() == text


def test_save_verifies_before_writing(plan, build, tmp_path):
    record = replace(compiled(plan, build, Lifecycle.CARRY), lifecycle=Lifecycle.SINGLE)
    target = tmp_path / "records" / "kws.json"
    with pytest.raises(ConfigError):
        record.save(target, roots={"run": tmp_path / "run"})
    assert not target.exists()


def test_an_interrupted_save_keeps_the_previous_file(plan, build, tmp_path, monkeypatch):
    target = tmp_path / "kws.json"
    target.write_text("previous", encoding="utf-8")

    def interrupted(self: Path, target: object) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", interrupted)
    with pytest.raises(ConfigError, match="disk full"):
        compiled(plan, build, Lifecycle.CARRY).save(target, roots={"run": tmp_path / "run"})
    assert target.read_text(encoding="utf-8") == "previous"
    assert list(tmp_path.glob(".kws.json*")) == []


def test_save_refuses_a_file_outside_the_roots(plan, build, tmp_path):
    with pytest.raises(ConfigError, match="Cannot save compiled build"):
        compiled(plan, build, Lifecycle.CARRY).save(
            tmp_path / "r.json", roots={"run": tmp_path / "elsewhere"}
        )


def test_verify_refuses_a_tampered_build(plan, build):
    record = compiled(plan, build, Lifecycle.CARRY)
    record.verify()
    assert build.flat_binary is not None
    build.flat_binary.path.write_bytes(b"changed image")
    with pytest.raises(ConfigError, match="does not verify"):
        record.verify()


def test_verify_refuses_a_lifecycle_the_firmware_does_not_run(plan, build):
    compiled(plan, _single(build), Lifecycle.SINGLE).verify()
    assert build.iterations == 100
    with pytest.raises(ConfigError, match="lifecycle single of its plan runs 1 and 0"):
        compiled(plan, build, Lifecycle.SINGLE).verify()
    with pytest.raises(ConfigError, match="lifecycle carry of its plan runs 100 and 5"):
        compiled(plan, _single(build), Lifecycle.CARRY).verify()


def test_verify_refuses_a_plan_for_another_model(plan, build):
    other = replace(plan, model=replace(plan.model, sha256="0" * 64))
    with pytest.raises(ConfigError, match="model does not match its plan"):
        compiled(other, build, Lifecycle.CARRY).verify()


def test_verify_refuses_a_plan_whose_intent_hash_is_stale(plan, build):
    object.__setattr__(plan, "_intent", "0" * 64)
    with pytest.raises(ConfigError, match="intent hash"):
        compiled(plan, build, Lifecycle.CARRY).verify()


def test_load_verifies_the_plan_against_the_build(plan, build, root, tmp_path):
    path = compiled(plan, build, Lifecycle.CARRY).save(tmp_path / "r.json", roots={"run": root})
    data = json.loads(path.read_text())
    other = replace(plan, model=replace(plan.model, sha256="0" * 64))
    data["plan"] = other.to_dict()
    path.write_text(json.dumps(data))
    with pytest.raises(ConfigError, match="binding does not match its plan"):
        CompiledBuild.load(path, roots={"run": root})


def _edit(data: dict[str, Any], mutation: str) -> str:
    if mutation == "not_object":
        return "[]"
    if mutation == "missing_build":
        del data["build"]
    elif mutation == "extra_field":
        data["note"] = "x"
    elif mutation == "build_string":
        data["build"] = json.dumps(data["build"])
    elif mutation == "lifecycle_unknown":
        data["lifecycle"] = "reset"
    elif mutation == "lifecycle_list":
        data["lifecycle"] = ["carry"]
    elif mutation == "plan_empty":
        data["plan"] = {}
    elif mutation == "plan_edited":
        data["plan"]["settings"]["profiling"]["iterations"] = 99
    elif mutation == "build_bool_count":
        data["build"]["build"]["iterations"] = True
    elif mutation == "build_traversal":
        data["build"]["build"]["binary"]["path"] = "../main.elf"
    text = json.dumps(data)
    if mutation == "not_json":
        return text[:-1]
    if mutation == "duplicate":
        return text.replace('"lifecycle": "carry"', '"lifecycle": "carry", "lifecycle": "single"')
    if mutation == "nan":
        return text.replace('"iterations": 100', '"iterations": NaN')
    return text


@pytest.mark.parametrize(
    "mutation",
    [
        "not_json",
        "not_object",
        "missing_build",
        "extra_field",
        "build_string",
        "lifecycle_unknown",
        "lifecycle_list",
        "plan_empty",
        "plan_edited",
        "build_bool_count",
        "build_traversal",
        "duplicate",
        "nan",
    ],
)
def test_malformed_records_are_refused_as_config_errors(plan, build, root, tmp_path, mutation):
    path = compiled(plan, build, Lifecycle.CARRY).save(tmp_path / "r.json", roots={"run": root})
    edited = _edit(json.loads(path.read_text()), mutation)
    assert edited != path.read_text()
    path.write_text(edited)
    with pytest.raises(ConfigError):
        CompiledBuild.load(path, roots={"run": root})


def test_missing_record_or_pinned_file_is_a_config_error(plan, build, root, tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        CompiledBuild.load(tmp_path / "absent.json", roots={"run": root})
    path = compiled(plan, build, Lifecycle.CARRY).save(tmp_path / "r.json", roots={"run": root})
    assert build.flat_binary is not None
    build.flat_binary.path.unlink()
    with pytest.raises(ConfigError, match="missing under the given roots"):
        CompiledBuild.load(path, roots={"run": root})


@pytest.mark.parametrize(
    "key,value",
    [
        ("schema", "hpx.frozen-run"),
        ("schema_version", COMPILED_BUILD_SCHEMA_VERSION + 1),
        ("schema_version", str(COMPILED_BUILD_SCHEMA_VERSION)),
        ("schema_version", True),
        ("schema_version", 2),
    ],
)
def test_unknown_schema_or_version_is_refused(plan, build, root, tmp_path, key, value):
    path = compiled(plan, build, Lifecycle.CARRY).save(tmp_path / "r.json", roots={"run": root})
    data = json.loads(path.read_text())
    data[key] = value
    path.write_text(json.dumps(data))
    match = "Not a compiled build" if key == "schema" else "Unsupported compiled build version"
    with pytest.raises(ConfigError, match=match):
        CompiledBuild.load(path, roots={"run": root})


_RUN_API = ("CompiledBuild", "RunResult", "Lifecycle", "build", "load_build", "run_build")


def test_package_exports_the_run_api():
    import importlib.util

    assert hpx.CompiledBuild is CompiledBuild
    assert hpx.Lifecycle is Lifecycle
    assert hpx.RunResult.__module__ == "helia_profiler.compiled_build"
    assert hpx.build.__module__ == "helia_profiler.compiled_build"
    assert {hpx.__api_stability__[name] for name in _RUN_API} == {"experimental"}
    assert importlib.util.find_spec("helia_profiler.build") is None


def test_load_build_and_run_build_delegate_to_the_record(plan, build, root, tmp_path, monkeypatch):
    record = compiled(plan, build, Lifecycle.CARRY)
    path = record.save(tmp_path / "r.json", roots={"run": root})
    assert hpx.load_build(path, roots={"run": root}) == record

    seen: dict[str, Any] = {}
    monkeypatch.setattr(CompiledBuild, "run", lambda self, **options: seen.update(options) or "ran")
    guard: Any = object()
    result = hpx.run_build(
        record, guard=guard, jlink_serial="1", evidence_dir=tmp_path, settle_seconds=1.0
    )
    assert result == "ran"
    assert seen == {
        "guard": guard,
        "jlink_serial": "1",
        "evidence_dir": tmp_path,
        "settle_seconds": 1.0,
        "expect": None,
        "expected_duration_s": None,
        "power": None,
        "power_guard": None,
    }


def test_a_lifecycle_given_by_name_is_a_lifecycle(plan, fixture, calls):
    record = plan.build(fixture, method=_METHOD, lifecycle="single")
    assert record.lifecycle is Lifecycle.SINGLE
    assert (calls[0]["config"].profiling.iterations, calls[0]["config"].profiling.warmup) == (1, 0)


@pytest.mark.parametrize(
    "fact,plan_options,build_change,message",
    [
        (
            "engine",
            {"engine": "helia-rt"},
            {},
            "engine 'helia-aot' differs from its plan's 'helia-rt'",
        ),
        (
            "board",
            {"board": "apollo510b_evb"},
            {},
            "board 'apollo510_evb' differs from its plan's 'apollo510b_evb'",
        ),
        (
            "toolchain",
            {},
            {"toolchain": ToolchainInfo("arm-none-eabi-gcc", ATFE_VERSION, "3.31")},
            "toolchain 'arm-none-eabi-gcc' differs from its plan's 'atfe'",
        ),
        (
            "toolchain version",
            {},
            {"toolchain": ToolchainInfo("atfe", "clang version 21.1.0", "3.31")},
            "toolchain version 'clang version 21.1.0' is not its plan's 'clang version 22.1.0'",
        ),
        (
            "arena location",
            {},
            {"arena_location": Placement.TCM},
            "arena location 'tcm' differs from its plan's 'sram'",
        ),
        (
            "weights location",
            {},
            {"weights_location": Placement.TCM},
            "weights location 'tcm' differs from its plan's 'mram'",
        ),
        (
            "arena size",
            {},
            {"arena_capacity": 65536},
            "arena size 65536 differs from its plan's 32768",
        ),
        ("clock profile", {"clock": "hp"}, {}, "clock profile 'lp' differs from its plan's 'hp'"),
    ],
)
def test_verify_names_the_fact_a_build_does_not_share_with_its_plan(
    tmp_path, build, fact, plan_options, build_change, message
):
    plan = plan_for(build, tmp_path / "planned.tflite", **plan_options)
    built = reseal(build, **build_change) if build_change else build
    with pytest.raises(ConfigError, match=re.escape(message)):
        compiled(plan, built, Lifecycle.CARRY).verify()


def test_verify_checks_the_plan_model_size_against_the_built_model(plan, build):
    assert build.model is not None
    size = build.model.path.stat().st_size
    other = replace(plan, model=replace(plan.model, size_bytes=size + 1))
    with pytest.raises(ConfigError, match=f"model size {size} differs from its plan's {size + 1}"):
        compiled(other, build, Lifecycle.CARRY).verify()


def test_the_build_binds_its_plan_and_lifecycle(plan, fixture, calls, build, tmp_path):
    record = plan.build(fixture, method=_METHOD)
    record.verify()

    other = plan_for(build, tmp_path / "other.tflite", warmup=6)
    for swapped in (replace(record, plan=other), replace(record, lifecycle=Lifecycle.SINGLE)):
        with pytest.raises(ConfigError, match="binding does not match its plan and lifecycle"):
            swapped.verify()


@pytest.mark.parametrize(
    "change",
    [
        {"timing_scope": FixtureTimingScope.RESTORE_AND_INVOKE},
        {"state_policy": None},
        {"observe_ram": True},
        {"aot_arena_scan": ((0, 1024),)},
    ],
)
def test_the_binding_covers_the_method_and_observations_the_build_records(plan, build, change):
    record = compiled(plan, build, Lifecycle.CARRY)
    with pytest.raises(ConfigError, match="binding does not match"):
        replace(record, build=reseal(build, **change)).verify()


def test_the_binding_covers_the_prepared_runtime(plan, build, root):
    manifest = _pin(root / "runtime" / "manifest.json", b"{}")
    record = compiled(plan, build, Lifecycle.CARRY)
    assert _binding(plan, Lifecycle.CARRY, reseal(build, runtime_manifest=manifest)) != (
        record.binding
    )


def test_the_binding_names_no_host_path(plan, tmp_path):
    here, there = atfe_build(_build(tmp_path / "here")), atfe_build(_build(tmp_path / "there"))
    assert _binding(plan, Lifecycle.CARRY, here) == _binding(plan, Lifecycle.CARRY, there)


def test_a_binding_refuses_a_non_finite_number(plan, tmp_path):
    build = replace(atfe_build(_build(tmp_path)), aot_arena_scan=((float("nan"), 1),))
    with pytest.raises(ConfigError, match="cannot record a non-finite number"):
        _binding(plan, Lifecycle.CARRY, build)


@pytest.mark.parametrize(
    "binding,message",
    [
        ("hpx.binding/1:sha256:" + "0" * 64, "binding does not match"),
        ("0" * 64, "not an hpx.binding/1:sha256:<hex> hash"),
        ("hpx.intent/1:sha256:" + "0" * 64, "not an hpx.binding/1:sha256:<hex> hash"),
        (7, "not an hpx.binding/1:sha256:<hex> hash"),
    ],
)
def test_load_refuses_an_edited_binding(plan, build, root, tmp_path, binding, message):
    path = compiled(plan, build, Lifecycle.CARRY).save(tmp_path / "r.json", roots={"run": root})
    data = json.loads(path.read_text())
    assert data["binding"] == compiled(plan, build, Lifecycle.CARRY).binding
    data["binding"] = binding
    path.write_text(json.dumps(data))
    with pytest.raises(ConfigError, match=message):
        hpx.load_build(path, roots={"run": root})
