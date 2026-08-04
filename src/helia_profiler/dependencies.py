"""Deterministic NSX dependency workspaces and lock reuse."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import uuid
from contextlib import contextmanager
from dataclasses import asdict, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from neuralspotx.file_lock import file_mutex
from neuralspotx.nsx_lock import LOCK_SCHEMA_VERSION, hash_manifest, read_lock

from . import nsx as nsx_cli
from ._version import __version__
from .errors import BuildError, DependencyError
from .results.dependencies import (
    ContentDigest,
    DependencyLockMode,
    DependencyLockState,
    DependencyModule,
    DependencyOverride,
    DependencyProvenance,
    DependencyWorkspace,
)

if TYPE_CHECKING:
    from .pipeline import PipelineContext

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
    source = engine_config.get("source")
    if source is not None:
        if not isinstance(source, dict):
            raise DependencyError(
                "engine.config.source must contain a repository/ref mapping."
            )
        from .engines.helia_rt.artifacts import HELIART_GH_REPO, HELIART_RELEASE_TAG

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

    assert ctx.board is not None
    assert ctx.engine_artifacts is not None
    compatibility = ctx.config.compatibility
    if compatibility is None:
        raise DependencyError("Compatibility baseline is unavailable for dependency locking.")

    registry_hash = _registry_digest()
    module_overrides, explicit_overrides = _override_inputs(ctx)
    artifacts = ctx.engine_artifacts
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

    inputs = _canonical(
        {
            "hpx_version": __version__,
            "baseline": {
                "id": compatibility.baseline.baseline_id,
                "fingerprint": compatibility.fingerprint,
            },
            "registry_hash": registry_hash.to_dict(),
            "board": asdict(ctx.board),
            "soc": asdict(ctx.soc) if ctx.soc is not None else None,
            "target": {
                "toolchain": ctx.config.target.toolchain,
                "transport": ctx.config.target.transport,
                "psram": ctx.config.target.psram,
                "clock": ctx.config.target.clock,
                "heartbeat": ctx.config.target.heartbeat,
                "rtt_buffer_size_up": ctx.config.target.rtt_buffer_size_up,
                "segger_rtt_source_hash": (
                    _digest_path(ctx.config.target.segger_rtt_path).to_dict()
                    if ctx.config.target.segger_rtt_path is not None
                    else None
                ),
                "probe_serial": (
                    ctx.resolved_jlink_serial or ctx.config.target.jlink_serial
                ),
            },
            "model": {
                "sha256": _digest_path(ctx.config.model.path).value,
                "arena_size": ctx.config.model.arena_size,
                "arena_location": ctx.config.model.arena_location,
                "weights_location": ctx.config.model.weights_location,
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
                "resolved_backend": artifacts.engine_backend,
                "resolved_variant": artifacts.heliart_variant,
                "resolved_version": artifacts.heliart_version or artifacts.helia_aot_version,
                "toolchain_tag": artifacts.heliart_toolchain_tag,
                "cmake_vars": artifacts.cmake_vars,
                "extra_modules": extra_modules,
            },
            "build": {
                "channel": ctx.config.build.channel,
                "compiler_launcher": ctx.config.build.compiler_launcher,
                "module_overrides": module_overrides,
                "explicit_overrides": explicit_overrides,
            },
            "module_selection": {
                "profiling": ctx.config.profiling,
                "power": ctx.config.power,
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


def _lock_incompatibility(app_dir: Path, board: str) -> str | None:
    lock_path = app_dir / "nsx.lock"
    if not lock_path.is_file():
        return "nsx.lock is missing"
    try:
        lock = read_lock(app_dir, board)
    except Exception as exc:
        return f"nsx.lock is unreadable or structurally incompatible: {exc}"
    if lock is None:
        return f"nsx.lock has no target section for board '{board}'"
    if lock.schema_version != LOCK_SCHEMA_VERSION:
        return (
            f"nsx.lock schema v{lock.schema_version} is incompatible "
            f"(neuralspotx requires v{LOCK_SCHEMA_VERSION})"
        )
    expected_manifest_hash = hash_manifest(app_dir / "nsx.yml")
    if lock.manifest_hash != expected_manifest_hash:
        return "nsx.lock was produced from a different generated nsx.yml manifest"
    if not lock.modules:
        return "nsx.lock contains no resolved modules"
    for name, module in lock.modules.items():
        content_hash = module.content_hash.removeprefix("sha256:")
        if len(content_hash) != 64 or any(ch not in "0123456789abcdef" for ch in content_hash):
            return f"nsx.lock module '{name}' has an invalid content hash"
        if str(module.kind) == "git":
            commit = module.commit or ""
            if len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit.lower()):
                return f"nsx.lock module '{name}' has no exact peeled commit"
    return None


def _offline_materialization_error(app_dir: Path, board: str) -> str | None:
    lock = read_lock(app_dir, board)
    if lock is None:
        return f"nsx.lock has no target section for board '{board}'"
    missing = sorted(
        {
            module.vendored_at
            for module in lock.modules.values()
            if module.vendored_at and not (app_dir / module.vendored_at).exists()
        }
    )
    if missing:
        return "locked module trees are missing: " + ", ".join(missing)
    return None


def prepare_locked_dependencies(ctx: PipelineContext) -> DependencyProvenance:
    """Reuse or explicitly resolve a lock, then materialize it frozen."""

    assert ctx.firmware_dir is not None
    assert ctx.board is not None
    assert ctx.dependency_workspace is not None
    app_dir = ctx.firmware_dir
    board = ctx.board.name
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
        raise DependencyError(
            f"Offline/frozen dependency reuse requires a compatible lock: {reason}.",
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
        raise DependencyError(
            f"NSX produced an incompatible dependency lock: {reason}.",
            hint="Update neuralspotx or remove this fingerprinted workspace and retry.",
        )
    if offline:
        missing = _offline_materialization_error(app_dir, board)
        if missing is not None:
            raise DependencyError(
                f"Offline/frozen dependency sync cannot continue because {missing}.",
                hint="Run once online without --offline/--frozen to materialize the exact lock.",
            )
    try:
        nsx_cli.sync(
            app_dir,
            frozen=True,
            timeout_s=config.timeouts.configure_s,
            verbose=config.verbose,
        )
    except BuildError as exc:
        if offline:
            raise DependencyError(
                "Frozen dependency sync rejected the locked workspace.",
                details=exc.details,
                hint=(
                    "Offline mode cannot repair missing or modified module trees. Run once "
                    "online, or remove the fingerprinted workspace and retry online."
                ),
            ) from exc
        log.warning(
            "Repairing module materialization from the existing exact nsx.lock after "
            "frozen verification failed."
        )
        try:
            # Non-frozen sync repairs only module materialization at the exact
            # commits/content hashes already in nsx.lock; it never resolves refs
            # or rewrites the lock. Verify frozen again before proceeding.
            nsx_cli.sync(
                app_dir,
                frozen=False,
                force=True,
                timeout_s=config.timeouts.configure_s,
                verbose=config.verbose,
            )
            nsx_cli.sync(
                app_dir,
                frozen=True,
                timeout_s=config.timeouts.configure_s,
                verbose=config.verbose,
            )
        except BuildError as repair_exc:
            raise DependencyError(
                "Dependency module repair from the exact lock failed.",
                details=repair_exc.details,
                hint=(
                    f"Remove the isolated workspace at {ctx.dependency_workspace.root} "
                    "and retry. Use explicit path overrides for intentional local edits."
                ),
            ) from repair_exc

    provenance = _collect_provenance(ctx, mode=mode, offline=offline)
    _atomic_json(app_dir / _DEPENDENCY_STATE, provenance.to_dict())
    run_key = ctx.run_metadata.run_id or uuid.uuid4().hex
    snapshot = ctx.work_dir / "run-locks" / run_key / "nsx.lock"
    _atomic_copy(app_dir / "nsx.lock", snapshot)
    ctx.dependency_lock_path = snapshot
    ctx.run_metadata.dependencies = provenance
    return provenance


def _collect_provenance(
    ctx: PipelineContext,
    *,
    mode: DependencyLockMode,
    offline: bool,
) -> DependencyProvenance:
    assert ctx.firmware_dir is not None
    assert ctx.board is not None
    assert ctx.dependency_workspace is not None
    lock = read_lock(ctx.firmware_dir, ctx.board.name)
    if lock is None:
        raise DependencyError("Cannot record dependency provenance without an exact NSX lock.")
    lock_path = ctx.firmware_dir / "nsx.lock"
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
        workspace=ctx.dependency_workspace,
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
