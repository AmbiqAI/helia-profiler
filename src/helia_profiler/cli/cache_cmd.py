"""Implementation of the ``hpx cache`` command (purge/info)."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def _cmd_cache(args: argparse.Namespace) -> None:
    action = getattr(args, "cache_action", None)
    if action == "purge":
        _cmd_cache_purge()
    elif action == "info":
        _cmd_cache_info()
    else:
        print("Usage: hpx cache {purge|info}", file=sys.stderr)
        sys.exit(1)


def _workspace_cache_root() -> Path:
    return Path.home() / ".cache" / "helia-profiler" / "workspaces"


def _cmd_cache_purge() -> None:
    """Purge hpx/nsx caches (module cache + resolve-ref cache + workspaces)."""
    from neuralspotx import _resolve_cache, module_cache

    # 1. Clear the module content-addressed cache
    n_modules = module_cache.clear()
    if n_modules:
        print(f"  Purged {n_modules} cached module(s).")
    else:
        print("  Module cache already empty.")

    # 2. Invalidate the resolve-ref TTL cache
    _resolve_cache.invalidate_all()
    print("  Purged resolve-ref cache.")

    # 3. Remove persistent per-board workspaces (generated apps + nsx.lock)
    workspaces_root = _workspace_cache_root()
    if workspaces_root.is_dir():
        n_workspaces = sum(1 for child in workspaces_root.iterdir() if child.is_dir())
        shutil.rmtree(workspaces_root, ignore_errors=True)
        print(f"  Purged {n_workspaces} cached workspace(s).")
    else:
        print("  Workspace cache already empty.")

    print("Done — next profile/build will recreate workspaces and refresh module state.")


def _cmd_cache_info() -> None:
    """Show cache location and approximate disk usage."""
    from neuralspotx import module_cache
    from neuralspotx._resolve_cache import _cache_path

    mod_root = module_cache.module_cache_root()
    resolve_path = _cache_path()
    workspaces_root = _workspace_cache_root()

    print(f"Module cache:      {mod_root}")
    if mod_root.is_dir():
        entries = module_cache.iter_entries()
        total_bytes = sum(f.stat().st_size for e in entries for f in e.rglob("*") if f.is_file())
        print(f"  Entries: {len(entries)}, Size: {total_bytes / 1024 / 1024:.1f} MB")
    else:
        print("  (empty)")

    print(f"Resolve-ref cache: {resolve_path}")
    if resolve_path.exists():
        size = resolve_path.stat().st_size
        print(f"  Size: {size / 1024:.1f} KB")
    else:
        print("  (empty)")

    print(f"Workspace cache:   {workspaces_root}")
    if workspaces_root.is_dir():
        entries = [entry for entry in workspaces_root.iterdir() if entry.is_dir()]
        total_bytes = sum(
            f.stat().st_size for entry in entries for f in entry.rglob("*") if f.is_file()
        )
        print(f"  Entries: {len(entries)}, Size: {total_bytes / 1024 / 1024:.1f} MB")
    else:
        print("  (empty)")
