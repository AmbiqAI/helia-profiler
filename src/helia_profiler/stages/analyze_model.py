"""Stage 2.5 — Analyze model: extract per-layer OPS/MACs from the tflite flatbuffer.

This stage is **optional** — it silently skips if ``ai-edge-litert`` is not
installed.  Results are stored in ``ctx.model_analysis`` and merged into the
report by the generate_report stage.
"""

from __future__ import annotations

import logging

from ..evaluation import analyze_model, is_available
from ..pipeline import PipelineContext

log = logging.getLogger("hpx")


class AnalyzeModelStage:
    @property
    def name(self) -> str:
        return "analyze_model"

    def should_skip(self, ctx: PipelineContext) -> bool:
        if not is_available():
            log.debug("ai-edge-litert not installed — skipping model analysis")
            return True
        return False

    def run(self, ctx: PipelineContext) -> None:
        result = analyze_model(ctx.config.model.path)
        if result is None:
            log.warning("Model analysis returned no results")
            return

        ctx.model_analysis = result
        log.info(
            "Model analysis: %d layers, %s total MACs, %s total OPS, %d params",
            len(result.layers),
            f"{result.total_macs:,}",
            f"{result.total_ops:,}",
            result.num_parameters,
        )
