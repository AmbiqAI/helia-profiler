"""heliaRT engine adapter."""

from __future__ import annotations

from pathlib import Path

from ..config import ProfileConfig
from .base import EngineArtifacts


class HeliaRTAdapter:
    """Adapter for heliaRT — Ambiq's optimized TFLM fork.

    Until heliaRT ships a proper nsx-module.yaml, this adapter generates a
    local wrapper module that integrates the heliaRT static library into the
    NSX build system.
    """

    @property
    def name(self) -> str:
        return "heliaRT"

    def prepare(self, config: ProfileConfig, work_dir: Path) -> EngineArtifacts:
        backend = config.engine.backend or "helia"

        # TODO: Generate local NSX wrapper module for heliaRT static lib.
        # For now, return the template vars the firmware needs.
        return EngineArtifacts(
            template_vars={
                "engine_type": "helia_rt",
                "engine_backend": backend,
                "engine_header": "tensorflow/lite/micro/micro_interpreter.h",
            },
        )
