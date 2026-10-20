"""A saved run result: save, load and verify it, and its portable view."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

import helia_profiler as hpx
from helia_profiler.api import export_result, inspect_result, load_result, observations
from helia_profiler.compiled_build import CompiledBuild, RunResult
from helia_profiler.errors import ConfigError
from helia_profiler.expect import (
    ReferenceOrigin,
    ReferenceOutputs,
    Tolerance,
    Verification,
    compare_outputs,
)
from helia_profiler.fixture import (
    FixtureBoardPowerRequest,
    FixtureBuild,
    FixtureCaptureResult,
    FixtureMemory,
    FixtureRailPowerRequest,
    FixtureTiming,
)
from helia_profiler.lifecycle import Lifecycle
from helia_profiler.records import ResultReadError
from helia_profiler.run_record import RunRecord, RunResultError, _identity
from tests.run_api_helpers import ATFE_OUTPUT, atfe_build, compiled, plan_for
from tests.test_run_power import _energy, _powered, _rail
from tests.unit.test_fixture_record import _build, _pin

CONTRACT = Path(__file__).parent / "fixtures" / "result_contract_v1"
_SUPPLY = FixtureBoardPowerRequest("joulescope", "JS320-001", "VDD")
_Power = FixtureRailPowerRequest | FixtureBoardPowerRequest | None


@pytest.fixture(autouse=True)
def host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "helia_profiler.hostenv._proc.tool_output", lambda command, *, timeout_s: ATFE_OUTPUT
    )
    monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "run"


@pytest.fixture
def record(tmp_path: Path, root: Path) -> CompiledBuild:
    build = atfe_build(_build(root))
    return _saved(compiled(plan_for(build, tmp_path / "model.tflite"), build, Lifecycle.CARRY))


def _roots(root: Path) -> dict[str, Path]:
    return {"run": root}


def _home(record: CompiledBuild) -> Path:
    assert record.build.binary is not None
    return record.build.binary.path.parents[1]


def _saved(record: CompiledBuild) -> CompiledBuild:
    root = _home(record)
    record.save(root / "build.json", roots=_roots(root))
    return record


def _capture(
    build: FixtureBuild, evidence: Path, *, energy: Any = None, state: str = "success"
) -> FixtureCaptureResult:
    if state != "success":
        receipt = _pin(evidence / "identity.json", b"{}\n")
        return FixtureCaptureResult(
            "failure", build.timing_scope, artifacts=(receipt,), error="Probe enumeration mismatch"
        )
    outputs = tuple(
        _pin(
            evidence / f"deployment_output{'' if i == 0 else f'_{i}'}.bin",
            bytes([i + 1]) * t.size_bytes,
        )
        for i, t in enumerate(build.output_tensors)
    )
    scope = build.timing_scope
    return FixtureCaptureResult(
        "success",
        scope,
        output=outputs[0],
        status=0,
        checksum=7,
        timing=FixtureTiming(65536, build.iterations, build.warmups, 32768, 4, 96_000_000, scope),
        memory=FixtureMemory(4096, 1024, 2048, 3072),
        artifacts=(_pin(evidence / "identity.json", b"{}\n"), *outputs),
        outputs=outputs,
        rail_energy=energy,
    )


def _verification(build: FixtureBuild, capture: FixtureCaptureResult) -> Verification:
    observed = [item.read() for item in capture.outputs]
    reference = ReferenceOutputs(ReferenceOrigin("files"), tuple(observed))
    return compare_outputs(build.output_tensors, reference, observed, Tolerance(0))


def _run(
    record: CompiledBuild,
    *,
    power: _Power = None,
    energy: Any = None,
    state: str = "success",
    verified: bool = True,
) -> RunResult:
    capture = _capture(record.build, _home(record) / "evidence-1", energy=energy, state=state)
    verification = _verification(record.build, capture) if verified and state == "success" else None
    return RunResult(record, capture, _identity(record, capture, power), verification, power)


def _save(result: RunResult) -> Path:
    root = _home(result.compiled)
    return result.save(
        root / "evidence-1" / "result.json", roots=_roots(root), build=root / "build.json"
    )


def _edit(path: Path, change: Callable[[dict[str, Any]], None]) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _validate(view: dict[str, Any]) -> None:
    schema = json.loads((CONTRACT / "result.schema.json").read_bytes())
    Draft202012Validator(schema).validate(view)


def _items(view: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in view["observations"]}


# -- save and load ---------------------------------------------------------------


def test_a_saved_run_loads_back_as_it_was(root, record):
    result = _run(record)
    path = _save(result)

    loaded = hpx.load_run(path, roots=_roots(root))

    assert isinstance(loaded, RunRecord) and loaded.path == path.resolve()
    assert loaded.result == result
    assert loaded.result.verification is not None and loaded.result.verification.matched
    assert loaded.document["location"] == {"root": "run", "path": "evidence-1/result.json"}
    assert loaded.document["compiled_build"]["path"] == "build.json"
    assert str(root) not in path.read_text(encoding="utf-8")
    assert (root / "evidence-1" / "result.reference_0.bin").read_bytes() == bytes([1]) * 2


def test_a_run_directory_moves_as_a_unit(tmp_path, root, record):
    _save(_run(record))
    moved = tmp_path / "elsewhere"
    shutil.move(root, moved)

    loaded = hpx.load_run(moved / "evidence-1" / "result.json", roots=_roots(moved))

    assert loaded.result.capture.outputs[0].path.is_relative_to(moved.resolve())
    assert isinstance(load_result(moved / "evidence-1"), RunRecord)
    with pytest.raises(RunResultError, match="records itself") as refused:
        hpx.load_run(moved / "evidence-1" / "result.json", roots=_roots(tmp_path))
    assert refused.value.code == "invalid_path"


def test_load_result_reads_a_run_result_by_its_schema(root, record):
    path = _save(_run(record))

    by_file, by_directory = load_result(path), load_result(path.parent)

    assert isinstance(by_file, RunRecord) and isinstance(by_directory, RunRecord)
    assert by_file.sha256 == by_directory.sha256
    assert inspect_result(path).result == by_file


def test_a_failed_capture_round_trips(root, record):
    result = _run(record, state="failure")
    path = _save(result)

    loaded = hpx.load_run(path, roots=_roots(root)).result

    assert loaded == result and not loaded.succeeded
    view = observations(load_result(path))
    _validate(view)
    assert view["outcome"] == {
        "status": "failed",
        "stage": "capture",
        "reason": "Probe enumeration mismatch",
    }
    assert {item["unavailable_reason"] for item in view["observations"]} == {"capture_failed"}


def _bump(data: dict[str, Any]) -> None:
    data["capture"]["timing"]["ticks"] += 1


def _rehash(data: dict[str, Any]) -> None:
    data["identity"]["observation_hash"] = "hpx.observation/1:sha256:" + "0" * 64


def _unmatch(data: dict[str, Any]) -> None:
    data["verification"]["tensors"][0]["mismatched_elements"] = 1


def _schema(data: dict[str, Any]) -> None:
    data["schema"] = "hpx.compiled-build"


def _version(data: dict[str, Any]) -> None:
    data["schema_version"] = 2


def _extra(data: dict[str, Any]) -> None:
    data["notes"] = "edited"


def _resize(data: dict[str, Any]) -> None:
    data["capture"]["outputs"][1]["size_bytes"] += 1


def _state(data: dict[str, Any]) -> None:
    data["capture"]["state"] = "pending"


def _nested(data: dict[str, Any]) -> None:
    data["capture"]["timing"]["notes"] = "edited"


@pytest.mark.parametrize(
    "edit,code,message",
    [
        (_bump, "identity_mismatch", "observation_hash differ"),
        (_rehash, "identity_mismatch", "observation_hash differ"),
        (_unmatch, "identity_mismatch", "output comparison does not recompute"),
        (_schema, "unsupported_schema", None),
        (_version, "unsupported_schema", None),
        (_extra, "invalid_evidence", "Malformed run result"),
        (_resize, "artifact_changed", "is not the file it recorded"),
        (_state, "invalid_evidence", "unknown capture state"),
        (_nested, "invalid_evidence", "Malformed run result timing"),
    ],
)
def test_an_edited_result_is_refused(root, record, edit, code, message):
    path = _save(_run(record))
    _edit(path, edit)

    with pytest.raises(ResultReadError) as read:
        load_result(path)
    assert read.value.code == code
    with pytest.raises(RunResultError) as refused:
        hpx.load_run(path, roots=_roots(root))
    assert refused.value.code == code and isinstance(refused.value, ConfigError)
    assert message is None or message in str(refused.value)


def test_a_renamed_result_is_refused(root, record):
    path = _save(_run(record))
    renamed = path.with_name("copy.json")
    shutil.copy(path, renamed)

    with pytest.raises(ResultReadError) as read:
        load_result(renamed)
    assert read.value.code == "invalid_path" and "records itself" in str(read.value)


def test_a_location_deeper_than_the_file_is_refused(root, record):
    path = _save(_run(record))
    _edit(path, lambda data: data["location"].update(path="/".join(["x"] * 64) + "/r.json"))

    with pytest.raises(ResultReadError) as read:
        load_result(path)
    assert read.value.code == "invalid_path"


def _missing(root: Path) -> None:
    (root / "evidence-1" / "deployment_output.bin").unlink()


def _changed(root: Path) -> None:
    (root / "evidence-1" / "deployment_output.bin").write_bytes(b"\x09\x09")


def _reference(root: Path) -> None:
    (root / "evidence-1" / "result.reference_0.bin").write_bytes(b"\x09\x09")


def _build_edited(root: Path) -> None:
    with (root / "build.json").open("a", encoding="utf-8") as stream:
        stream.write("\n")


@pytest.mark.parametrize(
    "damage,message",
    [
        (_missing, "is missing"),
        (_changed, "is not the file it recorded"),
        (_reference, "reference output"),
        (_build_edited, "compiled build"),
    ],
)
def test_a_missing_or_changed_file_is_refused(root, record, damage, message):
    path = _save(_run(record))
    damage(root)

    with pytest.raises(RunResultError, match=message) as refused:
        hpx.load_run(path, roots=_roots(root))
    assert refused.value.code == "artifact_changed"
    with pytest.raises(ResultReadError) as read:
        load_result(path)
    assert read.value.code == "artifact_changed"


def test_a_different_compiled_build_is_refused(tmp_path, root, record):
    path = _save(_run(record))
    other = replace(record.build, iterations=1, warmups=0)
    plan = plan_for(other, tmp_path / "model.tflite")
    compiled(plan, atfe_build(other), Lifecycle.SINGLE).save(
        root / "build.json", roots=_roots(root)
    )

    with pytest.raises(RunResultError, match="compiled build") as refused:
        hpx.load_run(path, roots=_roots(root))
    assert refused.value.code == "artifact_changed"


def test_save_refuses_a_run_that_does_not_recompute(tmp_path, root, record):
    result = _run(record)
    target = root / "evidence-1" / "result.json"

    with pytest.raises(ConfigError, match="observation_hash differ"):
        replace(result, identity=record.identity).save(
            target, roots=_roots(root), build=root / "build.json"
        )
    assert result.verification is not None
    tensors = (replace(result.verification.tensors[0], mismatched_elements=1),)
    with pytest.raises(ConfigError, match="verification does not recompute"):
        replace(result, verification=replace(result.verification, tensors=tensors)).save(
            target, roots=_roots(root), build=root / "build.json"
        )
    failed = replace(result.capture, state="failure")
    with pytest.raises(ConfigError, match="failed capture has no output comparison"):
        replace(result, capture=failed, identity=_identity(record, failed, None)).save(
            target, roots=_roots(root), build=root / "build.json"
        )
    assert not target.exists()


def test_save_refuses_another_compiled_build(tmp_path, root, record):
    other = tmp_path / "other"
    build = atfe_build(_build(other), warmups=2)
    _saved(compiled(plan_for(build, tmp_path / "m.tflite", warmup=2), build, Lifecycle.CARRY))

    with pytest.raises(ConfigError, match="is not the compiled build this run captured from"):
        _run(record).save(
            root / "evidence-1" / "result.json",
            roots={"run": root, "other": other},
            build=other / "build.json",
        )


def test_save_refuses_a_path_under_no_root(tmp_path, root, record):
    with pytest.raises(ConfigError, match="no supplied root"):
        _run(record).save(tmp_path / "result.json", roots=_roots(root), build=root / "build.json")


# -- the portable view -----------------------------------------------------------


def test_a_timing_only_run_maps_latency_memory_and_states_no_energy(root, record):
    path = _save(_run(record))
    loaded = load_result(path)
    assert isinstance(loaded, RunRecord)

    view = observations(loaded)

    _validate(view)
    assert view["producer"]["mapping_version"] == "hpx-run-1"
    assert view["native_result"]["identity"] == loaded.sha256
    latency = _items(view)["latency.invoke.v1"]
    assert latency["value"] == pytest.approx(65536 / 32768 / 100 * 1000)
    assert (latency["scope"], latency["statistic"], latency["sample_count"]) == (
        "invoke_only",
        "mean",
        100,
    )
    assert latency["basis"]["transformation"] == "ticks / timer_hz / iterations * 1000"
    assert {(c["role"], c["domain"], c["value_hz"]) for c in latency["basis"]["clocks"]} == {
        ("configured", "timer", 32768),
        ("reported", "timer", 32768),
    }
    for name in ("energy.per_inference.v1", "power.mean.v1"):
        assert _items(view)[name]["unavailable_reason"] == "energy_not_measured_timing_only"
    assert _items(view)["memory.arena_capacity.v1"]["value"] == 4096
    assert _items(view)["memory.arena_used_after_invoke.v1"]["value"] == 3072
    identity = loaded.result.identity
    assert view["workload"] == {
        "name": "model.tflite",
        "model_identity": identity.model_sha256,
        "input_identity": identity.inputs_hash,
        "state_policy": "carry",
    }
    extension = view["extensions"]["helia-profiler"]
    assert extension["semantic_hashes"]["observation_hash"] == identity.observation_hash
    assert extension["run"]["first_invoke_window"] == identity.run.first_invoke_window
    assert extension["output_comparison"]["matched"] is True
    _pointers_resolve(path, view)


def _pointers_resolve(path: Path, view: dict[str, Any]) -> None:
    document = json.loads(path.read_bytes())
    for item in view["observations"]:
        sources = [*(item["basis"] or {}).get("sources", ())]
        sources += [c["source"] for c in (item["basis"] or {}).get("clocks", ())]
        for source in sources:
            assert source["artifact"] == "native-result"
            value = document
            for part in source["pointer"].split("/")[1:]:
                value = value[part]
            assert value is not None


def test_a_rail_energy_run_maps_energy_and_mean_power(tmp_path, root, build_power):
    record = build_power
    energy = _energy(root / "evidence-1")
    path = _save(_run(record, power=_rail(), energy=energy))

    view = observations(load_result(path))

    _validate(view)
    items = _items(view)
    per_inference = items["energy.per_inference.v1"]
    assert per_inference["value"] == pytest.approx(50.0) and per_inference["unit"] == "uJ"
    assert per_inference["sample_count"] == 100 and per_inference["domain"] == "system"
    assert per_inference["scope"] == "gross_rail_restore_and_invoke"
    assert "artifact:evidence-1/rail-power.json" in per_inference["evidence"]
    mean = items["power.mean.v1"]
    assert mean["value"] == pytest.approx(10.0) and mean["unit"] == "mW"
    assert items["latency.restore_invoke.v1"]["scope"] == "restore_and_invoke"
    roles = {a["location"]: a["role"] for a in view["native_result"]["artifacts"]}
    assert roles["evidence-1/rail-power.json"] == "hpx.rail-trace"
    assert view["extensions"]["helia-profiler"]["power_request"] == "rail"
    _pointers_resolve(path, view)


@pytest.fixture
def build_power(tmp_path: Path, root: Path) -> CompiledBuild:
    return _saved(_powered(tmp_path, atfe_build(_build(root))))


def test_a_supply_only_run_states_no_energy(root, record):
    path = _save(_run(record, power=_SUPPLY))

    view = observations(load_result(path))

    _validate(view)
    for name in ("energy.per_inference.v1", "power.mean.v1"):
        assert _items(view)[name]["unavailable_reason"] == "energy_not_measured_supply_only"
    assert view["extensions"]["helia-profiler"]["power_request"] == "supply"


def test_an_unknown_engine_version_stays_null_with_its_reason(root, record):
    view = observations(load_result(_save(_run(record))))

    extension = view["extensions"]["helia-profiler"]
    assert view["engine"] == {"name": "helia-aot", "version": None}
    assert extension["engine_version_basis"] == "aot_provider_release"
    assert extension["engine_version_unavailable_reason"] == "the record states no runtime source"


@pytest.mark.parametrize(
    "engine,basis", [("helia-aot", "aot_provider_release"), ("litert", "linked_runtime_release")]
)
def test_the_engine_version_states_its_basis(tmp_path, engine, basis):
    from tests.test_compiled_build_runtime import _linked

    plan, build = _linked(tmp_path, engine)
    record = _saved(compiled(plan, build, Lifecycle.CARRY))
    loaded = load_result(_save(_run(record, verified=False)))

    view = observations(loaded)

    _validate(view)
    assert isinstance(loaded, RunRecord)
    version = loaded.result.identity.runtime.version
    assert isinstance(version, str) and view["engine"] == {"name": engine, "version": version}
    extension = view["extensions"]["helia-profiler"]
    assert extension["engine_version_basis"] == basis
    assert extension["engine_version_unavailable_reason"] is None
    assert extension["output_comparison"] is None


def test_results_show_and_export_work_for_a_run(tmp_path, root, record):
    from helia_profiler.cli.results_cmd import app

    path = _save(_run(record))

    shown = CliRunner().invoke(app, ["show", str(path.parent)])
    exported = CliRunner().invoke(app, ["export", str(path), "--output", str(tmp_path / "v.json")])

    assert shown.exit_code == 0 and exported.exit_code == 0, shown.output + exported.output
    view = observations(load_result(path))
    assert json.loads(shown.stdout) == view
    assert json.loads((tmp_path / "v.json").read_bytes()) == view
    with pytest.raises(FileExistsError):
        export_result(load_result(path), tmp_path / "v.json")
