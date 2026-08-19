from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from threading import Thread

import pytest
from neuralspotx.nsx_lock import (
    LockKind,
    NsxLock,
    ResolvedModule,
    hash_manifest,
    write_lock,
)

from helia_profiler.config import load_config
from helia_profiler.dependencies import (
    create_workspace,
    normalize_path,
    prepare_locked_dependencies,
    read_dependency_lock_provenance,
)
from helia_profiler.engines.base import EngineArtifacts
from helia_profiler.errors import DependencyError, LockError, VersionError
from helia_profiler.errors import BuildError
from helia_profiler.pipeline import PipelineContext
from helia_profiler.results import DependencyLockMode
from helia_profiler.compatibility import QualificationState
from helia_profiler.stages.resolve_platform import ResolvePlatformStage


def _context(
    tmp_path: Path,
    *,
    build: dict | None = None,
    backend: str | None = None,
    engine_type: str = "tflm",
    engine_config: dict | None = None,
    model_name: str = "model.tflite",
    model_bytes: bytes = b"TFL3",
) -> PipelineContext:
    tmp_path.mkdir(parents=True, exist_ok=True)
    model = tmp_path / model_name
    model.write_bytes(model_bytes)
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {
                "type": engine_type,
                "backend": backend,
                "config": engine_config or {},
            },
            "target": {"board": "apollo510_evb"},
            "build": build or {},
            "work_dir": str(tmp_path / "work"),
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path / "work")
    ResolvePlatformStage().run(ctx)
    ctx.engine_artifacts = EngineArtifacts()
    ctx.dependency_workspace = create_workspace(ctx)
    ctx.firmware_dir = ctx.dependency_workspace.root / "profiler_app"
    ctx.firmware_dir.mkdir(parents=True, exist_ok=True)
    (ctx.firmware_dir / "nsx.yml").write_text(
        "target:\n  board: apollo510_evb\nmodules:\n  - name: demo\n",
        encoding="utf-8",
    )
    return ctx


def _write_valid_lock(
    ctx: PipelineContext,
    *,
    commit: str = "a" * 40,
    project: str = "demo-project",
) -> bytes:
    assert ctx.firmware_dir is not None
    module_dir = ctx.firmware_dir / "modules" / "demo"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "nsx-module.yaml").write_text("name: demo\n", encoding="utf-8")
    lock = NsxLock(
        generated_at="2026-08-03T00:00:00+00:00",
        nsx_tool_version="0.7.17",
        manifest_hash=hash_manifest(ctx.firmware_dir / "nsx.yml"),
        target={"board": "apollo510_evb"},
        modules={
            "demo": ResolvedModule(
                project=project,
                kind=LockKind.GIT,
                constraint="v1.2.3",
                tag="v1.2.3",
                commit=commit,
                url="https://example.invalid/demo.git",
                vendored_at="modules/demo",
                content_hash="sha256:" + "b" * 64,
                acquired_at="2026-08-03T00:00:00+00:00",
            )
        },
    )
    write_lock(ctx.firmware_dir, lock, "apollo510_evb")
    return (ctx.firmware_dir / "nsx.lock").read_bytes()


def test_first_resolution_locks_without_update_then_syncs_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _context(tmp_path)
    lock_calls: list[bool] = []
    sync_calls: list[bool] = []

    def lock(app_dir: Path, *, update: bool, **_kwargs) -> None:
        lock_calls.append(update)
        _write_valid_lock(ctx)

    monkeypatch.setattr("helia_profiler.dependencies.nsx_cli.lock", lock)
    monkeypatch.setattr(
        "helia_profiler.dependencies.nsx_cli.sync",
        lambda _path, *, frozen, **_kwargs: sync_calls.append(frozen),
    )

    provenance = prepare_locked_dependencies(ctx)

    assert lock_calls == [False]
    assert sync_calls == [True]
    assert provenance.lock.mode is DependencyLockMode.RESOLVED
    assert provenance.modules[0].requested_tag == "v1.2.3"
    assert provenance.modules[0].peeled_commit == "a" * 40


