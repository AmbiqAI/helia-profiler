"""RunIdentity of fixture builds: facts from the record, reasoned unknowns, release-stable hashes."""

from __future__ import annotations

import json
from dataclasses import asdict, replace

import pytest
from test_fixture_record import _build, _pin, _sourced
from test_fixture_sources import NS

from helia_profiler._fixture_record import compiled_fixture_identity
from helia_profiler.engines.base import EngineSource
from helia_profiler.fixture import (
    CanonicalEngine,
    CanonicalKernel,
    FixtureCaptureResult,
    FixtureFile,
    FixtureTimingScope,
    Unknown,
    dump_fixture_build,
    fixture_run_identity,
    load_fixture_build,
    read_prepared_runtime_manifest,
)

AOT_COMMIT = "7b92ec8a3ced8459eb68940a42d405536144368e"


def _state(root, build, *, hpx_version="0.2.3", name="state.json"):
    assert build.dependency_lock is not None
    data = {
        "lock": {"sha256": {"value": build.dependency_lock.sha256}},
        "modules": [asdict(NS)],
        "workspace": {
            "baseline_id": "hpx-neuralspotx-0.8.1-2026-10-19.2",
            "inputs": {"hpx_version": hpx_version},
        },
    }
    return _pin(root / "evidence" / name, json.dumps(data).encode())


def _reseal(build, **change):
    build = replace(build, **change)
    return replace(build, build_identity=compiled_fixture_identity(build))


def _aot(root, **state):
    build = _sourced(root)
    return _reseal(
        build,
        dependency_state=_state(root, build, **state),
        engine_source=EngineSource("helia-aot", "0.31.0", AOT_COMMIT),
    )


def test_a_compiled_build_states_what_it_linked(tmp_path):
    identity = fixture_run_identity(_aot(tmp_path))

    assert identity.runtime.engine is CanonicalEngine.HELIA_AOT
    assert (identity.runtime.version, identity.runtime.commit) == ("0.31.0", AOT_COMMIT)
    assert identity.kernels.library is CanonicalKernel.HELIA_CORE
    assert identity.kernels.version == "v7.45.1"
    assert identity.prepared_archive_sha256 is None
    assert identity.baseline_id == "hpx-neuralspotx-0.8.1-2026-10-19.2"
    assert identity.producer == "0.2.3"
    assert identity.build.toolchain_version == "22.1.0" and identity.build.cpu_hz == 96000000
    assert (identity.run.lifecycle, identity.run.iterations) == ("carry", 100)
    assert isinstance(identity.build_hash, str) and isinstance(identity.inputs_sha256, str)


def test_the_build_hash_ignores_the_hpx_release_but_not_the_image(tmp_path):
    first = fixture_run_identity(_aot(tmp_path / "a", hpx_version="0.2.3"))
    later = fixture_run_identity(_aot(tmp_path / "b", hpx_version="0.3.0"))
    assert first.producer != later.producer
    assert first.build_hash == later.build_hash

    build = _aot(tmp_path / "c")
    rebuilt = _reseal(build, flat_binary=_pin(tmp_path / "c" / "app" / "main.bin", b"other"))
    assert fixture_run_identity(rebuilt).build_hash != first.build_hash


def test_identity_survives_a_relocated_record(tmp_path):
    build = _aot(tmp_path / "here")
    original = fixture_run_identity(build)
    record = dump_fixture_build(build, roots={"run": tmp_path / "here"})
    moved = tmp_path / "there"
    (tmp_path / "here").rename(moved)
    loaded = load_fixture_build(record, roots={"run": moved})
    assert fixture_run_identity(loaded) == original


def test_missing_facts_carry_their_reason(tmp_path):
    render_only = replace(
        _build(tmp_path),
        built=False,
        flat_binary=None,
        dependency_lock=None,
        dependency_state=None,
        toolchain=None,
    )
    identity = fixture_run_identity(render_only)
    assert identity.build_hash == Unknown("the build was not compiled")
    assert identity.kernels.library == Unknown("the build was not compiled")
    assert identity.dependency_lock_sha256 == Unknown("the build was not compiled")

    no_workspace = fixture_run_identity(_sourced(tmp_path / "w"))
    assert no_workspace.baseline_id == Unknown("the dependency state records no workspace")
    assert no_workspace.runtime.commit == Unknown("the record states no runtime source")


def test_an_engine_package_without_a_commit_says_so(tmp_path):
    build = _reseal(_aot(tmp_path), engine_source=EngineSource("helia-aot", "0.31.0"))
    commit = fixture_run_identity(build).runtime.commit
    assert commit == Unknown("helia-aot 0.31.0 records no commit")


def test_to_dict_is_json_with_reasoned_unknowns(tmp_path):
    data = fixture_run_identity(_sourced(tmp_path)).to_dict()
    assert json.loads(json.dumps(data)) == data
    assert data["runtime"]["engine"] == "helia-aot"
    assert data["baseline_id"] == {"unknown": "the dependency state records no workspace"}


def _capture(root, status=0, name="out.bin"):
    out = _pin(root / name, b"\x01\x02")
    return FixtureCaptureResult(
        "success", FixtureTimingScope.INVOKE_ONLY, status=status, outputs=(out,)
    )


def test_the_observation_hash_follows_measured_facts_not_paths(tmp_path):
    build = _aot(tmp_path)
    one = fixture_run_identity(build, _capture(tmp_path / "x"))
    moved = fixture_run_identity(build, _capture(tmp_path / "y", name="renamed.bin"))
    failed = fixture_run_identity(build, _capture(tmp_path / "z", status=3))

    assert one.observation_hash == moved.observation_hash != failed.observation_hash
    assert one.build_hash == failed.build_hash
    assert fixture_run_identity(build).observation_hash is None


def _manifest(root, *, stack_schema=2):
    data = {
        "schema_version": stack_schema,
        "archive_sha256": "c" * 64,
        "providers": {
            "helia-rt": {"url": "https://github.com/AmbiqAI/helia-rt", "revision": "a" * 40},
            "ns-cmsis-nn": {"url": "https://github.com/AmbiqAI/ns-cmsis-nn", "revision": "b" * 40},
        },
        "abi": {},
        "headers": {},
        "include_dirs": [],
        "stack": "helia-rt",
        "build": {},
    }
    return _pin(root / "provider-manifest.json", json.dumps(data).encode())


def test_a_prepared_runtime_manifest_reads_without_its_archive(tmp_path):
    facts = read_prepared_runtime_manifest(_manifest(tmp_path))
    assert (facts.stack, facts.archive_sha256) == ("helia-rt", "c" * 64)
    assert [(p.name, p.revision) for p in facts.providers] == [
        ("helia-rt", "a" * 40),
        ("ns-cmsis-nn", "b" * 40),
    ]
    tampered = FixtureFile(_manifest(tmp_path).path, "0" * 64)
    with pytest.raises(ValueError, match="hash mismatch"):
        read_prepared_runtime_manifest(tampered)


def test_a_record_from_the_previous_api_still_loads(tmp_path):
    build = _aot(tmp_path)
    record = json.loads(dump_fixture_build(build, roots={"run": tmp_path}))
    assert record["fixture_api"] == [5, 7]
    record["fixture_api"] = [5, 6]
    assert load_fixture_build(json.dumps(record), roots={"run": tmp_path}) == build
