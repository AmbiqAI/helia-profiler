"""RunIdentity of fixture builds: facts from the record, reasoned unknowns, release-stable hashes."""

from __future__ import annotations

import json
from dataclasses import asdict, replace

import pytest
from test_fixture_record import _build, _pin, _sourced
from test_fixture_sources import NS

from helia_profiler import SemanticHash
from helia_profiler._fixture_record import compiled_fixture_identity
from helia_profiler.engines import EngineType
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


def _lock(*, stamp="2026-10-19T11:59:32+00:00", tool="0.2.3", commit="9" * 40):
    return f"""schema_version: 4
targets:
  apollo510_evb:
    generated_at: '{stamp}'
    nsx_tool:
      version: {tool}
    manifest:
      path: nsx.yml
      hash: sha256:{"1" * 64}
    target:
      board: apollo510_evb
      soc: apollo510
      toolchain: atfe
    modules:
      nsx-cmsis-nn:
        project: ns-cmsis-nn
        kind: git
        constraint: {commit}
        resolved:
          url: https://github.com/AmbiqAI/ns-cmsis-nn.git
          commit: {commit}
          vendored_at: modules/ns-cmsis-nn
          content_hash: sha256:{"2" * 64}
          acquired_at: '{stamp}'
      nsx-tooling:
        project: neuralspotx
        kind: packaged
        constraint: packaged
        resolved:
          tool_version: {tool}
          vendored_at: cmake/nsx
          content_hash: sha256:{"3" * 64}
          acquired_at: '{stamp}'
""".encode()


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


def _aot(root, lock=None, **state):
    build = _sourced(root)
    build = replace(build, dependency_lock=_pin(root / "evidence" / "nsx.lock", lock or _lock()))
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
    assert SemanticHash.parse(identity.build_hash).kind == "build"
    assert len(identity.inputs_sha256) == 64


def test_the_build_hash_ignores_the_hpx_release_but_not_the_image(tmp_path):
    first = fixture_run_identity(_aot(tmp_path / "a", hpx_version="0.2.3"))
    later = fixture_run_identity(
        _aot(
            tmp_path / "b",
            _lock(stamp="2026-11-02T08:00:00+00:00", tool="0.2.4"),
            hpx_version="0.3.0",
        )
    )
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
    assert isinstance(one.observation_hash, str)
    assert SemanticHash.parse(one.observation_hash).kind == "observation"
    assert one.build_hash == failed.build_hash
    assert fixture_run_identity(build).observation_hash is None


def _manifest(root):
    data = {
        "schema_version": 2,
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
    assert record["fixture_api"] == [7, 0]
    for previous in ([5, 6], [5, 7], [5, 8], [6, 0], [6, 1]):
        record["fixture_api"] = previous
        assert load_fixture_build(json.dumps(record), roots={"run": tmp_path}) == build


def test_the_build_hash_covers_the_runtime_archive_the_model_and_the_lock(tmp_path):
    from helia_profiler.fixture import FixtureRuntimeSource

    build = _aot(tmp_path)
    base = fixture_run_identity(build).build_hash
    archive = FixtureRuntimeSource(
        "helia-rt", "https://github.com/AmbiqAI/helia-rt", "2.2.2", "a" * 40, "c" * 64
    )
    for changed in (
        replace(build, runtime_source=archive),
        replace(build, model=_pin(tmp_path / "other" / "model.tflite", b"other model")),
        replace(
            build, dependency_lock=_pin(tmp_path / "other" / "nsx.lock", _lock(commit="8" * 40))
        ),
    ):
        assert fixture_run_identity(changed).build_hash != base


def test_each_missing_fact_states_its_own_reason(tmp_path):
    from helia_profiler.fixture import FixtureRuntimeSource

    build = _aot(tmp_path)
    no_modules = _pin(tmp_path / "none.json", json.dumps({"modules": []}).encode())
    identity = fixture_run_identity(
        replace(
            build,
            model=None,
            inputs=(),
            cpu_hz=None,
            toolchain=None,
            kernel_source=None,
            dependency_state=no_modules,
        )
    )
    assert identity.model_sha256 == Unknown("the record predates pinned models")
    assert identity.inputs_sha256 == Unknown("the record predates pinned inputs")
    assert identity.build.cpu_hz == Unknown("the record predates this fact")
    assert identity.build.toolchain == Unknown("the record states no toolchain")
    assert identity.kernels.library == Unknown(
        "the dependency state names 0 CMSIS-NN modules, not one"
    )
    unmatched = FixtureRuntimeSource(
        "helia-rt", "https://github.com/AmbiqAI/helia-rt", None, "a" * 40, "c" * 64
    )
    version = fixture_run_identity(replace(build, runtime_source=unmatched)).runtime.version
    assert version == Unknown(f"no runtime record matches {'a' * 40}")


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"not json", "the dependency state is not JSON"),
        (b"\x80abc", "the dependency state is not JSON"),
        (b"[]", "the dependency state records no workspace"),
        (b'{"workspace": "x"}', "the dependency state records no workspace"),
    ],
)
def test_a_malformed_dependency_state_is_an_unknown_not_a_crash(tmp_path, content, reason):
    build = replace(_aot(tmp_path), dependency_state=_pin(tmp_path / "bad.json", content))
    assert fixture_run_identity(build).baseline_id == Unknown(reason)