def test_ordinary_reuse_is_byte_stable_and_never_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _context(tmp_path)
    before = _write_valid_lock(ctx)
    monkeypatch.setattr(
        "helia_profiler.dependencies.nsx_cli.lock",
        lambda *_args, **_kwargs: pytest.fail("ordinary reuse must not call nsx lock"),
    )
    monkeypatch.setattr("helia_profiler.dependencies.nsx_cli.sync", lambda *_a, **_kw: None)

    provenance = prepare_locked_dependencies(ctx)

    assert (ctx.firmware_dir / "nsx.lock").read_bytes() == before
    assert provenance.lock.mode is DependencyLockMode.REUSED
    assert provenance.lock.frozen_sync is True


def test_online_reuse_repairs_partial_materialization_without_relocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _context(tmp_path)
    before = _write_valid_lock(ctx)
    calls: list[tuple[bool, bool]] = []

    def sync(_path: Path, *, frozen: bool, force: bool = False, **_kwargs) -> None:
        calls.append((frozen, force))
        if len(calls) == 1:
            raise BuildError("content mismatch", details="partial module")

    monkeypatch.setattr(
        "helia_profiler.dependencies.nsx_cli.lock",
        lambda *_args, **_kwargs: pytest.fail("repair must not resolve refs"),
    )
    monkeypatch.setattr("helia_profiler.dependencies.nsx_cli.sync", sync)

    provenance = prepare_locked_dependencies(ctx)

    assert calls == [(True, False), (False, True), (True, False)]
    assert (ctx.firmware_dir / "nsx.lock").read_bytes() == before
    assert provenance.lock.mode is DependencyLockMode.REUSED


def test_engine_release_source_mapping_is_fingerprinted_and_serialized(
    tmp_path: Path,
) -> None:
    model = tmp_path / "model.tflite"
    model.write_bytes(b"TFL3")
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {
                "type": "helia-rt",
                "config": {
                    "source": {
                        "repo": "AmbiqAI/helia-rt",
                        "ref": "helia-rt-v1.16.0",
                    }
                },
            },
            "work_dir": str(tmp_path / "work"),
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path / "work")
    ResolvePlatformStage().run(ctx)
    ctx.engine_artifacts = EngineArtifacts()

    workspace = create_workspace(ctx)

    assert workspace.fingerprint
    from helia_profiler.dependencies import _override_inputs

    _, overrides = _override_inputs(ctx)
    assert overrides[-1].mode == "release"
    assert overrides[-1].requested == "AmbiqAI/helia-rt@helia-rt-v1.16.0"


def test_explicit_update_is_the_only_refresh_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _context(tmp_path, build={"update_dependencies": True})
    _write_valid_lock(ctx)
    updates: list[bool] = []

    def lock(_app_dir: Path, *, update: bool, **_kwargs) -> None:
        updates.append(update)
        _write_valid_lock(ctx, commit="c" * 40)

    monkeypatch.setattr("helia_profiler.dependencies.nsx_cli.lock", lock)
    monkeypatch.setattr("helia_profiler.dependencies.nsx_cli.sync", lambda *_a, **_kw: None)

    provenance = prepare_locked_dependencies(ctx)

    assert updates == [True]
    assert provenance.lock.mode is DependencyLockMode.UPDATED
    assert provenance.lock.update_requested is True
    assert provenance.modules[0].peeled_commit == "c" * 40


