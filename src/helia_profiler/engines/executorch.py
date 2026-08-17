"""ExecuTorch engine adapter for NSX target profiling."""

from __future__ import annotations

import logging
import shutil
import subprocess
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

# Persistent, ref-keyed cache so repeat hpx runs don't re-clone a provider
# that is already materialized at the pinned baseline commit.
_CMSIS_NN_CACHE_ROOT = Path.home() / ".cache" / "helia-profiler" / "executorch-cmsis-nn"


def _resolve_cmsis_nn_provider_root(config: ProfileConfig, project: str) -> Path:
    """Materialize the selected CMSIS-NN provider outside NSX's app bootstrap.

    Clones (or reuses a cached checkout of) the HPX compatibility baseline's
    pinned commit for *project* so nsx-executorch can add_subdirectory() it
    exactly once, itself, via its explicit root-override cache var.
    """
    baseline_project = config.compatibility_baseline.project(project)
    dest = _CMSIS_NN_CACHE_ROOT / f"{project}-{baseline_project.ref}"
    if not (dest / "CMakeLists.txt").is_file():
        _clone_provider_at_ref(baseline_project.url, baseline_project.ref, dest)
    if not (dest / "CMakeLists.txt").is_file():
        raise EngineError(
            f"CMSIS-NN provider checkout at {dest} is missing CMakeLists.txt",
            hint=(
                f"Delete {dest} and retry, or verify network access to "
                f"{baseline_project.url}."
            ),
        )
    return dest


def _clone_provider_at_ref(url: str, ref: str, dest: Path) -> None:
    log.info("ExecuTorch: materializing CMSIS-NN provider %s@%s -> %s", url, ref, dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "clone", "--quiet", url, str(dest)],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        subprocess.run(
            ["git", "-C", str(dest), "checkout", "--quiet", ref],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        subprocess.run(
            ["git", "-C", str(dest), "submodule", "update", "--init", "--recursive"],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        if dest.exists():
            shutil.rmtree(dest)
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
    separator = ";" if name == "cortex_m_ops" else ","
    return separator.join(value)


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

        provider = config.engine.backend or "arm"
        if provider not in _CMSIS_NN_PROVIDERS:
            raise EngineError("ExecuTorch backend must be 'arm' or 'ns'")

        planned_size = _positive_int(
            engine_config, "planned_arena_size", config.model.arena_size
        )
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
                "NSX_EXECUTORCH_CORTEX_M_SELECT_OPS_LIST": _operator_list(
                    engine_config, "cortex_m_ops"
                ),
                "NSX_EXECUTORCH_PORTABLE_SELECT_OPS_LIST": _operator_list(
                    engine_config, "portable_ops"
                ),
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