def test_a_workspace_without_inputs_has_no_producer(tmp_path):
    state = _pin(
        tmp_path / "s.json", json.dumps({"workspace": {"baseline_id": "b", "inputs": "x"}}).encode()
    )
    identity = fixture_run_identity(replace(_aot(tmp_path), dependency_state=state))
    assert identity.baseline_id == "b"
    assert identity.producer == Unknown("the workspace records no inputs")


def test_lifecycle_and_input_names_are_part_of_the_identity(tmp_path):
    build = _aot(tmp_path)
    assert fixture_run_identity(replace(build, state_policy=None)).run.lifecycle == "stateless"
    renamed = tuple(
        replace(io, tensor=replace(io.tensor, name=io.tensor.name + "_x")) for io in build.inputs
    )
    assert (
        fixture_run_identity(replace(build, inputs=renamed)).inputs_sha256
        != fixture_run_identity(build).inputs_sha256
    )


def test_a_ram_observing_record_from_the_previous_api_still_loads(tmp_path):
    build = _reseal(_aot(tmp_path), observe_ram=True)
    record = json.loads(dump_fixture_build(build, roots={"run": tmp_path}))
    record["fixture_api"] = [5, 6]
    assert load_fixture_build(json.dumps(record), roots={"run": tmp_path}) == build


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda d: d["providers"]["helia-rt"].update(url="https://example.com/fork"),
            "provider source",
        ),
        (lambda d: d.update(stack="mystery"), "stack"),
        (lambda d: d.update(archive_sha256="not-a-digest"), "SHA256"),
    ],
)
def test_the_manifest_reader_checks_declared_fields(tmp_path, change, message):
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.path.read_bytes())
    change(data)
    with pytest.raises(ValueError, match=message):
        read_prepared_runtime_manifest(_pin(tmp_path / "changed.json", json.dumps(data).encode()))


def test_capture_evidence_files_are_not_part_of_the_observation(tmp_path):
    build = _aot(tmp_path)
    capture = _capture(tmp_path / "x")
    noted = replace(
        capture, artifacts=(_pin(tmp_path / "x" / "completion.json", b'{"elapsed_s": 1}'),)
    )
    assert (
        fixture_run_identity(build, capture).observation_hash
        == fixture_run_identity(build, noted).observation_hash
    )


@pytest.mark.parametrize("baseline", [None, ""])
def test_an_absent_or_empty_workspace_fact_is_unknown(tmp_path, baseline):
    workspace = {"inputs": {"hpx_version": "0.2.3"}}
    if baseline is not None:
        workspace["baseline_id"] = baseline
    state = _pin(tmp_path / "s.json", json.dumps({"workspace": workspace}).encode())
    identity = fixture_run_identity(replace(_aot(tmp_path), dependency_state=state))
    assert identity.baseline_id == Unknown("the workspace records no baseline_id")