def test_exact_dependency_provenance_serialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _context(
        tmp_path,
        build={"nsx_modules": {"demo": {"ref": "feature/test"}}},
    )
    exact_lock = _write_valid_lock(ctx)
    monkeypatch.setattr("helia_profiler.dependencies.nsx_cli.sync", lambda *_a, **_kw: None)

    provenance = prepare_locked_dependencies(ctx)
    assert ctx.dependency_lock_path is not None
    snapshot_bytes = ctx.dependency_lock_path.read_bytes()
    (ctx.firmware_dir / "nsx.lock").write_text("concurrent update\n")
    serialized = json.loads(json.dumps(provenance.to_dict(), sort_keys=True))

    assert ctx.dependency_lock_path.read_bytes() == snapshot_bytes == exact_lock
    assert serialized["workspace"]["registry_hash"]["algorithm"] == "sha256"
    assert serialized["workspace"]["baseline_id"] == "hpx-neuralspotx-0.7.17-2026-08"
    assert (
        serialized["workspace"]["baseline_fingerprint"]
        == "6de26ef7b2bebefefe99f7dd651fc80f41ad27e39713714be0e5d274e545e7c0"
    )
    assert serialized["lock"]["mode"] == "reused"
    assert serialized["qualification"] == "development-overrides"
    assert serialized["lock"]["sha256"]["value"] == hashlib.sha256(exact_lock).hexdigest()
    assert serialized["modules"] == [
        {
            "content_hash": {"algorithm": "sha256", "value": "b" * 64},
            "kind": "git",
            "name": "demo",
            "peeled_commit": "a" * 40,
            "project": "demo-project",
            "requested_ref": "v1.2.3",
            "requested_tag": "v1.2.3",
            "url": "https://example.invalid/demo.git",
            "vendored_at": "modules/demo",
        }
    ]
    assert any(
        override["scope"] == "project"
        and override["name"] == "demo-project"
        and override["requested"] == "feature/test"
        for override in serialized["overrides"]
    )


def test_read_only_lock_provenance_surface_from_app_or_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _context(tmp_path)
    exact_lock = _write_valid_lock(ctx)
    monkeypatch.setattr("helia_profiler.dependencies.nsx_cli.sync", lambda *_a, **_kw: None)
    prepare_locked_dependencies(ctx)
    assert ctx.firmware_dir is not None
    assert ctx.dependency_workspace is not None
    state_path = ctx.firmware_dir / "hpx-dependencies.json"
    lock_path = ctx.firmware_dir / "nsx.lock"
    before = {
        state_path: (state_path.read_bytes(), state_path.stat().st_mtime_ns),
        lock_path: (lock_path.read_bytes(), lock_path.stat().st_mtime_ns),
    }

    surfaces = tuple(
        read_dependency_lock_provenance(path)
        for path in (
            ctx.firmware_dir,
            ctx.dependency_workspace.root,
            state_path,
            lock_path,
        )
    )

    assert all(surface == surfaces[0] for surface in surfaces)
    surface = surfaces[0]
    assert surface.lock_path == lock_path.resolve()
    assert surface.lock_sha256 == hashlib.sha256(exact_lock).hexdigest()
    assert len(surface.registry_hash) == 64
    assert surface.qualification is QualificationState.QUALIFIED
    assert surface.baseline_fingerprint == ctx.config.compatibility_baseline.fingerprint
    assert surface.workspace_fingerprint == ctx.dependency_workspace.fingerprint
    assert surface.lock_mode is DependencyLockMode.REUSED
    assert surface.update_requested is False
    assert surface.overrides == ()
    assert [(item.scope, item.name, item.requested_ref, item.requested_tag) for item in surface.requested_refs] == [
        ("module", "demo", "v1.2.3", "v1.2.3"),
        ("project", "demo-project", "v1.2.3", "v1.2.3"),
    ]
    assert surface.resolved[0].peeled_commit == "a" * 40
    assert surface.resolved[0].content_hash.value == "b" * 64
    assert {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before
    } == before


def test_lock_provenance_provider_rejects_lock_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _context(tmp_path)
    _write_valid_lock(ctx)
    monkeypatch.setattr("helia_profiler.dependencies.nsx_cli.sync", lambda *_a, **_kw: None)
    prepare_locked_dependencies(ctx)
    assert ctx.firmware_dir is not None
    (ctx.firmware_dir / "nsx.lock").write_bytes(b"changed")

    with pytest.raises(DependencyError, match="no longer matches"):
        read_dependency_lock_provenance(ctx.firmware_dir)


