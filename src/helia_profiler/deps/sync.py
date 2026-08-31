"""Frozen dependency sync — verification, repair, and the stamp fast path.

``nsx sync --frozen`` verifies a workspace byte-for-byte against nsx.lock by
re-hashing every vendored module file — ~3s per run on a fully warm
workspace, paid once per firmware build.  The stamp records the nsx.lock
digest of the last successful frozen verification of this workspace, so an
unchanged lock skips straight past re-verification.  What the skip trades
away is detection of OUT-OF-BAND edits to vendored module trees; those are
unsupported (path overrides exist for intentional local sources), and every
refresh path still re-verifies: a lock rewrite (``--update-dependencies``),
a cleaned workspace (``--clean``), or stamp invalidation after a failed
build (see ``firmware.build.build_app``).

Extracted from ``dependencies`` at the module size ceiling (see the
launcher/elf_inventory precedent); ``dependencies`` re-exports the public
name so callers keep one import surface.

NOTE: ``nsx_cli`` is imported as a module (never ``from .... import sync``) so
tests that monkeypatch ``helia_profiler.dependencies.nsx_cli.*`` keep
patching the same module object this code reads at call time.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from neuralspotx.nsx_lock import read_lock

from . import nsx as nsx_cli
from ..errors import BuildError, LockError

if TYPE_CHECKING:
    from ..config import ProfileConfig
    from ..results.dependencies import DependencyLockMode, DependencyWorkspace

log = logging.getLogger("hpx")


_SYNC_STAMP = "hpx-frozen-sync.json"


def _lock_sha256(app_dir: Path) -> str:
    digest = hashlib.sha256()
    with (app_dir / "nsx.lock").open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_sync_lock_digest(app_dir: Path) -> str | None:
    """Return the nsx.lock sha256 recorded by the last verified frozen sync."""
    try:
        raw = json.loads((app_dir / _SYNC_STAMP).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    digest = raw.get("lock_sha256") if isinstance(raw, Mapping) else None
    return digest if isinstance(digest, str) else None


def _write_sync_stamp(app_dir: Path) -> None:
    """Record the current nsx.lock digest as frozen-sync verified."""
    try:
        payload = json.dumps({"lock_sha256": _lock_sha256(app_dir)})
    except OSError:  # pragma: no cover — lock vanished mid-run; stay unstamped
        return
    stamp = app_dir / _SYNC_STAMP
    scratch = stamp.with_name(stamp.name + ".tmp")
    scratch.write_text(payload, encoding="utf-8")
    scratch.replace(stamp)


def invalidate_sync_stamp(app_dir: Path) -> None:
    """Forget the frozen-sync stamp so the next run fully re-verifies."""
    (app_dir / _SYNC_STAMP).unlink(missing_ok=True)


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


def _sync_stamp_matches(app_dir: Path, board: str, mode: DependencyLockMode) -> bool:
    """True when frozen sync may be skipped for this run.

    Only a byte-exact REUSED lock qualifies — a lock that was just resolved
    or updated has never had this workspace verified against it.  The stamp
    must record exactly the current nsx.lock digest, and every locked module
    tree must still be materialized (a half-deleted workspace re-verifies and
    repairs rather than building from whatever is left).
    """
    if mode.value != "reused":
        return False
    stamp = _verified_sync_lock_digest(app_dir)
    if stamp is None:
        return False
    try:
        current = _lock_sha256(app_dir)
    except OSError:
        return False
    return stamp == current and _offline_materialization_error(app_dir, board) is None


def _run_frozen_sync_with_repair(
    app_dir: Path,
    workspace: DependencyWorkspace,
    config: ProfileConfig,
    offline: bool,
) -> None:
    """Verify the workspace via ``nsx sync --frozen``, repairing once online."""
    try:
        nsx_cli.sync(
            app_dir,
            frozen=True,
            timeout_s=config.timeouts.configure_s,
            verbose=config.verbose,
        )
    except BuildError as exc:
        if offline:
            raise LockError(
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
            raise LockError(
                "Dependency module repair from the exact lock failed.",
                details=repair_exc.details,
                hint=(
                    f"Remove the isolated workspace at {workspace.root} "
                    "and retry. Use explicit path overrides for intentional local edits."
                ),
            ) from repair_exc
