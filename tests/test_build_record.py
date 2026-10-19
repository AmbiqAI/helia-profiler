"""BuildRecord: a frozen plan's compiled fixture, saved, verified and reloaded."""

from __future__ import annotations

import hashlib
import json
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
from helia_profiler.build_record import (
    BUILD_RECORD_SCHEMA,
    BUILD_RECORD_SCHEMA_VERSION,
    BuildRecord,
    build_record,
)
from helia_profiler.errors import ConfigError
from helia_profiler.fixture import FixtureFile, fixture_run_identity
from helia_profiler.frozen_run import _ENVIRONMENT_VARIABLES, FrozenRun
from helia_profiler.lifecycle import Lifecycle
from tests.unit.test_fixture_record import _build

_METHOD = FixtureMethod(FixtureTimingScope.INVOKE_ONLY)
_GCC = "arm-none-eabi-gcc (Arm GNU Toolchain 15.2.Rel1) 15.2.1 20251203\nCopyright (C) 2025\n"


@pytest.fixture(autouse=True)
def compiler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "helia_profiler.hostenv._proc.tool_output", lambda command, *, timeout_s: _GCC
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
    return _build(root)


@pytest.fixture
def plan(tmp_path: Path, build) -> FrozenRun:
    model = tmp_path / "model.tflite"
    model.write_bytes(build.model.read())
    return (
        hpx.Session()
        .with_model(model)
        .with_engine("helia-aot")
        .with_target(board="apollo510_evb", toolchain="arm-none-eabi-gcc")
        .with_profiling(iterations=100, warmup=5)
        .freeze()
    )


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
        return build

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
    assert record == BuildRecord(plan, build, lifecycle)
    assert record.plan is plan


def test_build_asks_the_engine_for_the_lifecycle(plan, fixture, calls, monkeypatch):
    def refuse(engine, lifecycle):
        raise ConfigError(f"{engine.value} refuses {lifecycle.value}")

    monkeypatch.setattr("helia_profiler.build_record.require_lifecycle", refuse)
    with pytest.raises(ConfigError, match="helia-aot refuses single"):
        build_record(plan, fixture, method=_METHOD, lifecycle=Lifecycle.SINGLE)
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

    with pytest.raises(ConfigError, match="too short for a power capture"):
        power_method: Any = SimpleNamespace(power_sync=object())
        plan.build(fixture, method=power_method, lifecycle=Lifecycle.SINGLE)
    assert not calls


def test_a_deeply_nested_record_is_a_config_error(tmp_path, root):
    path = tmp_path / "deep.json"
    path.write_text("[" * 100000, encoding="utf-8")
    with pytest.raises(ConfigError, match="Cannot read build record"):
        BuildRecord.load(path, roots={"run": root})


def test_build_refuses_a_fixture_of_another_model(plan, fixture, calls, tmp_path):
    other = tmp_path / "other.tflite"
    other.write_bytes(b"other")
    changed = replace(fixture, model=FixtureFile(other, hashlib.sha256(b"other").hexdigest()))
    with pytest.raises(ConfigError, match="does not match the plan's model"):
        build_record(plan, changed, method=_METHOD)
    assert not calls


def test_identity_states_the_lifecycle_that_ran(plan, build):
    base = fixture_run_identity(build)
    identity = BuildRecord(plan, build, Lifecycle.SINGLE).identity

    assert base.run.lifecycle == "carry"
    assert identity.run.lifecycle == "single"
    assert replace(identity, run=base.run) == base
    assert identity.observation_hash is None


def _single(build):
    once = replace(build, iterations=1, warmups=0)
    return replace(once, build_identity=compiled_fixture_identity(once))


def test_save_and_load_round_trip_after_relocation(plan, build, root, tmp_path):
    build = _single(build)
    record = BuildRecord(plan, build, Lifecycle.SINGLE)
    identity = record.identity
    path = record.save(tmp_path / "records" / "kws.json", roots={"run": root})

    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert text == json.dumps(data, indent=2, sort_keys=True) + "\n"
    assert str(root) not in text
    assert data["schema"] == BUILD_RECORD_SCHEMA
    assert data["schema_version"] == BUILD_RECORD_SCHEMA_VERSION
    assert data["lifecycle"] == "single"
    assert data["plan"] == plan.to_dict()
    assert isinstance(data["build"], dict)

    moved = tmp_path / "moved"
    shutil.move(root, moved)
    loaded = BuildRecord.load(path, roots={"run": moved})

    assert loaded.lifecycle is Lifecycle.SINGLE
    assert loaded.plan.to_dict() == plan.to_dict()
    assert loaded.build.build_identity == build.build_identity
    assert loaded.build.flat_binary is not None
    assert loaded.build.flat_binary.path == moved / "app" / "main.bin"
    assert loaded.identity == identity
    assert loaded.save(tmp_path / "again.json", roots={"run": moved}).read_text() == text


def test_save_refuses_a_file_outside_the_roots(plan, build, tmp_path):
    with pytest.raises(ConfigError, match="Cannot save build record"):
        BuildRecord(plan, build, Lifecycle.CARRY).save(
            tmp_path / "r.json", roots={"run": tmp_path / "elsewhere"}
        )


