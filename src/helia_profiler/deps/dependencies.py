"""Deterministic NSX dependency workspaces and lock reuse."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import asdict, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from neuralspotx.file_lock import file_mutex
from neuralspotx.nsx_lock import LOCK_SCHEMA_VERSION, hash_manifest, read_lock

from . import nsx as nsx_cli
from .._version import __version__
from ..errors import BuildError, DependencyError, LockError, VersionError
from ..results.dependencies import (
    ContentDigest,
    DependencyLockMode,
    DependencyLockProvenance,
    DependencyLockState,
    DependencyModule,
    DependencyOverride,
    DependencyProvenance,
    DependencyRequest,
    DependencyWorkspace,
)
from .compatibility import QualificationState
from .sync import (
    _offline_materialization_error,
    _run_frozen_sync_with_repair,
    _sync_stamp_matches,
    _write_sync_stamp,
    invalidate_sync_stamp,
)

if TYPE_CHECKING:
    from ..pipeline import PipelineContext

log = logging.getLogger("hpx")

WORKSPACE_SCHEMA_VERSION = 1
_WORKSPACE_IDENTITY = "hpx-workspace.json"
_DEPENDENCY_STATE = "hpx-dependencies.json"
_HASH_EXCLUDED_PARTS = frozenset(
    {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".DS_Store"}
)


def normalize_path(value: str | Path) -> str:
    """Return a slash-stable path spelling for fingerprints and provenance."""

    text = str(value).replace("\\", "/")
    unc = text.startswith("//")
    while "//" in text:
        text = text.replace("//", "/")
    if unc:
        text = "/" + text
    if len(text) >= 2 and text[1] == ":":
        text = text[0].lower() + text[1:]
    return text.rstrip("/") or "."


def _digest_bytes(payload: bytes) -> ContentDigest:
    return ContentDigest("sha256", hashlib.sha256(payload).hexdigest())


def _digest_file(path: Path) -> ContentDigest:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return ContentDigest("sha256", digest.hexdigest())


def _digest_path(path: Path) -> ContentDigest:
    """Hash path content without filesystem metadata or native separators."""

    if not path.exists():
        raise DependencyError(
            f"Dependency fingerprint input does not exist: {path}",
            hint="Correct or remove the model/module/engine source path override.",
        )
    if path.is_file():
        return _digest_file(path)
    digest = hashlib.sha256()
    if path.is_dir():
        children = sorted(
            (
                child
                for child in path.rglob("*")
                if child.is_file()
                and not any(part in _HASH_EXCLUDED_PARTS for part in child.relative_to(path).parts)
            ),
            key=lambda child: child.relative_to(path).as_posix(),
        )
        for child in children:
            relative = child.relative_to(path).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(_digest_file(child).value.encode("ascii"))
            digest.update(b"\n")
        return ContentDigest("sha256", digest.hexdigest())
    raise DependencyError(
        f"Dependency fingerprint input is not a regular file or directory: {path}",
        hint="Use a regular file or directory for dependency source overrides.",
    )


def _canonical(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: _canonical(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_canonical(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, default=str))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return normalize_path(value)
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _registry_digest() -> ContentDigest:
    return _digest_bytes(_canonical_bytes(nsx_cli.load_registry()))


def _override_inputs(ctx: PipelineContext) -> tuple[dict[str, Any], tuple[DependencyOverride, ...]]:
    inputs: dict[str, Any] = {}
    provenance: list[DependencyOverride] = []
    for name, override in sorted(ctx.config.build.nsx_modules.items()):
        if override.path is not None:
            digest = _digest_path(override.path.expanduser())
            requested = normalize_path(override.path)
            inputs[name] = {
                "mode": "path",
                "requested": requested,
                "content_hash": digest.to_dict(),
            }
            provenance.append(
                DependencyOverride("module", name, "path", requested, digest)
            )
        elif override.ref is not None:
            inputs[name] = {"mode": "ref", "requested": override.ref}
            provenance.append(DependencyOverride("module", name, "ref", override.ref))
        else:
            assert override.version is not None
            inputs[name] = {"mode": "version", "requested": override.version}
            provenance.append(
                DependencyOverride("module", name, "version", override.version)
            )

    engine_config = ctx.config.engine.config
    for key in ("dist_path", "source_path", "cmsis_nn_path"):
        raw = engine_config.get(key)
        if raw is None:
            continue
        if not isinstance(raw, (str, Path)):
            raise DependencyError(
                f"engine.config.{key} must be a filesystem path for dependency provenance."
            )
        requested = normalize_path(raw)
        digest = _digest_path(Path(raw).expanduser())
        provenance.append(DependencyOverride("engine", key, "path", requested, digest))
    cmsis_nn_ref = engine_config.get("cmsis_nn_ref")
    if cmsis_nn_ref is not None:
        if not isinstance(cmsis_nn_ref, str) or not cmsis_nn_ref.strip():
            raise DependencyError(
                "engine.config.cmsis_nn_ref must be a non-empty git ref."
            )
        provenance.append(
            DependencyOverride("engine", "cmsis_nn_ref", "ref", cmsis_nn_ref)
        )
    source = engine_config.get("source")
    if source is not None:
        if not isinstance(source, dict):
            raise DependencyError(
                "engine.config.source must contain a repository/ref mapping."
            )
        from ..engines.helia_rt.artifacts import HELIART_GH_REPO, HELIART_RELEASE_TAG

        repository = source.get("repo", HELIART_GH_REPO)
        reference = source.get("ref", HELIART_RELEASE_TAG)
        if not isinstance(repository, str) or not isinstance(reference, str):
            raise DependencyError(
                "engine.config.source requires non-empty 'repo' and 'ref' strings."
            )
        provenance.append(
            DependencyOverride(
                "engine",
                "source",
                "release",
                f"{repository}@{reference}",
            )
        )
    for variable in ("HELIART_DIST_PATH", "HELIART_SOURCE_PATH", "CMSIS_NN_PATH"):
        raw = os.environ.get(variable)
        if raw:
            path = Path(raw).expanduser()
            provenance.append(
                DependencyOverride(
                    "engine",
                    f"env.{variable}",
                    "path",
                    normalize_path(raw),
                    _digest_path(path),
                )
            )
    if ctx.config.engine.config_path is not None:
        path = ctx.config.engine.config_path.expanduser()
        digest = _digest_path(path)
        provenance.append(
            DependencyOverride(
                "engine", "config_path", "path", normalize_path(path), digest
            )
        )
    return inputs, tuple(provenance)


def create_workspace(ctx: PipelineContext) -> DependencyWorkspace:
    """Compute and persist the deterministic workspace selected for *ctx*."""

    board = ctx.resolved_board
    artifacts = ctx.prepared_artifacts
    compatibility = ctx.config.compatibility
    if compatibility is None:
        raise DependencyError("Compatibility baseline is unavailable for dependency locking.")

    registry_hash = _registry_digest()
    module_overrides, explicit_overrides = _override_inputs(ctx)
    extra_modules = []
    for module in sorted(artifacts.extra_modules, key=lambda item: item.name):
        entry: dict[str, Any] = {
            "name": module.name,
            "project": module.project,
            "local": module.local,
            "ref": module.ref,
            "version": module.version,
        }
        if module.local:
            entry["content_hash"] = _digest_path(module.path).to_dict()
        extra_modules.append(entry)

    # The fingerprint captures DEPENDENCY identity only: everything that
    # decides what lands in modules/ and how CMake is configured.  Render-only
    # inputs (the model bytes, arena size/placement, iterations/warmup, PMU
    # counters, heartbeat, RTT buffer sizing, power wiring) are deliberately
    # excluded so cases that differ only in what the generate stage renders
    # share one synced, configured, compiled workspace instead of forking a
    # full module tree per model.  This is a cache key, not the correctness
    # boundary — three layers gate reuse regardless of what is hashed here:
    # the rendered nsx.yml manifest hash decides lock reuse
    # (_lock_incompatibility), ``nsx sync --frozen`` verifies module content
    # on every build, and CMake's regeneration rule reconfigures whenever a
    # rendered CMakeLists/template input changes.  A misclassified input
    # therefore costs a re-lock/reconfigure, never a wrong binary.
    #
    # The probe serial also left the identity: it was only here because
    # ``nsx flash`` baked it into the CMake cache, and the pipeline now
    # deploys via the direct J-Link recipe (stages/flash), so a workspace is
    # valid for any probe.
    #
    # ``engine.config`` stays whole even though some engines put render-ish
    # values in it (e.g. ExecuTorch arena sizes): it also pins dependency
    # refs like ``cmsis_nn_ref``, and splitting it per-engine buys little
    # for the risk — engines whose config embeds per-model values simply
    # keep forking per model, conservatively.
    inputs = _canonical(
        {
            "hpx_version": __version__,
            "baseline": {
                "id": compatibility.baseline.baseline_id,
                "fingerprint": compatibility.fingerprint,
            },
            "registry_hash": registry_hash.to_dict(),
            "board": asdict(board),
            "soc": asdict(ctx.soc) if ctx.soc is not None else None,
            "target": {
                "toolchain": ctx.config.target.toolchain,
                # Transport selects provider modules (USB CDC pulls in the
                # USB stack), so it is dependency identity, not render.
                "transport": ctx.config.target.transport,
                "psram": ctx.config.target.psram,
                "clock": ctx.config.target.clock,
                "segger_rtt_source_hash": (
                    _digest_path(ctx.config.target.segger_rtt_path).to_dict()
                    if ctx.config.target.segger_rtt_path is not None
                    else None
                ),
            },
            "engine": {
                "type": ctx.config.engine.type,
                "backend": ctx.config.engine.backend,
                "config": ctx.config.engine.config,
                "config_path_hash": (
                    _digest_path(ctx.config.engine.config_path).to_dict()
                    if ctx.config.engine.config_path is not None
                    else None
                ),
                # Engine-agnostic identity: each artifact subtype routes these
                # to its own fields (None where the engine has no such notion),
                # so the fingerprint inputs are unchanged for every engine.
                "resolved_backend": artifacts.resolved_backend,
                "resolved_variant": artifacts.resolved_variant,
                "resolved_version": artifacts.resolved_version,
                "toolchain_tag": artifacts.resolved_toolchain_tag,
                "cmake_vars": artifacts.cmake_vars,
                "extra_modules": extra_modules,
            },
            "build": {
                "channel": ctx.config.build.channel,
                "compiler_launcher": ctx.config.build.compiler_launcher,
                "module_overrides": module_overrides,
                "explicit_overrides": explicit_overrides,
            },
        }
    )
    fingerprint = hashlib.sha256(_canonical_bytes(inputs)).hexdigest()
    root = ctx.work_dir / "dependency-workspaces" / fingerprint
    root.mkdir(parents=True, exist_ok=True)
    workspace = DependencyWorkspace(
        schema_version=WORKSPACE_SCHEMA_VERSION,
        fingerprint=fingerprint,
        baseline_id=compatibility.baseline.baseline_id,
        baseline_fingerprint=compatibility.fingerprint,
        registry_hash=registry_hash,
        inputs=inputs,
        root=root,
    )
    _persist_workspace_identity(workspace)
    return workspace


def _persist_workspace_identity(workspace: DependencyWorkspace) -> None:
    path = workspace.root / _WORKSPACE_IDENTITY
    expected = workspace.to_dict()
    with file_mutex(workspace.root / ".hpx-workspace.lock"):
        if path.exists():
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise DependencyError(
                    f"Dependency workspace identity is unreadable: {path}",
                    hint="Remove this fingerprinted workspace and retry.",
                ) from exc
            if current != expected:
                raise DependencyError(
                    f"Dependency workspace fingerprint collision or incompatible state: {path}",
                    hint="Remove this fingerprinted workspace and retry.",
                )
            return
        _atomic_json(path, expected)


@contextmanager
def workspace_mutex(workspace: DependencyWorkspace) -> Iterator[None]:
    """Serialize render/build mutation of a fingerprinted workspace."""

    with file_mutex(workspace.root / ".hpx-workspace.lock"):
        yield


def _lock_incompatibility(app_dir: Path, board: str) -> tuple[str, type[DependencyError]] | None:
    """Return ``(reason, error_class)`` if the on-disk lock cannot be reused.

    *error_class* is :class:`VersionError` for a schema-version mismatch and
    :class:`LockError` for every other missing/corrupt/drifted lock reason,
    so callers can raise the exact taxonomy member without re-deriving it.
    """
    lock_path = app_dir / "nsx.lock"
    if not lock_path.is_file():
        return "nsx.lock is missing", LockError
    try:
        lock = read_lock(app_dir, board)
    except Exception as exc:
        # neuralspotx's own reader raises for an incompatible on-disk
        # ``schema_version`` (see ``NsxLock``/``LockFile.from_yaml_dict``)
        # rather than returning a lock object we could inspect below, so
        # that specific reason is classified as a version mismatch here.
        error_cls = VersionError if "schema_version" in str(exc) else LockError
        return f"nsx.lock is unreadable or structurally incompatible: {exc}", error_cls
    if lock is None:
        return f"nsx.lock has no target section for board '{board}'", LockError
    if lock.schema_version != LOCK_SCHEMA_VERSION:
        return (
            f"nsx.lock schema v{lock.schema_version} is incompatible "
            f"(neuralspotx requires v{LOCK_SCHEMA_VERSION})"
        ), VersionError
    expected_manifest_hash = hash_manifest(app_dir / "nsx.yml")
    if lock.manifest_hash != expected_manifest_hash:
        return "nsx.lock was produced from a different generated nsx.yml manifest", LockError
    if not lock.modules:
        return "nsx.lock contains no resolved modules", LockError
    for name, module in lock.modules.items():
        content_hash = module.content_hash.removeprefix("sha256:")
        if len(content_hash) != 64 or any(ch not in "0123456789abcdef" for ch in content_hash):
            return f"nsx.lock module '{name}' has an invalid content hash", LockError
        if str(module.kind) == "git":
            commit = module.commit or ""
            if len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit.lower()):
                return f"nsx.lock module '{name}' has no exact peeled commit", LockError
    return None


def prepare_locked_dependencies(ctx: PipelineContext) -> DependencyProvenance:
    """Reuse or explicitly resolve a lock, then materialize it frozen."""

    app_dir = ctx.resolved_firmware_dir
    board = ctx.resolved_board.name
    workspace = ctx.resolved_workspace
    config = ctx.config
    update_requested = config.build.update_dependencies
    offline = config.build.offline or config.frozen
    reason = _lock_incompatibility(app_dir, board)

    if update_requested:
        mode = DependencyLockMode.UPDATED
        nsx_cli.lock(
            app_dir,
            update=True,
            timeout_s=config.timeouts.configure_s,
            verbose=config.verbose,
        )
    elif reason is None:
        mode = DependencyLockMode.REUSED
        log.info("Reusing byte-exact compatible nsx.lock: %s", app_dir / "nsx.lock")
    elif offline:
        reason_message, error_cls = reason
        raise error_cls(
            f"Offline/frozen dependency reuse requires a compatible lock: {reason_message}.",
            hint=(
                "Run once online without --offline/--frozen to create the lock, or use "
                "--update-dependencies online to intentionally refresh it."
            ),
        )
    else:
        mode = DependencyLockMode.RESOLVED
        nsx_cli.lock(
            app_dir,
            update=False,
            timeout_s=config.timeouts.configure_s,
            verbose=config.verbose,
        )

    reason = _lock_incompatibility(app_dir, board)
    if reason is not None:
        reason_message, error_cls = reason
        raise error_cls(
            f"NSX produced an incompatible dependency lock: {reason_message}.",
            hint="Update neuralspotx or remove this fingerprinted workspace and retry.",
        )
    if offline:
        missing = _offline_materialization_error(app_dir, board)
        if missing is not None:
            raise LockError(
                f"Offline/frozen dependency sync cannot continue because {missing}.",
                hint="Run once online without --offline/--frozen to materialize the exact lock.",
            )
    if _sync_stamp_matches(app_dir, board, mode):
        log.info(
            "Skipping frozen dependency sync — workspace already verified "
            "against this exact nsx.lock."
        )
    else:
        _run_frozen_sync_with_repair(app_dir, workspace, config, offline)
        _write_sync_stamp(app_dir)

    provenance = _collect_provenance(ctx, mode=mode, offline=offline)
    _verify_baseline_resolution(ctx, provenance)
    _atomic_json(app_dir / _DEPENDENCY_STATE, provenance.to_dict())
    run_key = ctx.run_metadata.run_id or uuid.uuid4().hex
    snapshot = ctx.work_dir / "run-locks" / run_key / "nsx.lock"
    _atomic_copy(app_dir / "nsx.lock", snapshot)
    ctx.dependency_lock_path = snapshot
    ctx.run_metadata.dependencies = provenance
    return provenance


def read_dependency_lock_provenance(
    app_or_workspace_path: str | Path,
) -> DependencyLockProvenance:
    """Read typed lock provenance without mutating an app or workspace.

    *app_or_workspace_path* may name ``profiler_app``, its ``nsx.lock`` or
    ``hpx-dependencies.json``, or the parent fingerprint workspace containing
    ``profiler_app``. The exact on-disk lock digest is verified against the
    recorded run state before a surface is returned.
    """

    app_dir = _dependency_app_dir(Path(app_or_workspace_path))
    state_path = app_dir / _DEPENDENCY_STATE
    lock_path = app_dir / "nsx.lock"
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LockError(
            f"Cannot read dependency provenance state: {state_path}",
            hint="Run a successful HPX dependency preparation in this workspace first.",
        ) from exc
    if not isinstance(raw, Mapping):
        raise LockError(f"Dependency provenance state must be an object: {state_path}")

    workspace = _state_mapping(raw, "workspace", state_path)
    lock = _state_mapping(raw, "lock", state_path)
    recorded_lock_sha256 = _state_digest(lock.get("sha256"), "lock.sha256", state_path)
    if not lock_path.is_file():
        raise LockError(f"Dependency lock is missing: {lock_path}")
    try:
        actual_lock_sha256 = _digest_file(lock_path)
    except OSError as exc:
        raise LockError(f"Cannot read dependency lock: {lock_path}: {exc}") from exc
    if actual_lock_sha256 != recorded_lock_sha256:
        raise LockError(
            f"Dependency lock no longer matches recorded provenance: {lock_path}",
            hint="Re-run dependency preparation before collecting diagnostics.",
        )

    modules = tuple(
        _state_module(name, value, state_path)
        for name, value in _state_named_records(raw.get("modules"), "modules", state_path)
    )
    overrides = tuple(
        _state_override(index, value, state_path)
        for index, value in enumerate(_state_sequence(raw.get("overrides"), "overrides", state_path))
    )
    requests = [
        DependencyRequest(
            scope="module",
            name=module.name,
            requested_ref=module.requested_ref,
            requested_tag=module.requested_tag,
        )
        for module in modules
    ]
    project_requests = {
        (module.project, module.requested_ref, module.requested_tag) for module in modules
    }
    requests.extend(
        DependencyRequest(
            scope="project",
            name=project,
            requested_ref=requested_ref,
            requested_tag=requested_tag,
        )
        for project, requested_ref, requested_tag in sorted(
            project_requests,
            key=lambda value: (value[0], value[1], value[2] or ""),
        )
    )

    try:
        qualification = QualificationState(_state_string(raw, "qualification", state_path))
        lock_mode = DependencyLockMode(_state_string(lock, "mode", state_path))
    except ValueError as exc:
        raise LockError(f"Dependency provenance state has an invalid enum: {exc}") from exc
    update_requested = lock.get("update_requested")
    if not isinstance(update_requested, bool):
        raise LockError(
            f"Dependency provenance field 'lock.update_requested' must be boolean: {state_path}"
        )

    return DependencyLockProvenance(
        lock_path=lock_path,
        lock_sha256=actual_lock_sha256.value,
        registry_hash=_state_digest(
            workspace.get("registry_hash"), "workspace.registry_hash", state_path
        ).value,
        requested_refs=tuple(requests),
        resolved=modules,
        overrides=overrides,
        qualification=qualification,
        baseline_fingerprint=_state_string(
            workspace, "baseline_fingerprint", state_path
        ),
        workspace_fingerprint=_state_string(workspace, "fingerprint", state_path),
        lock_mode=lock_mode,
        update_requested=update_requested,
    )


def _verify_baseline_resolution(
    ctx: PipelineContext, provenance: DependencyProvenance
) -> None:
    """Fail when the lock resolves a baseline-pinned project off its ref.

    The generated manifest *asserts* qualified refs; the NSX lock is the
    *outcome*. The qualified-compatibility claim is only meaningful if the
    two agree, and NSX gives a packaged registry's module-level revision
    precedence over an app's project-level override — found 2026-08-12 when
    eight hardware runs silently built nsx-sensors v0.1.0 while every
    generated manifest and provenance artifact claimed the baseline commit.
    Runs whose divergence is intentional must say so through overrides
    (which reclassify qualification) rather than drift silently.
    """
    compatibility = ctx.config.compatibility
    if compatibility is None:
        return
    baseline = compatibility.baseline
    pinned = {project.name: project.ref for project in baseline.projects}
    engine_projects = {
        engine.name for engine in baseline.engines if engine.ref is not None
    }
    module_projects = {module.name: module.project for module in provenance.modules}
    skipped: set[str] = set()
    for override in provenance.overrides:
        if override.scope == "project":
            skipped.add(override.name)
        elif override.scope == "module":
            project = module_projects.get(override.name)
            if project is not None:
                skipped.add(project)
        elif override.scope == "engine":
            # Engine source overrides redirect engine-owned projects; their
            # divergence is already classified by qualification state.
            skipped |= engine_projects
            if override.name in {"cmsis_nn_path", "cmsis_nn_ref"}:
                provider_project = "ns-cmsis-nn"
                if (
                    str(ctx.config.engine.type) == "executorch"
                    and ctx.config.engine.backend == "arm"
                ):
                    provider_project = "arm-cmsis-nn"
                skipped.add(provider_project)
    for module in provenance.modules:
        expected = pinned.get(module.project)
        if (
            expected is None
            or module.project in skipped
            or module.kind != "git"
            or not module.peeled_commit
        ):
            continue
        if module.peeled_commit != expected:
            raise VersionError(
                f"Locked dependency '{module.name}' resolved to commit "
                f"{module.peeled_commit}, but the qualified baseline pins project "
                f"'{module.project}' at {expected}.",
                hint=(
                    "The generated manifest and NSX's resolution disagree — the "
                    "run would silently build unqualified sources. Re-resolve with "
                    "--update-dependencies (or remove the fingerprinted workspace); "
                    "for intentional divergence use explicit build.nsx_modules or "
                    "engine.config source overrides so qualification is classified "
                    "honestly."
                ),
            )


def _collect_provenance(
    ctx: PipelineContext,
    *,
    mode: DependencyLockMode,
    offline: bool,
) -> DependencyProvenance:
    firmware_dir = ctx.resolved_firmware_dir
    workspace = ctx.resolved_workspace
    lock = read_lock(firmware_dir, ctx.resolved_board.name)
    if lock is None:
        raise LockError("Cannot record dependency provenance without an exact NSX lock.")
    lock_path = firmware_dir / "nsx.lock"
    compatibility = ctx.config.compatibility
    if compatibility is None:
        raise DependencyError("Cannot record dependency provenance without compatibility state.")
    modules = tuple(
        DependencyModule(
            name=name,
            project=module.project,
            kind=str(module.kind),
            requested_ref=module.constraint,
            requested_tag=module.tag,
            peeled_commit=module.commit,
            content_hash=ContentDigest(
                "sha256", module.content_hash.removeprefix("sha256:")
            ),
            url=module.url,
            vendored_at=module.vendored_at,
        )
        for name, module in sorted(lock.modules.items())
    )
    _, overrides = _override_inputs(ctx)
    override_list = list(overrides)
    by_module = lock.modules
    project_overrides: set[tuple[str, str, str]] = set()
    for name, override in sorted(ctx.config.build.nsx_modules.items()):
        if override.path is not None or name not in by_module:
            continue
        mode_name = "ref" if override.ref is not None else "version"
        requested = override.ref if override.ref is not None else override.version
        assert requested is not None
        project_overrides.add((by_module[name].project, mode_name, requested))
    override_list.extend(
        DependencyOverride("project", project, mode_name, requested)
        for project, mode_name, requested in sorted(project_overrides)
    )
    manifest_digest = lock.manifest_hash.removeprefix("sha256:")
    return DependencyProvenance(
        workspace=workspace,
        lock=DependencyLockState(
            mode=mode,
            update_requested=ctx.config.build.update_dependencies,
            offline=offline,
            frozen_sync=True,
            schema_version=lock.schema_version,
            sha256=_digest_file(lock_path),
            manifest_hash=ContentDigest("sha256", manifest_digest),
        ),
        modules=modules,
        overrides=tuple(override_list),
        qualification=compatibility.qualification,
    )


def _dependency_app_dir(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.is_file():
        if resolved.name not in {"nsx.lock", _DEPENDENCY_STATE}:
            raise LockError(
                f"Expected nsx.lock or {_DEPENDENCY_STATE}, got: {resolved}"
            )
        resolved = resolved.parent
    if (resolved / _DEPENDENCY_STATE).is_file():
        return resolved
    app_dir = resolved / "profiler_app"
    if (app_dir / _DEPENDENCY_STATE).is_file():
        return app_dir
    raise LockError(
        f"No prepared HPX dependency app found at: {resolved}",
        hint=(
            "Pass a profiler_app directory, its nsx.lock/state file, or the "
            "fingerprinted workspace containing profiler_app."
        ),
    )


def _state_mapping(
    value: Mapping[str, Any],
    key: str,
    state_path: Path,
) -> Mapping[str, Any]:
    result = value.get(key)
    if not isinstance(result, Mapping):
        raise LockError(
            f"Dependency provenance field '{key}' must be an object: {state_path}"
        )
    return result


def _state_sequence(value: Any, key: str, state_path: Path) -> list[Any]:
    if not isinstance(value, list):
        raise LockError(
            f"Dependency provenance field '{key}' must be an array: {state_path}"
        )
    return value


def _state_named_records(
    value: Any,
    key: str,
    state_path: Path,
) -> tuple[tuple[str, Any], ...]:
    records = _state_sequence(value, key, state_path)
    named: list[tuple[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise LockError(
                f"Dependency provenance field '{key}[{index}]' must be an object: {state_path}"
            )
        named.append((_state_string(record, "name", state_path), record))
    return tuple(named)


def _state_string(value: Mapping[str, Any], key: str, state_path: Path) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise LockError(
            f"Dependency provenance field '{key}' must be a non-empty string: {state_path}"
        )
    return result


def _state_optional_string(
    value: Mapping[str, Any],
    key: str,
    state_path: Path,
) -> str | None:
    result = value.get(key)
    if result is None:
        return None
    if not isinstance(result, str) or not result:
        raise LockError(
            f"Dependency provenance field '{key}' must be a string or null: {state_path}"
        )
    return result


def _state_digest(value: Any, key: str, state_path: Path) -> ContentDigest:
    if not isinstance(value, Mapping):
        raise LockError(
            f"Dependency provenance field '{key}' must be a digest object: {state_path}"
        )
    algorithm = _state_string(value, "algorithm", state_path)
    digest = _state_string(value, "value", state_path)
    if algorithm != "sha256" or len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise LockError(
            f"Dependency provenance field '{key}' must be a lowercase SHA-256 digest: "
            f"{state_path}"
        )
    return ContentDigest(algorithm, digest)


def _state_module(name: str, value: Any, state_path: Path) -> DependencyModule:
    if not isinstance(value, Mapping):
        raise LockError(f"Dependency module '{name}' must be an object: {state_path}")
    vendored_at = value.get("vendored_at")
    if not isinstance(vendored_at, str):
        raise LockError(
            f"Dependency module '{name}' vendored_at must be a string: {state_path}"
        )
    return DependencyModule(
        name=name,
        project=_state_string(value, "project", state_path),
        kind=_state_string(value, "kind", state_path),
        requested_ref=_state_string(value, "requested_ref", state_path),
        requested_tag=_state_optional_string(value, "requested_tag", state_path),
        peeled_commit=_state_optional_string(value, "peeled_commit", state_path),
        content_hash=_state_digest(
            value.get("content_hash"), f"modules.{name}.content_hash", state_path
        ),
        url=_state_optional_string(value, "url", state_path),
        vendored_at=vendored_at,
    )


def _state_override(index: int, value: Any, state_path: Path) -> DependencyOverride:
    if not isinstance(value, Mapping):
        raise LockError(
            f"Dependency override at index {index} must be an object: {state_path}"
        )
    content_hash_raw = value.get("content_hash")
    return DependencyOverride(
        scope=_state_string(value, "scope", state_path),
        name=_state_string(value, "name", state_path),
        mode=_state_string(value, "mode", state_path),
        requested=_state_string(value, "requested", state_path),
        content_hash=(
            _state_digest(
                content_hash_raw,
                f"overrides[{index}].content_hash",
                state_path,
            )
            if content_hash_raw is not None
            else None
        ),
    )


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        shutil.copyfile(source, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
