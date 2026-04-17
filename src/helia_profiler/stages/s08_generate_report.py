"""Stage 8 — Generate report: CSV/JSON output + Model Explorer overlays."""

from __future__ import annotations

import logging

from ..errors import ReportError
from ..pipeline import PipelineContext

log = logging.getLogger("hpx")


class GenerateReportStage:
    @property
    def name(self) -> str:
        return "generate_report"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        if ctx.pmu_raw is None:
            raise ReportError("No PMU data available — capture stage did not run.")

        from ..report import write_report

        try:
            paths = write_report(ctx)
        except ReportError:
            raise
        except Exception as exc:
            raise ReportError(
                f"Report generation failed: {exc}",
                hint="Check that the output directory is writable.",
            ) from exc

        ctx.report_paths = paths
        for p in paths:
            log.info("Report: %s", p)
