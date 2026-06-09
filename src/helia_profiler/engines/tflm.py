"""Stock TFLM engine adapter."""

from __future__ import annotations

from pathlib import Path

from ..config import ProfileConfig
from ..placement import Placement
from . import EngineType, TFLM_ENGINE_HEADER
from .base import ArenaRegion, EngineArtifacts


class TFLMAdapter:
    """Adapter for stock TensorFlow Lite for Microcontrollers with CMSIS-NN."""

    @property
    def name(self) -> str:
        return "Stock TFLM (CMSIS-NN)"

    @property
    def engine_type(self) -> EngineType:
        return EngineType.TFLM

    def supports_runtime_split(self) -> bool:
        return True

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
        """TFLM uses pre-built static libraries resolved as NSX modules.

        No extra preparation step needed beyond standard module resolution.
        """
        return EngineArtifacts(
            engine_type=EngineType.TFLM,
            engine_header=TFLM_ENGINE_HEADER,
        )
