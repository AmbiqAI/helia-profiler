"""Top-level profiling orchestrator.

Composes the pipeline stages and delegates to ``PipelineRunner``.
"""

from __future__ import annotations

import logging

from .config import ProfileConfig
from .console import HpxConsole
from .pipeline import PipelineContext, PipelineRunner
from .stages import (
    AnalyzeModelStage,
    BuildFirmwareStage,
    CapturePmuStage,
    CapturePowerStage,
    FlashFirmwareStage,
    GenerateFirmwareStage,
    GenerateReportStage,
    PlanMemoryStage,
    PreflightStage,
    PrepareEngineStage,
    ResolvePlatformStage,
)

log = logging.getLogger("hpx")


def build_default_pipeline(console: HpxConsole | None = None) -> PipelineRunner:
    """Create the standard profiling pipeline with all stages."""
    return PipelineRunner(
        [
            PreflightStage(),
            ResolvePlatformStage(),
            PrepareEngineStage(),
            AnalyzeModelStage(),
            PlanMemoryStage(),
            GenerateFirmwareStage(),
            BuildFirmwareStage(),
            FlashFirmwareStage(),
            CapturePmuStage(),
            CapturePowerStage(),
            GenerateReportStage(),
        ],
        console=console,
    )


def run_profile(config: ProfileConfig) -> PipelineContext:
    """Execute the full profiling pipeline.

    Returns the final ``PipelineContext`` with all captured data and report
    paths.  Raises ``HpxError`` (or a subclass) on failure — errors are never
    swallowed silently.
    """
    _setup_logging(config.verbose)
    console = HpxConsole(config.verbose)
    console.print_banner()
    pipeline = build_default_pipeline(console=console)
    ctx = pipeline.run(config)

    # Print the rich results summary.
    if ctx.pmu_result is not None:
        console.print_results(ctx)

    return ctx


def _setup_logging(verbosity: int) -> None:
    """Configure the ``hpx`` logger based on CLI verbosity."""
    level = logging.WARNING
    if verbosity >= 2:
        level = logging.DEBUG
    elif verbosity >= 1:
        level = logging.INFO

    logger = logging.getLogger("hpx")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[dim]\[hpx][/dim] %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level)