def test_incompatible_inputs_select_isolated_workspaces(tmp_path: Path) -> None:
    first = _context(tmp_path / "first")
    second = _context(tmp_path / "second", backend="cmsis-nn")

    assert first.dependency_workspace is not None
    assert second.dependency_workspace is not None
    assert first.dependency_workspace.fingerprint != second.dependency_workspace.fingerprint
    assert first.dependency_workspace.root != second.dependency_workspace.root


def test_model_and_firmware_config_changes_isolate_workspaces(tmp_path: Path) -> None:
    first = _context(tmp_path / "one")
    assert first.dependency_workspace is not None
    second = _context(tmp_path / "two", model_bytes=b"different-model")
    assert second.dependency_workspace is not None

    model = tmp_path / "three" / "model.tflite"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"TFL3")
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "tflm"},
            "profiling": {"iterations": 7},
            "work_dir": str(tmp_path / "three" / "work"),
        },
    )
    third = PipelineContext(config=config, work_dir=tmp_path / "three" / "work")
    ResolvePlatformStage().run(third)
    third.engine_artifacts = EngineArtifacts()
    third.dependency_workspace = create_workspace(third)

    assert first.dependency_workspace.fingerprint != second.dependency_workspace.fingerprint
    assert first.dependency_workspace.fingerprint != third.dependency_workspace.fingerprint


def test_offline_requires_compatible_lock(tmp_path: Path) -> None:
    ctx = _context(tmp_path, build={"offline": True})

    with pytest.raises(DependencyError, match="requires a compatible lock") as exc:
        prepare_locked_dependencies(ctx)

    assert exc.value.hint is not None
    assert "--update-dependencies" in exc.value.hint
    # Missing lock is a LockError, not a bare/other DependencyError.
    assert isinstance(exc.value, LockError)


def test_missing_override_path_fails_with_typed_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing-module"

    with pytest.raises(DependencyError, match="does not exist"):
        _context(
            tmp_path / "run",
            build={"nsx_modules": {"nsx-core": {"path": str(missing)}}},
        )


def test_offline_requires_materialized_locked_modules(tmp_path: Path) -> None:
    ctx = _context(tmp_path, build={"offline": True})
    _write_valid_lock(ctx)
    assert ctx.firmware_dir is not None
    module_dir = ctx.firmware_dir / "modules" / "demo"
    for child in module_dir.iterdir():
        child.unlink()
    module_dir.rmdir()

    with pytest.raises(DependencyError, match="module trees are missing") as exc:
        prepare_locked_dependencies(ctx)
    assert isinstance(exc.value, LockError)


def test_override_content_and_request_change_fingerprint(
    tmp_path: Path,
) -> None:
    override = tmp_path / "module"
    override.mkdir(parents=True)
    (override / "nsx-module.yaml").write_text("name: demo\n")
    first = _context(
        tmp_path / "one",
        build={"nsx_modules": {"nsx-core": {"path": str(override)}}},
    )
    (override / "source.c").write_text("int changed;\n")
    second = _context(
        tmp_path / "two",
        build={"nsx_modules": {"nsx-core": {"path": str(override)}}},
    )

    assert first.dependency_workspace is not None
    assert second.dependency_workspace is not None
    assert first.dependency_workspace.fingerprint != second.dependency_workspace.fingerprint


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (r"C:\Users\Ada\module", "c:/Users/Ada/module"),
        ("C:/Users/Ada/module/", "c:/Users/Ada/module"),
        ("relative\\module", "relative/module"),
        (r"\\server\share\module", "//server/share/module"),
    ],
)
def test_cross_platform_path_normalization(value: str, expected: str) -> None:
    assert normalize_path(value) == expected


