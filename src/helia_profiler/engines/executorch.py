"""ExecuTorch engine adapter for NSX target profiling."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..config import ProfileConfig
from ..errors import EngineError
from ..placement import Placement
from ..results import NsxModuleRef
from . import EngineType
from .base import ArenaRegion, EngineArtifacts

log = logging.getLogger("hpx")

# CMSIS-NN provider -> (registry project, override cache var) consumed by
# nsx-executorch's own CMakeLists.txt (NSX_EXECUTORCH_CMSIS_NN_PROVIDER).
#
# Exactly one provider is materialized per run — never both. nsx-executorch's
# CMakeLists.txt add_subdirectory()s the selected provider's source itself
# (directly, or transitively via ExecuTorch's stock CMSIS_NN_LOCAL_PATH
# hook) — it does not expect the NSX app's own generic module bootstrap to
# have already add_subdirectory()'d the identical source tree. Declaring the
# provider as a normal NSX_APP_MODULE would make `nsx_bootstrap_app()` do
# exactly that, colliding with nsx-executorch's own add_subdirectory() of the
# same directory (duplicate `cmsis-nn` / `nsx_*_cmsis_nn` targets). So HPX
# materializes the selected provider itself, pinned to the HPX compatibility
# baseline's qualified commit, and hands nsx-executorch the checkout via its
# documented standalone override cache var — the same override path its own
# README/CMakeLists.txt describe for "standalone/test consumers".
_CMSIS_NN_PROVIDERS: dict[str, tuple[str, str]] = {
    "arm": ("arm-cmsis-nn", "NSX_EXECUTORCH_ARM_CMSIS_NN_ROOT"),
    "ns": ("ns-cmsis-nn", "NSX_EXECUTORCH_NS_CMSIS_NN_ROOT"),
}

EXECUTORCH_MODULE = "nsx-executorch"
EXECUTORCH_PROJECT = "nsx-executorch"


def _provider_cache_root() -> Path:
    """Return the NSX-compatible cache root for provider source checkouts."""
    if configured := os.environ.get("NSX_CACHE_DIR"):
        root = Path(configured).expanduser()
    elif configured := os.environ.get("XDG_CACHE_HOME"):
        root = Path(configured).expanduser() / "nsx"
    else:
        root = Path.home() / ".cache" / "nsx"
    return root.resolve() / "hpx-provider-sources"


def _checkout_commit(path: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise EngineError(
            f"Cannot verify git revision for ExecuTorch dependency checkout: {path}",
            hint="Use a git checkout at the exact HPX compatibility-baseline commit.",
        ) from exc
    return completed.stdout.strip()


def _gitlink_commit(path: Path, submodule: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "ls-tree", "HEAD", "--", submodule],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        fields = completed.stdout.split()
        if len(fields) >= 3 and fields[1] == "commit":
            return fields[2]
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise EngineError(
            f"Cannot verify nsx-executorch submodule pin for {submodule}",
            hint="Use the recursively initialized git checkout pinned by HPX.",
        ) from exc
    raise EngineError(
        f"nsx-executorch does not record the expected submodule: {submodule}",
        hint="Use the exact nsx-executorch commit pinned by HPX.",
    )


def _resolve_cmsis_nn_provider_root(config: ProfileConfig, project: str) -> Path:
    """Materialize the selected CMSIS-NN provider outside NSX's app bootstrap.

    Clones (or reuses a cached checkout of) the HPX compatibility baseline's
    pinned commit for *project* so nsx-executorch can add_subdirectory() it
    exactly once, itself, via its explicit root-override cache var.
    """
    configured = config.engine.config.get("cmsis_nn_path")
    if configured is not None:
        if not isinstance(configured, (str, Path)) or not str(configured).strip():
            raise EngineError(
                "engine.config.cmsis_nn_path must be a non-empty filesystem path"
            )
        dest = Path(configured).expanduser().resolve()
        if not (dest / "CMakeLists.txt").is_file():
            raise EngineError(f"CMSIS-NN provider checkout at {dest} is missing CMakeLists.txt")
        return dest

    baseline_project = config.compatibility_baseline.project(project)
    dest = _provider_cache_root() / f"{project}-{baseline_project.ref}"
    if not (dest / "CMakeLists.txt").is_file():
        _clone_provider_at_ref(baseline_project.url, baseline_project.ref, dest)
    if not (dest / "CMakeLists.txt").is_file():
        raise EngineError(
            f"CMSIS-NN provider checkout at {dest} is missing CMakeLists.txt",
            hint=(f"Delete {dest} and retry, or verify network access to {baseline_project.url}."),
        )
    actual_ref = _checkout_commit(dest)
    if actual_ref != baseline_project.ref:
        raise EngineError(
            f"Cached CMSIS-NN provider at {dest} is at {actual_ref}, "
            f"expected {baseline_project.ref}",
            hint=f"Remove {dest} and retry so HPX can materialize the qualified commit.",
        )
    return dest


def _clone_provider_at_ref(url: str, ref: str, dest: Path) -> None:
    log.info("ExecuTorch: materializing CMSIS-NN provider %s@%s -> %s", url, ref, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{dest.name}.", dir=dest.parent))
    try:
        subprocess.run(
            ["git", "clone", "--quiet", "--no-checkout", url, str(temporary)],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        subprocess.run(
            ["git", "-C", str(temporary), "checkout", "--quiet", "--detach", ref],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        subprocess.run(
            ["git", "-C", str(temporary), "submodule", "update", "--init", "--recursive"],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        try:
            temporary.replace(dest)
        except OSError:
            try:
                concurrently_published = (dest / "CMakeLists.txt").is_file() and _checkout_commit(
                    dest
                ) == ref
            except EngineError:
                concurrently_published = False
            if not concurrently_published:
                raise
            shutil.rmtree(temporary)
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise EngineError(
            f"Failed to materialize CMSIS-NN provider from {url}@{ref}",
            hint=(
                "Clone manually and retry:\n"
                f"  git clone {url} {dest}\n"
                f"  git -C {dest} checkout {ref}\n"
                f"  git -C {dest} submodule update --init --recursive"
            ),
        ) from exc


def _positive_int(config: dict[str, Any], name: str, default: int | None = None) -> int:
    value = config.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise EngineError(
            f"engine.config.{name} must be a positive integer",
            hint="ExecuTorch PTE tensor and arena sizes are explicit target-build inputs.",
        )
    return value


def _operator_list(config: dict[str, Any], name: str) -> str:
    value = config.get(name, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise EngineError(f"engine.config.{name} must be a list of operator names")
    return ",".join(value)


class ExecuTorchAdapter:
    """Prepare the local nsx-executorch module and explicit PTE contract."""

    @property
    def name(self) -> str:
        return "ExecuTorch"

    @property
    def engine_type(self) -> EngineType:
        return EngineType.EXECUTORCH

    def default_auto_placement(
        self, *, tcm_cap: int, sram_cap: int
    ) -> tuple[Placement, Placement] | None:
        del tcm_cap, sram_cap
        return None

    def apply_arena_placement_override(
        self, regions: list[ArenaRegion], target: Placement
    ) -> list[ArenaRegion]:
        del target
        return regions

    def prepare(self, config: ProfileConfig, work_dir: Path) -> EngineArtifacts:
        engine_config = config.engine.config
        source_value = engine_config.get("source_path")
        if not isinstance(source_value, (str, Path)):
            raise EngineError(
                "ExecuTorch requires engine.config.source_path",
                hint="Point it at the local nsx-executorch repository root.",
            )
        source_root = Path(source_value).expanduser().resolve()
        # nsx-executorch is a real NSX module at its checkout root — its own
        # nsx-module.yaml + CMakeLists.txt (not a retired nsx/ subdirectory).
        if (
            not (source_root / "version.txt").is_file()
            or not (source_root / "nsx-module.yaml").is_file()
            or not (source_root / "CMakeLists.txt").is_file()
        ):
            raise EngineError(
                f"Invalid nsx-executorch source_path: {source_root}",
                hint="Expected version.txt, nsx-module.yaml, and CMakeLists.txt at the checkout root.",
            )
        expected_engine = config.compatibility_baseline.engine("executorch")
        actual_version = (source_root / "version.txt").read_text(encoding="utf-8").strip()
        if actual_version != expected_engine.version:
            raise EngineError(
                f"nsx-executorch version {actual_version!r} does not match the qualified "
                f"version {expected_engine.version!r}",
                hint="Check out the exact nsx-executorch commit pinned by HPX.",
            )
        actual_ref = _checkout_commit(source_root)
        if actual_ref != expected_engine.ref:
            raise EngineError(
                f"nsx-executorch checkout is at {actual_ref}, expected {expected_engine.ref}",
                hint="Fetch PR #1 and check out the exact commit pinned by HPX.",
            )
        if not (source_root / "external" / "executorch" / "version.txt").is_file():
            raise EngineError(
                f"nsx-executorch checkout is missing its ExecuTorch submodule: {source_root}",
                hint=(
                    "Initialize external/executorch and the minimal Cortex-M submodules "
                    "listed in nsx-executorch's README."
                ),
            )
        expected_submodule_ref = _gitlink_commit(source_root, "external/executorch")
        actual_submodule_ref = _checkout_commit(source_root / "external" / "executorch")
        if actual_submodule_ref != expected_submodule_ref:
            raise EngineError(
                f"nsx-executorch external/executorch is at {actual_submodule_ref}, "
                f"expected {expected_submodule_ref}",
                hint="Run git submodule update --init external/executorch at the pinned checkout.",
            )
        if not (source_root / "tools" / "python" / "torchgen" / "__init__.py").is_file():
            raise EngineError(
                f"nsx-executorch checkout is missing its pinned torchgen sources: {source_root}"
            )

        provider = config.engine.backend or "arm"
        if provider not in _CMSIS_NN_PROVIDERS:
            raise EngineError("ExecuTorch backend must be 'arm' or 'ns'")
        configured_provider = engine_config.get("cmsis_nn_path")
        if provider == "ns" and (
            not isinstance(configured_provider, (str, Path))
            or not str(configured_provider).strip()
        ):
            raise EngineError(
                "ExecuTorch backend 'ns' requires engine.config.cmsis_nn_path",
                hint=(
                    "Provide an ns-cmsis-nn checkout that implements the stock CMSIS-NN "
                    "API used by nsx-executorch PR #1. The qualified ns-cmsis-nn v7.29.2 "
                    "pin has an incompatible weight_sum_ctx ABI and is not selected "
                    "silently."
                ),
            )

        planned_size = _positive_int(engine_config, "planned_arena_size", config.model.arena_size)
        method_size = _positive_int(engine_config, "method_arena_size", 64 * 1024)
        temporary_size = _positive_int(engine_config, "temporary_arena_size", 32 * 1024)
        input_size = _positive_int(engine_config, "input_size")
        output_size = _positive_int(engine_config, "output_size")

        # Generate a thin NSX module wrapper around the local checkout: mirror
        # its own nsx-module.yaml and delegate build logic to its own
        # CMakeLists.txt via add_subdirectory() (not include()) — the
        # runtime's own CMakeLists.txt references its sources with paths
        # relative to CMAKE_CURRENT_SOURCE_DIR (e.g. `src/nsx_executorch.cpp`
        # in add_library()), which only resolves correctly when CMake
        # actually descends into source_root as its own directory scope.
        # include() would leave CMAKE_CURRENT_SOURCE_DIR pointed at this
        # wrapper directory instead, breaking every relative source path.
        wrapper = work_dir / "engine" / "nsx-executorch"
        wrapper.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / "nsx-module.yaml", wrapper / "nsx-module.yaml")
        (wrapper / "CMakeLists.txt").write_text(
            "# Auto-generated by hpx ExecuTorchAdapter.\n"
            "# Do not edit — regenerated on every hpx run.\n"
            "cmake_minimum_required(VERSION 3.24)\n"
            "if(TARGET nsx::executorch)\n  return()\nendif()\n"
            "add_subdirectory(\n"
            f'  "{source_root.as_posix()}"\n'
            '  "${CMAKE_CURRENT_BINARY_DIR}/nsx-executorch-root")\n',
            encoding="utf-8",
        )

        cmsis_project, cmsis_override_var = _CMSIS_NN_PROVIDERS[provider]
        cmsis_root = _resolve_cmsis_nn_provider_root(config, cmsis_project)
        return EngineArtifacts(
            engine_type=EngineType.EXECUTORCH,
            extra_modules=[
                # Only nsx-executorch is declared as a normal NSX app module.
                # The selected CMSIS-NN provider is materialized separately
                # (see _resolve_cmsis_nn_provider_root) and handed to
                # nsx-executorch via its own root-override cache var below —
                # never both providers, and never through NSX's generic
                # per-module bootstrap (see _CMSIS_NN_PROVIDERS docstring).
                NsxModuleRef(
                    name=EXECUTORCH_MODULE,
                    path=wrapper,
                    local=True,
                    project=EXECUTORCH_PROJECT,
                ),
            ],
            cmake_vars={
                "NSX_EXECUTORCH_ENABLE_PROFILING": "ON",
                "NSX_EXECUTORCH_CMSIS_NN_PROVIDER": provider,
                cmsis_override_var: cmsis_root.as_posix(),
                "NSX_EXECUTORCH_PORTABLE_SELECT_OPS_LIST": _operator_list(
                    engine_config, "portable_ops"
                ),
                # nsx-executorch wraps this interpreter with its pinned
                # torchgen sources. Point CMake discovery at HPX's own Python
                # environment, which is Python 3.11+ and already includes
                # PyYAML, rather than whichever `python3` happens to be first.
                # Do not resolve this symlink: uv-managed environments point
                # `.venv/bin/python` at a base interpreter whose standalone
                # site-packages do not contain HPX's PyYAML dependency.
                "Python3_EXECUTABLE": Path(sys.executable).absolute().as_posix(),
                # ExecuTorch's own CMakeLists.txt documents this exact
                # scenario: a "standalone consumer" (like this HPX-generated
                # app) adds ExecuTorch as a subproject but cannot satisfy its
                # install(EXPORT ExecuTorchTargets ...) rules — those pull in
                # NSX board-flags targets (e.g. nsx_board_<board>_flags via
                # cmsis-nn's PUBLIC link) that NSX's own board.cmake stages
                # under an "nsxTargets" export set HPX-generated apps never
                # finalize with a matching install(EXPORT nsxTargets ...).
                # Skip ExecuTorch's install() rules entirely; HPX profiles
                # firmware directly off the build tree and never installs it.
                "EXECUTORCH_BAREMETAL_SKIP_INSTALL": "ON",
            },
            engine_header="nsx_executorch.h",
            executorch_method_arena_size=method_size,
            executorch_planned_arena_size=planned_size,
            executorch_temporary_arena_size=temporary_size,
            executorch_input_size=input_size,
            executorch_output_size=output_size,
        )