def test_a_lock_that_is_not_an_nsx_lock_has_no_build_hash(tmp_path):
    build = replace(_aot(tmp_path), dependency_lock=_pin(tmp_path / "x.lock", b"- not a lock"))
    assert fixture_run_identity(build).build_hash == Unknown(
        "the dependency lock is not an NSX lock"
    )


def test_records_that_predate_sources_rederive_them_from_pinned_files(tmp_path):
    build = _aot(tmp_path)
    older = replace(build, kernel_source=None, runtime_source=None)
    assert fixture_run_identity(older).kernels == fixture_run_identity(build).kernels

    manifest = _manifest(tmp_path / "rt")
    heliart = replace(build, runtime_manifest=manifest, kernel_source=None, engine_source=None)
    identity = fixture_run_identity(heliart)
    assert identity.runtime.commit == "a" * 40 and identity.kernels.commit == "b" * 40
    assert identity.prepared_archive_sha256 == "c" * 64


def test_the_ram_observation_is_part_of_the_observation_hash(tmp_path):
    build = _aot(tmp_path)
    capture = _capture(tmp_path / "x")

    def with_ram(stack):
        ram = _pin(tmp_path / f"ram{stack}" / "ram.json", json.dumps({"stack": stack}).encode())
        return fixture_run_identity(build, replace(capture, artifacts=(ram,))).observation_hash

    assert with_ram(2048) != with_ram(61440)


def test_an_error_message_is_not_part_of_the_observation(tmp_path):
    build = _aot(tmp_path)
    capture = replace(_capture(tmp_path / "x"), state="failure", error="flash failed at /tmp/a")
    other = replace(capture, error="flash failed at /home/b")
    assert (
        fixture_run_identity(build, capture).observation_hash
        == fixture_run_identity(build, other).observation_hash
    )


def test_a_git_module_counts_by_commit_and_packaged_tooling_by_content(tmp_path):
    base = fixture_run_identity(_aot(tmp_path / "a")).build_hash
    crlf = _lock().replace(b"sha256:" + b"2" * 64, b"sha256:" + b"4" * 64)
    assert fixture_run_identity(_aot(tmp_path / "b", crlf)).build_hash == base
    tooling = _lock().replace(b"sha256:" + b"3" * 64, b"sha256:" + b"5" * 64)
    assert fixture_run_identity(_aot(tmp_path / "c", tooling)).build_hash != base


def test_generated_vendored_modules_do_not_change_the_build_hash(tmp_path):
    vendored = b"""      hpx_model:
        project: hpx_model
        kind: vendored
        constraint: vendored
        resolved:
          vendored_at: modules/hpx_model
          content_hash: sha256:{digest}
"""

    def lock(digest):
        return _lock() + vendored.replace(b"{digest}", digest.encode())

    first = fixture_run_identity(_aot(tmp_path / "a", lock("6" * 64))).build_hash
    second = fixture_run_identity(_aot(tmp_path / "b", lock("7" * 64))).build_hash
    assert first == second


@pytest.mark.parametrize(
    "engine,declared,expected",
    [
        (EngineType.LITERT, True, True),
        (EngineType.LITERT, False, False),
        (EngineType.HELIA_RT, True, True),
        (EngineType.HELIA_AOT, True, False),
    ],
)
def test_the_first_invocation_states_whether_it_runs_the_initializer(
    tmp_path, engine, declared, expected
):
    build = replace(_build(tmp_path), engine=engine, source_initializer_present=declared)
    run = fixture_run_identity(build).run

    assert run.first_invoke_initializer is expected
    assert run.instance_boundary == "boot"
    assert (run.warmups, run.first_invoke_window) == (5, "warmup")
    unwarmed = fixture_run_identity(replace(build, warmups=0)).run
    assert unwarmed.first_invoke_window == "measured"


def test_the_window_is_one_stimer_total_over_the_timed_invocations(tmp_path):
    identity = fixture_run_identity(_build(tmp_path))

    run = identity.run
    assert (run.timing_scope, run.timing_clock, run.timing_clock_hz) == (
        "invoke_only",
        "stimer",
        32768,
    )
    assert run.timing_statistic == "total"
    assert identity.intent_hash is None
    assert fixture_run_identity(replace(_build(tmp_path), timer_hz=None)).run.timing_clock_hz == (
        Unknown("the record predates this fact")
    )
