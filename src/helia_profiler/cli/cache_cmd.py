"""Implementation of the ``hpx cache`` command (purge/info)."""

from __future__ import annotations

import shutil
from pathlib import Path


def _workspace_cache_root() -> Path:
    from ..hostenv.cache_dirs import hpx_cache_root

    return hpx_cache_root() / "workspaces"


def _cmd_cache_purge() -> None:
    """Purge all NSX persistent caches and HPX workspaces."""
    from neuralspotx import clean_cache

    nsx_result = clean_cache()
    if nsx_result.removed_count:
        print(f"  Purged {nsx_result.removed_count} neuralSPOT-X cache item(s).")
    else:
        print("  neuralSPOT-X caches already empty.")

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
    from neuralspotx import cache_info, clean_cache

    nsx_info = cache_info()
    nsx_preview = clean_cache(dry_run=True)
    workspaces_root = _workspace_cache_root()

    print(f"neuralSPOT-X cache: {nsx_preview.root}")
    print(
        f"  Module entries: {nsx_info.entry_count}, "
        f"Size: {nsx_info.total_size_bytes / 1024 / 1024:.1f} MB"
    )
    print(f"  Purgeable items: {nsx_preview.removed_count}")

    print(f"Workspace cache:   {workspaces_root}")
    if workspaces_root.is_dir():
        entries = [entry for entry in workspaces_root.iterdir() if entry.is_dir()]
        total_bytes = sum(
            f.stat().st_size for entry in entries for f in entry.rglob("*") if f.is_file()
        )
        print(f"  Entries: {len(entries)}, Size: {total_bytes / 1024 / 1024:.1f} MB")
    else:
        print("  (empty)")
