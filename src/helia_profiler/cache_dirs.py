"""Resolution of the persistent hpx cache root.

Every hpx on-disk cache (incremental firmware workspaces, downloaded engine
distributions, vendored dependency checkouts, packaged example models) lives
under one root so ``hpx cache`` can purge and report them together.

Resolution order:

1. ``HPX_CACHE_DIR`` — explicit override, used verbatim as the root.
2. ``$XDG_CACHE_HOME/helia-profiler`` — the XDG base-directory convention.
3. ``~/.cache/helia-profiler`` — the default.

The override exists because ``Path.home()`` is not always writable: the
hardware-validation runner's service account has a NixOS-managed read-only
home, and every profile run crashed on ``mkdir ~/.cache`` until the workflow
pointed ``HPX_CACHE_DIR`` at a writable persistent path (see
.github/workflows/hardware-validation.yml).
"""

from __future__ import annotations

import os
from pathlib import Path


def hpx_cache_root() -> Path:
    """Return the root directory for hpx's persistent caches.

    Resolved at call time so environment overrides apply per-invocation;
    callers must not cache the result at import time.
    """
    override = os.environ.get("HPX_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg).expanduser() / "helia-profiler"
    return Path.home() / ".cache" / "helia-profiler"
