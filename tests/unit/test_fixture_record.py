"""Persisted producer facts survive relocation and refuse stale/tampered input."""

import hashlib
import json
import shutil
from dataclasses import replace

import pytest

from helia_profiler._fixture_build import FIXTURE_CAPABILITIES
from helia_profiler._fixture_record import compiled_fixture_identity
from helia_profiler.fixture import (
    EngineType,
    FixtureBuild,
    FixtureFile,
    FixtureIO,
    FixtureRole,
    FixtureStatePolicy,
    FixtureTensor,
    FixtureTimingScope,
    PerTensorQuantization,
    Placement,
    ToolchainInfo,
    dump_fixture_build,
    fixture_capture_request,
    load_fixture_build,
    supported_fixture_target,
    verify_fixture_build,
)


def _pin(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return FixtureFile(path, hashlib.sha256(data).hexdigest())


def _build(root):
    lock = _pin(root / "evidence" / "nsx.lock", b"lock")
    state = _pin(
        root / "evidence" / "state.json",
        json.dumps({"lock": {"sha256": {"value": lock.sha256}}}).encode(),
    )
    tensor = FixtureTensor("signal", 0, "int8", (1, 4), PerTensorQuantization(0.5, 0))
    output = replace(tensor, name="score", index=3, shape=(1, 2))
    build = FixtureBuild(
        fixture_identity="a" * 64,
        app_dir=root / "app",
        binary=_pin(root / "app" / "main.elf", b"elf"),
        generated_sources=(_pin(root / "app" / "main.cc", b"source"),),
        expected=None,
        built=True,
        runtime_manifest=None,
        flat_binary=_pin(root / "app" / "main.bin", b"image"),
        dependency_lock=lock,
        link_map=_pin(root / "app" / "main.map", b"map"),
        timing_scope=FixtureTimingScope.INVOKE_ONLY,
        engine=EngineType.HELIA_AOT,
        build_identity=None,
        iterations=100,
        warmups=5,
        intent_identity="b" * 64,
        target=supported_fixture_target(),
        toolchain=ToolchainInfo("clang", "22.1.0", "3.31"),
        dependency_state=state,
        state_policy=FixtureStatePolicy.CARRY,
        arena_location=Placement.SRAM,
        weights_location=Placement.MRAM,
        model=_pin(root / "input" / "model.tflite", b"model"),
        inputs=(
            FixtureIO(tensor, _pin(root / "input" / "signal.bin", bytes(4))),
            FixtureIO(
                replace(tensor, name="cache", index=1),
                _pin(root / "input" / "cache.bin", b"\x80" * 4),
                FixtureRole.AUX,
            ),
        ),
        output_tensors=(output, replace(tensor, name="cache_out", index=4)),
        arena_capacity=32768,
        capabilities=(("int8", FIXTURE_CAPABILITIES[EngineType.HELIA_AOT]["int8"]),),
        clock_profile="lp",
        cpu_hz=96000000,
        timer_hz=32768,
    )
    return replace(build, build_identity=compiled_fixture_identity(build))


@pytest.mark.parametrize("extra_root", ["alias", "nested", "parent"])
def test_record_load_accepts_additional_caller_roots(tmp_path, extra_root):
    root = tmp_path / "run"
    build = _build(root)
    record = dump_fixture_build(build, roots={"suite": root})
    extra = {"alias": root, "nested": root / "app", "parent": tmp_path}[extra_root]
    roots = {"suite": root, "evidence": extra}

    actual = load_fixture_build(record, roots=roots)

    assert actual == build
    assert dump_fixture_build(actual, roots={"suite": root}) == record
    build.flat_binary.path.write_bytes(b"changed image")
    with pytest.raises(ValueError):
        load_fixture_build(record, roots=roots)


@pytest.mark.parametrize("field", ["outputs", "aot_outputs"])
@pytest.mark.parametrize("malformed", [{}, ""])
def test_record_file_collections_require_json_arrays(tmp_path, field, malformed):
    record = json.loads(dump_fixture_build(_build(tmp_path), roots={"run": tmp_path}))
    record["build"][field] = malformed
    with pytest.raises(ValueError):
        load_fixture_build(json.dumps(record), roots={"run": tmp_path})


def test_full_state_record_round_trip_and_relocation(tmp_path):
    root = tmp_path / "original"
    build = _build(root)
    record = dump_fixture_build(build, roots={"run": root})
    assert str(root) not in record
    assert load_fixture_build(record, roots={"run": root}) == build
    moved = tmp_path / "moved"
    shutil.copytree(root, moved)
    shutil.rmtree(root)
    restored = load_fixture_build(record, roots={"run": moved})
    assert restored.build_identity == build.build_identity
    assert restored.inputs[1].data.read() == b"\x80" * 4
    assert restored.inputs[1].role is FixtureRole.AUX
    assert dump_fixture_build(restored, roots={"run": moved}) == record
    request = fixture_capture_request(
        restored, jlink_serial="caller-serial", evidence_dir=tmp_path / "raw", settle_seconds=30
    )
    assert request.elf == restored.binary
    assert request.image == restored.flat_binary
    assert (request.output_size, request.extra_output_sizes) == (2, (4,))
    assert request.arena_capacity is None
    assert request.jlink_serial == "caller-serial"
    assert not (tmp_path / "raw").exists()


@pytest.mark.parametrize(
    "name,value",
    [
        ("iterations", 99),
        ("warmups", 6),
        ("arena_capacity", 16384),
        ("weights_location", "sram"),
        ("state_policy", None),
    ],
)
def test_changed_execution_facts_refuse_before_capture(tmp_path, name, value):
    build = _build(tmp_path)
    record = json.loads(dump_fixture_build(build, roots={"run": tmp_path}))
    record["build"][name] = value
    with pytest.raises(ValueError, match="identity mismatch"):
        load_fixture_build(json.dumps(record), roots={"run": tmp_path})


@pytest.mark.parametrize("field", ["binary", "dependency_lock", "dependency_state", "model"])
def test_changed_or_missing_payload_refuses(tmp_path, field):
    build = _build(tmp_path)
    record = dump_fixture_build(build, roots={"run": tmp_path})
    getattr(build, field).path.write_bytes(b"changed")
    with pytest.raises(ValueError):
        load_fixture_build(record, roots={"run": tmp_path})
    getattr(build, field).path.unlink()
    with pytest.raises(OSError):
        load_fixture_build(record, roots={"run": tmp_path})


def test_resealed_record_does_not_hide_mismatched_state_lock(tmp_path):
    build = _build(tmp_path)
    other = _pin(tmp_path / "other.lock", b"other lock")
    changed = replace(build, dependency_lock=other)
    changed = replace(changed, build_identity=compiled_fixture_identity(changed))
    with pytest.raises(ValueError, match="state/lock"):
        verify_fixture_build(changed)


def test_float_target_address_is_rejected_even_with_matching_identity(tmp_path):
    build = _build(tmp_path)
    record = json.loads(dump_fixture_build(build, roots={"run": tmp_path}))
    changed = replace(
        build, target=replace(build.target, load_address=float(build.target.load_address))
    )
    changed = replace(changed, build_identity=compiled_fixture_identity(changed))
    record["build"]["target"]["load_address"] = changed.target.load_address
    record["build"]["build_identity"] = changed.build_identity
    with pytest.raises(ValueError, match="Integer fixture load address"):
        load_fixture_build(json.dumps(record), roots={"run": tmp_path})
    with pytest.raises(ValueError, match="Integer fixture load address"):
        fixture_capture_request(
            changed, jlink_serial="caller", evidence_dir=tmp_path / "raw", settle_seconds=30
        )


@pytest.mark.parametrize(
    "mutation", ["duplicate", "nan", "unknown", "traversal", "bool_count", "old_api"]
)
def test_malformed_records_are_not_coerced(tmp_path, mutation):
    record = dump_fixture_build(_build(tmp_path), roots={"run": tmp_path})
    data = json.loads(record)
    if mutation == "duplicate":
        record = record.replace('"schema_version":1', '"schema_version":1,"schema_version":1')
    elif mutation == "nan":
        record = record.replace('"iterations":100', '"iterations":NaN')
    else:
        if mutation == "unknown":
            data["build"]["surprise"] = True
        elif mutation == "traversal":
            data["build"]["binary"]["path"] = "../main.elf"
        elif mutation == "bool_count":
            data["build"]["iterations"] = True
        elif mutation == "old_api":
            data["fixture_api"] = [4, 1]
        record = json.dumps(data)
    with pytest.raises(ValueError):
        load_fixture_build(record, roots={"run": tmp_path})


def test_render_only_and_legacy_incomplete_builds_do_not_become_compiled(tmp_path):
    build = _build(tmp_path)
    for changed in (
        replace(build, built=False),
        replace(build, model=None),
        replace(build, output_tensors=()),
    ):
        with pytest.raises(ValueError):
            dump_fixture_build(changed, roots={"run": tmp_path})


@pytest.mark.parametrize("engine", [EngineType.HELIA_AOT, EngineType.TFLM])
def test_psram_record_retains_image_payload_and_capture_facts(tmp_path, engine):
    from helia_profiler.fixture import FixturePsramPayload, FixturePsramWeights

    build = _build(tmp_path)
    build = replace(
        build,
        engine=engine,
        weights_location=Placement.PSRAM,
        runtime_manifest=_pin(tmp_path / "runtime.json", b"{}")
        if engine is EngineType.TFLM
        else None,
        psram_weights=FixturePsramWeights(
            (FixturePsramPayload(0, 4, 123),), build.flat_binary.sha256
        ),
    )
    build = replace(build, build_identity=compiled_fixture_identity(build))
    record = dump_fixture_build(build, roots={"run": tmp_path})
    actual = load_fixture_build(record, roots={"run": tmp_path})
    request = fixture_capture_request(
        actual, jlink_serial="serial", evidence_dir=tmp_path / "raw", settle_seconds=60
    )
    assert request.psram_weights == build.psram_weights
    assert request.arena_capacity == (32768 if engine is EngineType.TFLM else None)
    wrong = replace(actual, flat_binary=_pin(tmp_path / "wrong.bin", b"wrong"))
    wrong = replace(wrong, build_identity=compiled_fixture_identity(wrong))
    with pytest.raises(ValueError, match="exact compiled image"):
        verify_fixture_build(wrong)