def test_concurrent_workspace_identity_is_atomic(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    assert ctx.dependency_workspace is not None
    identity_path = ctx.dependency_workspace.root / "hpx-workspace.json"
    identity_path.unlink()
    failures: list[Exception] = []

    def create() -> None:
        try:
            create_workspace(ctx)
        except Exception as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    threads = [Thread(target=create) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    assert json.loads(identity_path.read_text())["fingerprint"] == (
        ctx.dependency_workspace.fingerprint
    )


# ---------------------------------------------------------------------------
# Baseline resolution integrity — the lock must agree with the manifest's
# qualified-ref claims (found 2026-08-12: NSX honoured its packaged registry's
# module revision over the app's project pin, so eight hardware runs built
# nsx-sensors v0.1.0 while every artifact claimed the baseline commit).
# ---------------------------------------------------------------------------


def _baseline_ref(ctx: PipelineContext, project: str) -> str:
    compatibility = ctx.config.compatibility
    assert compatibility is not None
    return compatibility.baseline.project(project).ref


def test_lock_resolving_off_baseline_ref_raises_version_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _context(tmp_path)
    _write_valid_lock(ctx, project="nsx-sensors", commit="d" * 40)
    monkeypatch.setattr("helia_profiler.dependencies.nsx_cli.sync", lambda *_a, **_kw: None)

    with pytest.raises(VersionError, match="qualified baseline pins") as exc:
        prepare_locked_dependencies(ctx)

    message = str(exc.value)
    assert "d" * 40 in message
    assert _baseline_ref(ctx, "nsx-sensors") in message
    # The corrupt resolution must not be persisted as valid run state.
    assert ctx.firmware_dir is not None
    assert not (ctx.firmware_dir / "hpx-dependencies.json").exists()


def test_lock_matching_baseline_ref_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _context(tmp_path)
    _write_valid_lock(ctx, project="nsx-sensors", commit=_baseline_ref(ctx, "nsx-sensors"))
    monkeypatch.setattr("helia_profiler.dependencies.nsx_cli.sync", lambda *_a, **_kw: None)

    provenance = prepare_locked_dependencies(ctx)

    assert provenance.modules[0].peeled_commit == _baseline_ref(ctx, "nsx-sensors")


def test_explicit_module_override_exempts_project_from_baseline_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Intentional divergence goes through overrides, which reclassify
    qualification — it must not trip the silent-drift guard."""
    ctx = _context(
        tmp_path,
        build={"nsx_modules": {"demo": {"ref": "feature/experiment"}}},
    )
    _write_valid_lock(ctx, project="nsx-sensors", commit="d" * 40)
    monkeypatch.setattr("helia_profiler.dependencies.nsx_cli.sync", lambda *_a, **_kw: None)

    provenance = prepare_locked_dependencies(ctx)

    assert provenance.modules[0].peeled_commit == "d" * 40


@pytest.mark.parametrize(
    ("engine_type", "backend", "model_name", "project"),
    [
        ("helia-rt", None, "model.tflite", "ns-cmsis-nn"),
        ("executorch", "ns", "model.pte", "ns-cmsis-nn"),
        ("executorch", "arm", "model.pte", "arm-cmsis-nn"),
    ],
)
def test_engine_cmsis_nn_override_exempts_provider_project_from_baseline_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine_type: str,
    backend: str | None,
    model_name: str,
    project: str,
) -> None:
    ctx = _context(
        tmp_path,
        engine_type=engine_type,
        backend=backend,
        engine_config={"cmsis_nn_ref": "feature/provider-test"},
        model_name=model_name,
    )
    _write_valid_lock(ctx, project=project, commit="d" * 40)
    monkeypatch.setattr("helia_profiler.dependencies.nsx_cli.sync", lambda *_a, **_kw: None)

    provenance = prepare_locked_dependencies(ctx)

    assert provenance.modules[0].peeled_commit == "d" * 40
    assert any(
        override.scope == "engine" and override.name == "cmsis_nn_ref"
        for override in provenance.overrides
    )


def test_unpinned_projects_are_not_baseline_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _context(tmp_path)
    _write_valid_lock(ctx, project="demo-project", commit="d" * 40)
    monkeypatch.setattr("helia_profiler.dependencies.nsx_cli.sync", lambda *_a, **_kw: None)

    provenance = prepare_locked_dependencies(ctx)  # must not raise

    assert provenance.modules[0].project == "demo-project"


# ---------------------------------------------------------------------------
# DependencyError taxonomy — VersionError vs LockError classification.
# ---------------------------------------------------------------------------


def test_lock_schema_version_mismatch_raises_version_error(tmp_path: Path) -> None:
    from helia_profiler.dependencies import _lock_incompatibility

    ctx = _context(tmp_path)
    assert ctx.firmware_dir is not None
    _write_valid_lock(ctx)
    lock_path = ctx.firmware_dir / "nsx.lock"
    text = lock_path.read_text(encoding="utf-8")
    assert "schema_version: 4" in text
    lock_path.write_text(
        text.replace("schema_version: 4", "schema_version: 999"), encoding="utf-8"
    )

    reason = _lock_incompatibility(ctx.firmware_dir, "apollo510_evb")

    assert reason is not None
    message, error_cls = reason
    assert "schema_version 999" in message
    assert error_cls is VersionError
    assert issubclass(error_cls, DependencyError)
    assert not issubclass(error_cls, LockError)


def test_offline_lock_schema_drift_raises_version_error_end_to_end(tmp_path: Path) -> None:
    """The full offline-reuse path surfaces the same VersionError, not a bare LockError."""
    ctx = _context(tmp_path, build={"offline": True})
    assert ctx.firmware_dir is not None
    _write_valid_lock(ctx)
    lock_path = ctx.firmware_dir / "nsx.lock"
    text = lock_path.read_text(encoding="utf-8")
    lock_path.write_text(
        text.replace("schema_version: 4", "schema_version: 999"), encoding="utf-8"
    )

    with pytest.raises(VersionError, match="schema_version 999") as exc:
        prepare_locked_dependencies(ctx)

    assert not isinstance(exc.value, LockError)


def test_read_dependency_lock_provenance_missing_state_raises_lock_error(
    tmp_path: Path,
) -> None:
    empty_app_dir = tmp_path / "profiler_app"
    empty_app_dir.mkdir(parents=True)

    with pytest.raises(LockError, match="No prepared HPX dependency app found"):
        read_dependency_lock_provenance(tmp_path)


def test_lock_provenance_drift_raises_lock_error_not_version_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _context(tmp_path)
    _write_valid_lock(ctx)
    monkeypatch.setattr("helia_profiler.dependencies.nsx_cli.sync", lambda *_a, **_kw: None)
    prepare_locked_dependencies(ctx)
    assert ctx.firmware_dir is not None
    (ctx.firmware_dir / "nsx.lock").write_bytes(b"changed")

    with pytest.raises(LockError, match="no longer matches") as exc:
        read_dependency_lock_provenance(ctx.firmware_dir)

    assert not isinstance(exc.value, VersionError)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
def test_read_dependency_lock_provenance_unreadable_lock_raises_lock_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.geteuid() == 0:
        pytest.skip("root ignores POSIX permission bits")
    ctx = _context(tmp_path)
    _write_valid_lock(ctx)
    monkeypatch.setattr("helia_profiler.dependencies.nsx_cli.sync", lambda *_a, **_kw: None)
    prepare_locked_dependencies(ctx)
    assert ctx.firmware_dir is not None
    lock_path = ctx.firmware_dir / "nsx.lock"
    original_mode = lock_path.stat().st_mode
    lock_path.chmod(0o000)
    try:
        with pytest.raises(LockError):
            read_dependency_lock_provenance(ctx.firmware_dir)
    finally:
        lock_path.chmod(original_mode)
