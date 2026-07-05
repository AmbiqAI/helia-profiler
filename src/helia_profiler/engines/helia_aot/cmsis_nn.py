"""ns-cmsis-nn (CMSIS-NN fork) resolution and NSX module wrapping.

heliaAOT-generated code links against ``ns-cmsis-nn`` (the AmbiqAI CMSIS-NN
fork with API compatible with heliaAOT's codegen — upstream ``cmsis-nn``
V.19+ has dropped parameters heliaAOT still targets). By default the module
is resolved from the NSX registry (NSX clones it from GitHub during
``nsx sync``); a user-provided local checkout is vendored as a local NSX
module instead. Also used by the heliaRT source-build path, which links the
same CMSIS-NN kernels.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from ...config import ProfileConfig
from ...errors import EngineError
from ...results import NsxModuleRef

log = logging.getLogger("hpx")

_CMSIS_NN_GH_REPO = "AmbiqAI/ns-cmsis-nn"
_CMSIS_NN_CACHE_DIR = Path.home() / ".cache" / "helia-profiler" / "ns-cmsis-nn"

# NSX registry identity for ns-cmsis-nn. By default hpx declares this module
# and lets NSX clone it from the registered GitHub upstream; a user-provided
# local path (cmsis_nn_path / CMSIS_NN_PATH) vendors it instead.
CMSIS_NN_PROJECT = "ns-cmsis-nn"  # registry project (path: modules/ns-cmsis-nn)
CMSIS_NN_MODULE = "nsx-cmsis-nn"  # registry module name


def cmsis_nn_module_ref(config: ProfileConfig, work_dir: Path) -> NsxModuleRef:
    """Resolve the ns-cmsis-nn NSX module reference.

    By default the module is resolved from the NSX registry (NSX clones it
    from the registered GitHub upstream during ``nsx sync``). When the user
    provides a local checkout via ``engine.config.cmsis_nn_path`` or the
    ``CMSIS_NN_PATH`` environment variable, it is vendored as a local module
    under its registry-derived project directory (``modules/ns-cmsis-nn``).
    """
    raw = config.engine.config.get("cmsis_nn_path") or os.environ.get("CMSIS_NN_PATH")
    if raw:
        cmsis_nn_path = Path(str(raw)).expanduser().resolve()
        _validate_cmsis_nn(cmsis_nn_path)
        mod_dir = work_dir / "modules" / CMSIS_NN_PROJECT
        _write_cmsis_nn_wrapper(mod_dir, cmsis_nn_path)
        log.info("ns-cmsis-nn: vendoring local module from %s", cmsis_nn_path)
        return NsxModuleRef(
            name=CMSIS_NN_MODULE,
            path=mod_dir,
            local=True,
            project=CMSIS_NN_PROJECT,
        )

    log.info(
        "ns-cmsis-nn — resolving %s from NSX registry (project=%s)",
        CMSIS_NN_MODULE,
        CMSIS_NN_PROJECT,
    )
    return NsxModuleRef(
        name=CMSIS_NN_MODULE,
        path=Path(),
        local=False,
        project=CMSIS_NN_PROJECT,
    )


def _resolve_cmsis_nn(config: ProfileConfig) -> Path:
    """Resolve the ns-cmsis-nn source tree path.

    Checks (in order):
    1. ``engine.config.cmsis_nn_path`` — explicit user-provided path
    2. ``CMSIS_NN_PATH`` environment variable
    3. Auto-clone from GitHub (cached at ``~/.cache/helia-profiler/ns-cmsis-nn/``)
    """
    raw = config.engine.config.get("cmsis_nn_path")
    if raw:
        p = Path(raw).expanduser().resolve()
        _validate_cmsis_nn(p)
        return p

    env = os.environ.get("CMSIS_NN_PATH")
    if env:
        p = Path(env).expanduser().resolve()
        _validate_cmsis_nn(p)
        return p

    # Auto-clone from GitHub
    return _auto_clone_cmsis_nn()


def _validate_cmsis_nn(path: Path) -> None:
    """Verify that *path* looks like an ns-cmsis-nn checkout.

    Also checks the header revision against what heliaAOT expects.
    heliaAOT generates code targeting ns-cmsis-nn (AmbiqAI fork) — the
    upstream ``cmsis-nn`` V.19+ has incompatible API changes (e.g. dropped
    ``weight_sum_ctx`` parameter from ``arm_convolve_1x1_s8_fast``).
    """
    if not path.is_dir():
        raise EngineError(f"CMSIS-NN path does not exist: {path}")
    for d in ("Include", "Source"):
        if not (path / d).is_dir():
            raise EngineError(
                f"CMSIS-NN path missing '{d}/' directory: {path}",
                hint="Expected an ns-cmsis-nn repository with Include/ and Source/.",
            )

    # Warn if the header revision looks like upstream V.19+ (incompatible).
    header = path / "Include" / "arm_nnfunctions.h"
    if header.is_file():
        import re as _re

        text = header.read_text(errors="replace")[:2048]
        m = _re.search(r"\$Revision:\s*V\.(\d+)\.", text)
        if m and int(m.group(1)) >= 19:
            raise EngineError(
                f"CMSIS-NN at {path} is V.{m.group(1)}.x (upstream) — "
                "heliaAOT requires ns-cmsis-nn (AmbiqAI fork) V.18 or earlier.",
                hint=(
                    "Point cmsis_nn_path to a ns-cmsis-nn checkout. "
                    "See https://github.com/AmbiqAI/ns-cmsis-nn"
                ),
            )


def _auto_clone_cmsis_nn() -> Path:
    """Clone ns-cmsis-nn from GitHub into a local cache directory."""
    cache = _CMSIS_NN_CACHE_DIR
    if cache.is_dir() and (cache / "Include").is_dir() and (cache / "Source").is_dir():
        log.info("ns-cmsis-nn: cache hit at %s", cache)
        _validate_cmsis_nn(cache)
        return cache

    repo_url = f"https://github.com/{_CMSIS_NN_GH_REPO}.git"
    log.info("Cloning ns-cmsis-nn from %s ...", repo_url)
    cache.parent.mkdir(parents=True, exist_ok=True)

    # Remove stale partial clone
    if cache.exists():
        shutil.rmtree(cache)

    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(cache)],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise EngineError(
            f"Failed to clone ns-cmsis-nn from {repo_url}",
            hint=(
                "Clone manually and set CMSIS_NN_PATH:\n"
                f"  git clone {repo_url} /path/to/ns-cmsis-nn\n"
                "  export CMSIS_NN_PATH=/path/to/ns-cmsis-nn"
            ),
        ) from exc

    log.info("Cloned ns-cmsis-nn to %s", cache)
    _validate_cmsis_nn(cache)
    return cache


# ---------------------------------------------------------------------------
# NSX module generation — CMSIS-NN
# ---------------------------------------------------------------------------


def _write_cmsis_nn_wrapper(module_dir: Path, cmsis_nn_path: Path) -> None:
    """Write the NSX module for ns-cmsis-nn.

    Uses the native ``nsx/`` module that ships with ns-cmsis-nn (>= v7.23.0).
    A thin root shim delegates to ``nsx/CMakeLists.txt`` so that its
    ``../Source`` relative paths resolve correctly against the copied
    Source/ tree.
    """
    module_dir.mkdir(parents=True, exist_ok=True)

    native_nsx = cmsis_nn_path / "nsx"
    if (
        not (native_nsx / "CMakeLists.txt").is_file()
        or not (native_nsx / "nsx-module.yaml").is_file()
    ):
        raise EngineError(
            f"ns-cmsis-nn at {cmsis_nn_path} is missing native nsx/ module",
            hint=(
                "Expected nsx/CMakeLists.txt and nsx/nsx-module.yaml. "
                "Use ns-cmsis-nn >= v7.23.0 (AmbiqAI/ns-cmsis-nn)."
            ),
        )

    log.info("Using native nsx/ module from %s", cmsis_nn_path)

    # Copy the native manifest to the module root
    shutil.copy2(native_nsx / "nsx-module.yaml", module_dir / "nsx-module.yaml")

    # Place the native CMakeLists.txt in a subdirectory so its
    # relative paths (../Source, ../Include) resolve against the
    # copied Source/ and Include/ trees at the module root.
    nsx_subdir = module_dir / "nsx"
    nsx_subdir.mkdir(exist_ok=True)
    shutil.copy2(native_nsx / "CMakeLists.txt", nsx_subdir / "CMakeLists.txt")

    # Root shim delegates to the native build
    (module_dir / "CMakeLists.txt").write_text(
        "# Shim — delegates to the native ns-cmsis-nn NSX build.\nadd_subdirectory(nsx)\n"
    )

    # Copy the CMSIS-NN source tree into the module (no symlinks — Windows-safe)
    for d in ("Include", "Source", "cmake"):
        target = module_dir / d
        source = cmsis_nn_path / d
        if not source.is_dir():
            continue
        if target.is_dir():
            shutil.rmtree(target)
        shutil.copytree(source, target)
