"""ExecuTorch engine adapter for NSX target profiling."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import ProfileConfig
from ..errors import EngineError
from ..placement import Placement
from ..results import NsxModuleRef
from . import EngineType
from .base import ArenaRegion, EngineArtifacts


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
        source_module = source_root / "nsx"
        if not (source_root / "version.txt").is_file() or not (
            source_module / "nsx-module.yaml"
        ).is_file():
            raise EngineError(
                f"Invalid nsx-executorch source_path: {source_root}",
                hint="Expected version.txt and nsx/nsx-module.yaml.",
            )

        provider = config.engine.backend or "arm"
        if provider not in {"arm", "ns"}:
            raise EngineError("ExecuTorch backend must be 'arm' or 'ns'")

        planned_size = _positive_int(
            engine_config, "planned_arena_size", config.model.arena_size
        )
        method_size = _positive_int(engine_config, "method_arena_size", 64 * 1024)
        temporary_size = _positive_int(engine_config, "temporary_arena_size", 32 * 1024)
        input_size = _positive_int(engine_config, "input_size")
        output_size = _positive_int(engine_config, "output_size")

        # ExecuTorch currently requires its source directory basename to be
        # exactly "executorch". Keep the user's checkout untouched and expose
        # it through a work-dir alias with the required basename.
        source_alias = work_dir / "engine" / "executorch"
        source_alias.parent.mkdir(parents=True, exist_ok=True)
        if not source_alias.exists():
            try:
                source_alias.symlink_to(source_root, target_is_directory=True)
            except OSError as exc:
                raise EngineError(
                    f"Cannot create ExecuTorch source alias at {source_alias}: {exc}",
                    hint=(
                        "Use a checkout directory named 'executorch', or allow directory "
                        "symlinks in the HPX work directory."
                    ),
                ) from exc

        wrapper = work_dir / "engine" / "nsx-executorch"
        wrapper.mkdir(parents=True, exist_ok=True)
        (wrapper / "nsx-module.yaml").write_text(
            """schema_version: 1
module:
  name: nsx-executorch
  type: runtime
  version: 1.3.0
support:
  ambiqsuite: true
  zephyr: false
depends:
  required:
    - arm-cmsis-nn
    - nsx-cmsis-nn
    - nsx-cmsis-core
    - nsx-core
    - nsx-soc-hal
  optional: []
build:
  cmake:
    package: nsx_executorch
    targets:
      - nsx::executorch
""",
            encoding="utf-8",
        )
        source_literal = json.dumps((source_alias / "nsx").as_posix())
        (wrapper / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.24)\n"
            "if(TARGET nsx::executorch)\n  return()\nendif()\n"
            f"add_subdirectory({source_literal} "
            '"${CMAKE_CURRENT_BINARY_DIR}/nsx-executorch-source")\n',
            encoding="utf-8",
        )

        return EngineArtifacts(
            engine_type=EngineType.EXECUTORCH,
            extra_modules=[
                NsxModuleRef(
                    name="arm-cmsis-nn",
                    path=Path(),
                    local=False,
                    project="arm-cmsis-nn",
                ),
                NsxModuleRef(
                    name="nsx-cmsis-nn",
                    path=Path(),
                    local=False,
                    project="ns-cmsis-nn",
                ),
                NsxModuleRef(
                    name="nsx-executorch",
                    path=wrapper,
                    local=True,
                    project="nsx-executorch",
                ),
            ],
            cmake_vars={
                "NSX_EXECUTORCH_ENABLE_PROFILING": "ON",
                "NSX_EXECUTORCH_CMSIS_NN_PROVIDER": provider,
                "NSX_EXECUTORCH_CORTEX_M_SELECT_OPS_LIST": _operator_list(
                    engine_config, "cortex_m_ops"
                ),
                "NSX_EXECUTORCH_PORTABLE_SELECT_OPS_LIST": _operator_list(
                    engine_config, "portable_ops"
                ),
            },
            engine_header="nsx_executorch.h",
            executorch_method_arena_size=method_size,
            executorch_planned_arena_size=planned_size,
            executorch_temporary_arena_size=temporary_size,
            executorch_input_size=input_size,
            executorch_output_size=output_size,
        )
