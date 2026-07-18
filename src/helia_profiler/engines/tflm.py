"""Stock TFLM engine adapter."""

from __future__ import annotations

from pathlib import Path

from ..config import ProfileConfig
from ..errors import EngineError
from ..placement import Placement
from ..results import NsxModuleRef
from . import EngineType, TFLM_ENGINE_HEADER
from .base import ArenaRegion, EngineArtifacts


TFLITE_MICRO_MODULE = "nsx-tflite-micro"
TFLITE_MICRO_PROJECT = "nsx-tflite-micro"
ARM_CMSIS_NN_MODULE = "arm-cmsis-nn"
ARM_CMSIS_NN_PROJECT = "arm-cmsis-nn"
_SUPPORTED_BACKENDS = frozenset(("reference", "cmsis_nn"))


class TFLMAdapter:
    """Adapter for stock TensorFlow Lite for Microcontrollers."""

    @property
    def name(self) -> str:
        return "Stock TFLM"

    @property
    def engine_type(self) -> EngineType:
        return EngineType.TFLM

    def default_auto_placement(
        self, *, tcm_cap: int, sram_cap: int
    ) -> tuple[Placement, Placement] | None:
        # Fall through to the shared greedy fastest-fit policy.
        del tcm_cap, sram_cap
        return None

    def apply_arena_placement_override(
        self, regions: list[ArenaRegion], target: Placement
    ) -> list[ArenaRegion]:
        # TFLM owns a single arena managed by the firmware template;
        # no engine-side override needed.
        del target
        return regions

    def prepare(self, config: ProfileConfig, work_dir: Path) -> EngineArtifacts:
        """Resolve the reference or upstream-CMSIS-NN TFLM NSX modules."""
        del work_dir

        backend = config.engine.backend or "reference"
        if backend not in _SUPPORTED_BACKENDS:
            raise EngineError(
                f"Invalid TFLM backend '{backend}'. "
                f"Valid backends: {', '.join(sorted(_SUPPORTED_BACKENDS))}."
            )

        extra_modules: list[NsxModuleRef] = []
        if backend == "cmsis_nn":
            # This must precede nsx-tflite-micro in NSX_MODULES: the runtime
            # validates nsx::arm_cmsis_nn while its CMakeLists is processed.
            extra_modules.append(
                NsxModuleRef(
                    name=ARM_CMSIS_NN_MODULE,
                    path=Path(),
                    local=False,
                    project=ARM_CMSIS_NN_PROJECT,
                )
            )
        extra_modules.append(
            NsxModuleRef(
                name=TFLITE_MICRO_MODULE,
                path=Path(),
                local=False,
                project=TFLITE_MICRO_PROJECT,
            )
        )

        return EngineArtifacts(
            engine_type=EngineType.TFLM,
            extra_modules=extra_modules,
            cmake_vars={"NSX_TFLITE_MICRO_BACKEND": backend},
            engine_header=TFLM_ENGINE_HEADER,
        )
