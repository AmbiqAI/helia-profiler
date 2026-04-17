"""Stock TFLM engine adapter."""

from __future__ import annotations

from pathlib import Path

from ..config import ProfileConfig
from .base import EngineArtifacts


class TFLMAdapter:
    """Adapter for stock TensorFlow Lite for Microcontrollers with CMSIS-NN."""

    @property
    def name(self) -> str:
        return "Stock TFLM (CMSIS-NN)"

    def prepare(self, config: ProfileConfig, work_dir: Path) -> EngineArtifacts:
        """TFLM uses pre-built static libraries resolved as NSX modules.

        No extra preparation step needed beyond standard module resolution.
        """
        return EngineArtifacts(
            template_vars={
                "engine_type": "tflm",
                "engine_header": "tensorflow/lite/micro/micro_interpreter.h",
            },
        )
