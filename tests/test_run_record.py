"""A saved run result: save, load and verify it, and its portable view."""

from __future__ import annotations

import importlib.metadata
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
from helia_profiler import (
    FixtureBoardPowerRequest,
    FixtureBuild,
    FixtureCaptureResult,
    FixtureMemory,
    FixtureRailPowerRequest,
    FixtureTiming,
)
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
from helia_profiler.identity import CanonicalEngine, RuntimeIdentity
from helia_profiler.lifecycle import Lifecycle
from helia_profiler.records import ResultReadError
from helia_profiler.run_record import RunResultError, RunResultRecord, _identity
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

    assert isinstance(loaded, RunResultRecord) and loaded.path == path.resolve()
    assert loaded.result == result
    assert loaded.result.verification is not None and loaded.result.verification.matched
    assert loaded.document["location"] == {"root": "run", "path": "evidence-1/result.json"}
    assert loaded.document["compiled_build"]["path"] == "build.json"
    assert str(root) not in path.read_text(encoding="utf-8")
    reference = loaded.document["verification"]["reference_files"][0]["path"]
    assert (root / reference).read_bytes() == bytes([1]) * 2


def test_a_run_directory_moves_as_a_unit(tmp_path, root, record):
    _save(_run(record))
    moved = tmp_path / "elsewhere"
    shutil.move(root, moved)

    loaded = hpx.load_run(moved / "evidence-1" / "result.json", roots=_roots(moved))

    assert loaded.result.capture.outputs[0].path.is_relative_to(moved.resolve())
    assert isinstance(load_result(moved / "evidence-1"), RunResultRecord)
    with pytest.raises(RunResultError, match="records itself") as refused:
        hpx.load_run(moved / "evidence-1" / "result.json", roots=_roots(tmp_path))
    assert refused.value.code == "invalid_path"


def test_load_result_reads_a_run_result_by_its_schema(root, record):
    path = _save(_run(record))

    by_file, by_directory = load_result(path), load_result(path.parent)

    assert isinstance(by_file, RunResultRecord) and isinstance(by_directory, RunResultRecord)
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


def _writer(data: dict[str, Any]) -> None:
    data["hpx_version"] = 3


