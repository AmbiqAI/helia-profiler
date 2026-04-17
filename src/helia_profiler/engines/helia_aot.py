"""heliaAOT engine adapter."""

from __future__ import annotations

from pathlib import Path

from ..config import ProfileConfig
from .base import EngineArtifacts


class HeliaAOTAdapter:
    """Adapter for heliaAOT — Ambiq's ahead-of-time model compiler.

    Runs the heliaAOT compiler as a subprocess to produce a C module for
    the target model. The generated code is wrapped as an NSX-compatible
    local module for the profiler firmware build.
    """

    @property
    def name(self) -> str:
        return "heliaAOT"

    def prepare(self, config: ProfileConfig, work_dir: Path) -> EngineArtifacts:
        # TODO: Invoke heliaAOT compiler subprocess.
        # - Read engine-specific config from config.engine.config_path
        # - Generate C module into work_dir / "aot_module"
        # - Return paths so firmware templates can reference the output

        return EngineArtifacts(
            template_vars={
                "engine_type": "helia_aot",
                # AOT generates its own inference function — no interpreter header
                "engine_header": None,
            },
        )
