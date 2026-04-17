"""Stage 2 — Prepare engine: instantiate adapter and produce artifacts."""

from __future__ import annotations

import logging

from ..engines import EngineType
from ..errors import EngineError
from ..pipeline import PipelineContext

log = logging.getLogger("hpx")


def _get_adapter(engine_type: EngineType):
    """Import and instantiate the engine adapter for the given type."""
    if engine_type is EngineType.TFLM:
        from ..engines.tflm import TFLMAdapter

        return TFLMAdapter()
    elif engine_type is EngineType.HELIA_RT:
        from ..engines.helia_rt import HeliaRTAdapter

        return HeliaRTAdapter()
    elif engine_type is EngineType.HELIA_AOT:
        from ..engines.helia_aot import HeliaAOTAdapter

        return HeliaAOTAdapter()
    else:
        raise EngineError(f"Unknown engine type: {engine_type}")


class PrepareEngineStage:
    @property
    def name(self) -> str:
        return "prepare_engine"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        engine_type = ctx.config.engine.type

        try:
            adapter = _get_adapter(engine_type)
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