@pytest.mark.parametrize(
    "edit,code,message",
    [
        (_bump, "identity_mismatch", "differs in observation_hash"),
        (_rehash, "identity_mismatch", "differs in observation_hash"),
        (_unmatch, "identity_mismatch", "output comparison does not recompute"),
        (_schema, "unsupported_schema", None),
        (_version, "unsupported_schema", None),
        (_extra, "invalid_evidence", "Malformed run result"),
        (_resize, "artifact_changed", "is not the file it recorded"),
        (_state, "invalid_evidence", "unknown capture state"),
        (_nested, "invalid_evidence", "Malformed run result timing"),
        (_writer, "invalid_evidence", "hpx_version"),
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
    assert read.value.code == "invalid_path" and "does not lead back to" in str(read.value)


def _missing(root: Path) -> None:
    (root / "evidence-1" / "deployment_output.bin").unlink()


def _changed(root: Path) -> None:
    (root / "evidence-1" / "deployment_output.bin").write_bytes(b"\x09\x09")


def _reference(root: Path) -> None:
    document = json.loads((root / "evidence-1" / "result.json").read_bytes())
    reference = document["verification"]["reference_files"][0]["path"]
    (root / reference).write_bytes(b"\x09\x09")


def _build_edited(root: Path) -> None:
    with (root / "build.json").open("a", encoding="utf-8") as stream:
        stream.write("\n")


def _terminal(root: Path) -> None:
    (root / "evidence-1" / "identity.json").write_text("{}\n\n", encoding="utf-8")


@pytest.mark.parametrize(
    "damage,message",
    [
        (_missing, "is missing"),
        (_changed, "is not the file it recorded"),
        (_reference, "reference output"),
        (_build_edited, "compiled build"),
        (_terminal, "identity.json"),
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

    with pytest.raises(ConfigError, match="differs in observation_hash"):
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
    assert isinstance(loaded, RunResultRecord)

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


def _linked_record(tmp_path: Path, engine: str) -> tuple[RunResultRecord, str]:
    """A loaded run of an *engine* build, and the release its engine version must come from."""
    from tests.test_compiled_build_runtime import LITERT, _linked

    plan, build = _linked(tmp_path, engine)
    loaded = load_result(
        _save(_run(_saved(compiled(plan, build, Lifecycle.CARRY)), verified=False))
    )
    assert isinstance(loaded, RunResultRecord) and plan.runtime is not None
    if engine == "helia-aot":
        # The heliaAOT release that generated the module, as the build recorded it.
        assert build.engine_source is not None
        origin = build.engine_source.version
        assert origin == importlib.metadata.version("helia-aot")
    elif engine == "litert":
        origin = LITERT.version
    else:
        origin = plan.runtime.version
    assert origin is not None
    return loaded, origin


def _executorch_record(tmp_path: Path, engine: str) -> tuple[RunResultRecord, str]:
    """A run whose identity is an ExecuTorch one; no ExecuTorch fixture build is needed to map it."""
    root = tmp_path / "run"
    build = atfe_build(_build(root))
    record = _saved(compiled(plan_for(build, tmp_path / "model.tflite"), build, Lifecycle.CARRY))
    loaded = load_result(_save(_run(record)))
    assert isinstance(loaded, RunResultRecord)
    identity = loaded.result.identity
    runtime = RuntimeIdentity(
        CanonicalEngine.EXECUTORCH, "https://example.invalid/et", "1.0.1", "abc"
    )
    result = replace(loaded.result, identity=replace(identity, runtime=runtime))
    return replace(loaded, result=result), "1.0.1"


@pytest.mark.parametrize(
    "engine,basis,make",
    [
        ("helia-aot", "aot_provider_release", _linked_record),
        ("helia-rt", "linked_runtime_release", _linked_record),
        ("litert", "linked_runtime_release", _linked_record),
        ("executorch", "linked_runtime_release", _executorch_record),
    ],
)
def test_the_engine_version_states_its_basis(tmp_path, engine, basis, make):
    if engine == "helia-aot":
        pytest.importorskip("helia_aot")
    loaded, origin = make(tmp_path, engine)

    view = observations(loaded)

    _validate(view)
    assert view["engine"] == {"name": engine, "version": origin}
    extension = view["extensions"]["helia-profiler"]
    assert extension["engine_version_basis"] == basis
    assert extension["engine_version_unavailable_reason"] is None


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


# -- review round: power, writer, unknowns, runtime, codes -------------------------


def test_the_checks_name_the_output_comparison_only_when_outputs_were_compared(root, record):
    compared = observations(load_result(_save(_run(record))))["verification"]
    path = _save(_run(record, verified=False))
    uncompared = observations(load_result(path))["verification"]

    assert "output-comparison-recomputed" in compared["checks"]
    assert "Outputs were not compared with a reference." not in compared["limitations"]
    assert "output-comparison-recomputed" not in uncompared["checks"]
    assert "Outputs were not compared with a reference." in uncompared["limitations"]
    failed = observations(load_result(_save(_run(record, state="failure"))))["verification"]
    assert "output-comparison-recomputed" not in failed["checks"]


def test_save_refuses_a_rail_request_for_a_build_without_wiring(root, record):
    target = root / "evidence-1" / "result.json"

    with pytest.raises(ConfigError, match="no power wiring"):
        _run(record, power=_rail()).save(target, roots=_roots(root), build=root / "build.json")
    assert not target.exists()


@pytest.mark.parametrize(
    "power,message", [(_SUPPLY, "supply-only"), (None, "needs a FixtureRailPowerRequest")]
)
def test_save_refuses_a_power_build_run_without_its_rail_request(root, build_power, power, message):
    target = root / "evidence-1" / "result.json"

    with pytest.raises(ConfigError, match=message):
        _run(build_power, power=power).save(target, roots=_roots(root), build=root / "build.json")
    assert not target.exists()


def test_load_refuses_a_power_request_the_build_cannot_take(root, build_power):
    path = _save(_run(build_power, power=_rail(), energy=_energy(root / "evidence-1")))
    _edit(path, lambda data: data.update(power=None))

    with pytest.raises(RunResultError, match="needs a FixtureRailPowerRequest") as refused:
        hpx.load_run(path, roots=_roots(root))
    assert refused.value.code == "invalid_evidence"


def test_a_tampered_rail_trace_is_refused(root, build_power):
    path = _save(_run(build_power, power=_rail(), energy=_energy(root / "evidence-1")))
    (root / "evidence-1" / "rail-power.json").write_bytes(b"[]")

    with pytest.raises(ResultReadError) as read:
        load_result(path)
    assert read.value.code == "artifact_changed" and "rail-power.json" in str(read.value)


def test_the_result_records_the_hpx_that_wrote_it(root, record):
    loaded = load_result(_save(_run(record)))
    assert isinstance(loaded, RunResultRecord)

    view = observations(loaded)

    assert loaded.hpx_version == hpx.__version__ == loaded.document["hpx_version"]
    assert view["producer"]["version"] == hpx.__version__
    producer = loaded.result.identity.producer
    assert view["extensions"]["helia-profiler"]["build_producer"] == (
        producer if isinstance(producer, str) else {"unknown": producer.reason}
    )


def test_an_unknown_fact_matches_whatever_its_reason(root, record):
    path = _save(_run(record))
    _edit(path, lambda data: data["identity"].update(baseline_id={"unknown": "reworded"}))

    assert hpx.load_run(path, roots=_roots(root)).result.succeeded
    _edit(path, lambda data: data["identity"].update(baseline_id="b" * 8))
    with pytest.raises(RunResultError, match="differs in baseline_id") as refused:
        hpx.load_run(path, roots=_roots(root))
    assert refused.value.code == "identity_mismatch"


def test_a_missing_prepared_runtime_is_named_with_its_prepare_command(tmp_path, root):
    from tests.run_api_helpers import linking
    from tests.test_compiled_build_runtime import LITERT

    assert LITERT.kernels is not None
    cache = hpx.hpx_cache_root()
    build = linking(
        atfe_build(_build(root)), cache, "litert", LITERT.source.commit, LITERT.kernels.commit
    )
    record = compiled(
        plan_for(build, tmp_path / "m.tflite", engine="litert"), build, Lifecycle.CARRY
    )
    roots = {"run": root, "cache": cache}
    record.save(root / "build.json", roots=roots)
    capture = _capture(record.build, root / "evidence-1")
    path = RunResult(record, capture, _identity(record, capture, None), None).save(
        root / "evidence-1" / "result.json", roots=roots, build=root / "build.json"
    )
    shutil.rmtree(cache)

    with pytest.raises(RunResultError) as refused:
        hpx.load_run(path, roots=roots)
    assert refused.value.code == "runtime_missing"
    assert f"hpx runtimes prepare litert {LITERT.version}" in str(refused.value)
    with pytest.raises(ResultReadError) as read:
        load_result(path)
    assert read.value.code == "runtime_missing"


@pytest.mark.parametrize("roots", [{}, {"run": "not a Path"}])
def test_load_run_refuses_unusable_roots_with_a_code(root, record, roots):
    path = _save(_run(record))

    with pytest.raises(RunResultError) as refused:
        hpx.load_run(path, roots=roots)
    assert refused.value.code == "invalid_path"


def test_hpx_exports_the_run_result_error():
    assert hpx.RunResultError is RunResultError and issubclass(RunResultError, ConfigError)


def test_the_workload_is_named_by_its_pinned_model_file(root, record):
    loaded = load_result(_save(_run(record)))
    assert isinstance(loaded, RunResultRecord)
    plan = loaded.result.compiled.plan
    windows = replace(plan, model=replace(plan.model, path="C:\\models\\other.tflite"))
    moved = replace(loaded.result, compiled=replace(loaded.result.compiled, plan=windows))

    view = observations(replace(loaded, result=moved))

    assert view["workload"]["name"] == "model.tflite"


def test_a_result_that_is_not_json_is_refused(root, record):
    path = _save(_run(record))
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(RunResultError) as refused:
        hpx.load_run(path, roots=_roots(root))
    assert refused.value.code == "invalid_json"
    with pytest.raises(ResultReadError) as read:
        load_result(path)
    assert read.value.code == "invalid_json"


def test_a_result_that_changes_while_it_is_read_is_refused(root, record, monkeypatch):
    from helia_profiler import records

    path = _save(_run(record))
    real = records.read_run

    def read_then_change(location, raw):
        loaded = real(location, raw)
        location.write_bytes(raw + b"\n")
        return loaded

    monkeypatch.setattr(records, "read_run", read_then_change)
    with pytest.raises(ResultReadError) as read:
        load_result(path)
    assert read.value.code == "publication_changed"


def test_a_directory_with_a_run_result_and_a_profile_manifest_is_refused(root, record):
    path = _save(_run(record))
    (path.parent / "result_manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ResultReadError) as read:
        load_result(path.parent)
    assert read.value.code == "ambiguous_location"
    assert isinstance(load_result(path), RunResultRecord)


def test_a_failed_capture_with_an_output_comparison_is_refused(root, record):
    result = _run(record)
    path = _save(result)
    failed = replace(result.capture, state="failure")
    identity = _identity(record, failed, None).to_dict()

    def fail(data: dict[str, Any]) -> None:
        data["capture"]["state"] = "failure"
        data["identity"] = identity

    _edit(path, fail)
    with pytest.raises(RunResultError, match="failed capture has no output comparison") as refused:
        hpx.load_run(path, roots=_roots(root))
    assert refused.value.code == "invalid_evidence"


@pytest.mark.parametrize(
    "field,value",
    [
        ("sync_gpio_pin", 30),
        ("state_gpio_pin", 37),
        ("go_gpio_pin", 15),
        ("serial", "OTHER-INSTRUMENT"),
        ("rail", "VDD"),
    ],
)
@pytest.mark.parametrize("operation", ["save", "load"])
def test_saved_power_must_match_build_and_energy(root, build_power, field, value, operation):
    result = _run(build_power, power=_rail(), energy=_energy(root / "evidence-1"))
    if operation == "save":
        assert isinstance(result.power, FixtureRailPowerRequest)
        result = replace(result, power=replace(result.power, **{field: value}))
        with pytest.raises(ConfigError):
            _save(result)
    else:
        path = _save(result)
        _edit(path, lambda data: data["power"].update({field: value}))
        with pytest.raises(RunResultError) as refused:
            hpx.load_run(path, roots=_roots(root))
        assert refused.value.code == "invalid_evidence"


@pytest.mark.parametrize("power", [None, _SUPPLY])
@pytest.mark.parametrize("operation", ["save", "load"])
def test_timing_and_supply_only_runs_cannot_retain_energy(root, record, power, operation):
    energy = _energy(root / "evidence-1")
    result = _run(record, power=power)
    if operation == "save":
        with pytest.raises(ConfigError, match="successful rail capture"):
            _save(replace(result, capture=replace(result.capture, rail_energy=energy)))
    else:
        from helia_profiler.run_record import _encode, _Files

        path = _save(result)
        encoded = _encode(energy, _Files(_roots(root)).file)
        _edit(path, lambda data: data["capture"].update(rail_energy=encoded))
        with pytest.raises(RunResultError, match="successful rail capture") as refused:
            hpx.load_run(path, roots=_roots(root))
        assert refused.value.code == "invalid_evidence"


@pytest.mark.parametrize(
    "field,value",
    [
        ("instrument_serial", "OTHER"),
        ("rail", "VDD"),
        ("iterations", 99),
        ("scope", "net"),
        ("subtraction", "idle"),
        ("completion", None),
        ("duration_s", -1),
        ("energy_per_iteration_j", 1),
        ("mean_power_w", 1),
    ],
)
@pytest.mark.parametrize("operation", ["save", "load"])
def test_saved_energy_facts_are_consistent(root, build_power, field, value, operation):
    result = _run(build_power, power=_rail(), energy=_energy(root / "evidence-1"))
    if operation == "save":
        assert result.capture.rail_energy is not None
        energy = replace(result.capture.rail_energy, **{field: value})
        with pytest.raises(ConfigError):
            _save(replace(result, capture=replace(result.capture, rail_energy=energy)))
    else:
        path = _save(result)
        _edit(path, lambda data: data["capture"]["rail_energy"].update({field: value}))
        with pytest.raises(RunResultError) as refused:
            hpx.load_run(path, roots=_roots(root))
        assert refused.value.code == "invalid_evidence"


@pytest.mark.parametrize(
    "field,value",
    [
        ("protocol_version", 1),
        ("counter_domain", "host"),
        ("sample_rate", 0),
        ("ready_sample", 30),
        ("gate_fall_sample", 20),
        ("done_sample", 40),
    ],
)
@pytest.mark.parametrize("operation", ["save", "load"])
def test_saved_energy_requires_consistent_completion(root, build_power, field, value, operation):
    result = _run(build_power, power=_rail(), energy=_energy(root / "evidence-1"))
    if operation == "save":
        energy = result.capture.rail_energy
        assert energy is not None and energy.completion is not None
        energy = replace(energy, completion=replace(energy.completion, **{field: value}))
        with pytest.raises(ConfigError, match="batch completion"):
            _save(replace(result, capture=replace(result.capture, rail_energy=energy)))
    else:
        path = _save(result)
        _edit(
            path, lambda data: data["capture"]["rail_energy"]["completion"].update({field: value})
        )
        with pytest.raises(RunResultError, match="batch completion") as refused:
            hpx.load_run(path, roots=_roots(root))
        assert refused.value.code == "invalid_evidence"


def test_failed_rail_capture_without_energy_remains_readable(root, build_power):
    result = _run(build_power, power=_rail(), state="failure")
    path = _save(result)
    assert hpx.load_run(path, roots=_roots(root)).result == result
    _validate(observations(load_result(path)))


@pytest.mark.parametrize("error", [17, {"message": "failed"}, ["failed"], False])
@pytest.mark.parametrize("operation", ["save", "load"])
def test_capture_error_must_be_text_or_null(root, record, error, operation):
    result = _run(record, state="failure")
    if operation == "save":
        with pytest.raises(ConfigError, match="capture.error must be text or null"):
            _save(replace(result, capture=replace(result.capture, error=error)))
    else:
        path = _save(result)
        _edit(path, lambda data: data["capture"].update(error=error))
        with pytest.raises(RunResultError, match="capture.error must be text or null") as refused:
            hpx.load_run(path, roots=_roots(root))
        assert refused.value.code == "invalid_evidence"


@pytest.mark.parametrize("failure", ["reference", "document", None])
def test_replacement_publication_preserves_previous_record_on_failure(
    root, record, monkeypatch, failure
):
    from helia_profiler import run_record

    original = _run(record)
    path = _save(original)
    original_bytes = path.read_bytes()
    assert original.verification is not None
    reference = replace(
        original.verification.reference,
        outputs=tuple(bytes([9]) * len(data) for data in original.verification.reference.outputs),
    )
    comparison = compare_outputs(
        record.build.output_tensors,
        reference,
        [output.read() for output in original.capture.outputs],
        Tolerance(0),
    )
    replacement = replace(original, verification=comparison)
    write = run_record.write_bytes_atomically
    writes = []

    def fail_reference(target, data):
        writes.append(target)
        if len(writes) == 2:
            raise OSError("reference publication failed")
        write(target, data)

    def fail_document(target, text):
        raise OSError("document publication failed")

    if failure == "reference":
        monkeypatch.setattr(run_record, "write_bytes_atomically", fail_reference)
    elif failure == "document":
        monkeypatch.setattr(run_record, "write_text_atomically", fail_document)
    if failure is None:
        _save(replacement)
        assert hpx.load_run(path, roots=_roots(root)).result == replacement
    else:
        with pytest.raises(ConfigError, match="publication failed"):
            _save(replacement)
        assert path.read_bytes() == original_bytes
        assert hpx.load_run(path, roots=_roots(root)).result == original
        if failure == "reference":
            assert len(writes) == 2
