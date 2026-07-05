"""Stage 2 — Prepare engine: instantiate adapter and produce artifacts."""

from __future__ import annotations

import logging

from ..engines import get_adapter
from ..errors import EngineError
from ..pipeline import PipelineContext

log = logging.getLogger("hpx")


class PrepareEngineStage:
    @property
    def name(self) -> str:
        return "prepare_engine"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        engine_type = ctx.config.engine.type

        try:
            adapter = get_adapter(engine_type)
        except EngineError:
            raise
        except Exception as exc:
            raise EngineError(
                f"Failed to instantiate engine adapter for '{engine_type.value}': {exc}",
            ) from exc

        log.info("Engine: %s", adapter.name)

        try:
            artifacts = adapter.prepare(ctx.config, ctx.work_dir)
        except EngineError:
            raise
        except Exception as exc:
            raise EngineError(
                f"Engine '{adapter.name}' preparation failed: {exc}",
                hint="Check engine-specific config and that required tools are installed "
                "(run 'hpx doctor').",
            ) from exc

        ctx.engine_adapter = adapter
        ctx.engine_artifacts = artifacts