def test_verify_refuses_a_tampered_build(plan, build):
    record = BuildRecord(plan, build, Lifecycle.CARRY)
    record.verify()
    assert build.flat_binary is not None
    build.flat_binary.path.write_bytes(b"changed image")
    with pytest.raises(ConfigError, match="does not verify"):
        record.verify()


def test_verify_refuses_a_lifecycle_the_firmware_does_not_run(plan, build):
    BuildRecord(plan, _single(build), Lifecycle.SINGLE).verify()
    assert build.iterations == 100
    with pytest.raises(ConfigError, match="lifecycle single of its plan runs 1 and 0"):
        BuildRecord(plan, build, Lifecycle.SINGLE).verify()
    with pytest.raises(ConfigError, match="lifecycle carry of its plan runs 100 and 5"):
        BuildRecord(plan, _single(build), Lifecycle.CARRY).verify()


def test_verify_refuses_a_plan_for_another_model(plan, build):
    other = replace(plan, model=replace(plan.model, sha256="0" * 64))
    with pytest.raises(ConfigError, match="model does not match its plan"):
        BuildRecord(other, build, Lifecycle.CARRY).verify()


def test_verify_refuses_a_plan_whose_intent_hash_is_stale(plan, build):
    object.__setattr__(plan, "_intent", "0" * 64)
    with pytest.raises(ConfigError, match="intent hash"):
        BuildRecord(plan, build, Lifecycle.CARRY).verify()


def test_load_verifies_the_plan_against_the_build(plan, build, root, tmp_path):
    path = BuildRecord(plan, build, Lifecycle.CARRY).save(tmp_path / "r.json", roots={"run": root})
    data = json.loads(path.read_text())
    other = replace(plan, model=replace(plan.model, sha256="0" * 64))
    data["plan"] = other.to_dict()
    path.write_text(json.dumps(data))
    with pytest.raises(ConfigError, match="model does not match its plan"):
        BuildRecord.load(path, roots={"run": root})


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
    path = BuildRecord(plan, build, Lifecycle.CARRY).save(tmp_path / "r.json", roots={"run": root})
    edited = _edit(json.loads(path.read_text()), mutation)
    assert edited != path.read_text()
    path.write_text(edited)
    with pytest.raises(ConfigError):
        BuildRecord.load(path, roots={"run": root})


def test_missing_record_or_pinned_file_is_a_config_error(plan, build, root, tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        BuildRecord.load(tmp_path / "absent.json", roots={"run": root})
    path = BuildRecord(plan, build, Lifecycle.CARRY).save(tmp_path / "r.json", roots={"run": root})
    assert build.flat_binary is not None
    build.flat_binary.path.unlink()
    with pytest.raises(ConfigError, match="missing under the given roots"):
        BuildRecord.load(path, roots={"run": root})


@pytest.mark.parametrize(
    "key,value",
    [
        ("schema", "hpx.frozen-run"),
        ("schema_version", BUILD_RECORD_SCHEMA_VERSION + 1),
        ("schema_version", str(BUILD_RECORD_SCHEMA_VERSION)),
        ("schema_version", True),
    ],
)
def test_unknown_schema_or_version_is_refused(plan, build, root, tmp_path, key, value):
    path = BuildRecord(plan, build, Lifecycle.CARRY).save(tmp_path / "r.json", roots={"run": root})
    data = json.loads(path.read_text())
    data[key] = value
    path.write_text(json.dumps(data))
    match = "Not a build record" if key == "schema" else "Unsupported build record version"
    with pytest.raises(ConfigError, match=match):
        BuildRecord.load(path, roots={"run": root})


def test_package_exports_the_record_types():
    assert hpx.BuildRecord is BuildRecord
    assert hpx.Lifecycle is Lifecycle
    assert hpx.RunResult.__module__ == "helia_profiler.build_record"
    assert {hpx.__api_stability__[name] for name in ("BuildRecord", "RunResult", "Lifecycle")} == {
        "experimental"
    }


def test_verify_refuses_a_plan_for_another_engine(tmp_path, build):
    model = tmp_path / "other-engine.tflite"
    model.write_bytes(build.model.read())
    plan = (
        hpx.Session()
        .with_model(model)
        .with_engine("helia-rt")
        .with_target(board="apollo510_evb", toolchain="arm-none-eabi-gcc")
        .with_profiling(iterations=100, warmup=5)
        .freeze()
    )
    with pytest.raises(ConfigError, match="engine 'helia-aot' differs from its plan's 'helia-rt'"):
        BuildRecord(plan, build, Lifecycle.CARRY).verify()


def test_a_lifecycle_given_by_name_is_a_lifecycle(plan, fixture, calls):
    record = plan.build(fixture, method=_METHOD, lifecycle="single")
    assert record.lifecycle is Lifecycle.SINGLE
    assert (calls[0]["config"].profiling.iterations, calls[0]["config"].profiling.warmup) == (1, 0)
