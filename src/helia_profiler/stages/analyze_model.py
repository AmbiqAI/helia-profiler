"""Stage 2.5 — Analyze model: extract per-layer OPS/MACs from the tflite flatbuffer.

This stage is **optional** — it silently skips if ``ai-edge-litert`` is not
installed.  Results are stored in ``ctx.model_analysis`` and merged into the
report by the generate_report stage.
"""

from __future__ import annotations

import logging

from ..errors import ConfigError
from ..evaluation import analyze_model, is_available
from ..pipeline import PipelineContext

log = logging.getLogger("hpx")


def _check_ethos_u_consistency(cfg, analysis) -> None:
    """Fail fast on Vela-model / ethos_u-backend mismatches.

    Only possible when model analysis ran (ai-edge-litert installed);
    otherwise the mismatch surfaces at runtime as an unresolved custom op.
    """
    wants_npu = cfg.engine.backend == "ethos_u"
    if analysis.has_ethos_u_op and not wants_npu:
        raise ConfigError(
            f"Model contains {analysis.ethos_u_op_count} Vela-compiled "
            "ethos-u op(s), but the selected engine cannot dispatch them "
            f"(engine={cfg.engine.type}, backend={cfg.engine.backend or 'default'}).",
            hint=(
                "Use engine.backend=ethos_u (engine.type helia-rt or "
                "helia-aot) on an NPU-capable board, or profile the "
                "original (pre-Vela) .tflite instead."
            ),
        )
    if wants_npu and not analysis.has_ethos_u_op:
        raise ConfigError(
            "engine.backend=ethos_u selected, but the model has no ethos-u "
            "custom op — it was not compiled by Vela, so nothing would run "
            "on the NPU.",
            hint=(
                "Compile it first: pip install ethos-u-vela && "
                "vela model.tflite --accelerator-config ethos-u85-256 "
                "--output-dir vela_out, then profile the *_vela.tflite."
            ),
        )


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
        _check_ethos_u_consistency(ctx.config, result)
        log.info(
            "Model analysis: %d layers, %s total MACs, %s total OPS, %d params",
            len(result.layers),
            f"{result.total_macs:,}",
            f"{result.total_ops:,}",
            result.num_parameters,
        )
