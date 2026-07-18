"""Top-level profiling orchestrator.

Composes the pipeline stages and delegates to ``PipelineRunner``.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from collections.abc import Iterator

from .config import ProfileConfig
from .console import HpxConsole
from .pipeline import PipelineContext, PipelineRunner, ProgressSink
from .stages import (
    AnalyzeModelStage,
    BuildFirmwareStage,
    BuildPowerFirmwareStage,
    CapturePmuStage,
    CapturePowerStage,
    CollectPowerTerminalStage,
    EnsureBoardPoweredStage,
    FlashFirmwareStage,
    FlashPowerFirmwareStage,
    GenerateFirmwareStage,
    GenerateReportStage,
    PlanMemoryStage,
    PlanPowerRunStage,
    PreflightStage,
    PrepareEngineStage,
    ResolveJLinkProbeStage,
    ResolvePlatformStage,
    VerifyPlacementStage,
)

log = logging.getLogger("hpx")


def build_default_pipeline(
    console: HpxConsole | None = None,
    *,
    progress_sink: ProgressSink | None = None,
) -> PipelineRunner:
    """Create the standard profiling pipeline with all stages."""
    return PipelineRunner(
        [
            PreflightStage(),
            EnsureBoardPoweredStage(),
            ResolvePlatformStage(),
            ResolveJLinkProbeStage(),
            PrepareEngineStage(),
            AnalyzeModelStage(),
            PlanMemoryStage(),
            GenerateFirmwareStage(),
            BuildFirmwareStage(),
            VerifyPlacementStage(),
            FlashFirmwareStage(),
            CapturePmuStage(),
            PlanPowerRunStage(),
            BuildPowerFirmwareStage(),
            FlashPowerFirmwareStage(),
            CapturePowerStage(),
            CollectPowerTerminalStage(),
            GenerateReportStage(),
        ],
        console=console,
        progress_sink=progress_sink,
    )


def run_profile(
    config: ProfileConfig,
    *,
    console: HpxConsole | None = None,
    progress_sink: ProgressSink | None = None,
) -> PipelineContext:
    """Execute the full profiling pipeline.

    Returns the final ``PipelineContext`` with all captured data and report
    paths.  Raises ``HpxError`` (or a subclass) on failure — errors are never
    swallowed silently.
    """
    if console is None:
        return build_default_pipeline(progress_sink=progress_sink).run(config)

    with _cli_logging(config.verbose):
        console.print_banner()
        pipeline = build_default_pipeline(console=console, progress_sink=progress_sink)
        ctx = pipeline.run(config)
        if ctx.pmu_result is not None:
            console.print_results(ctx)
        return ctx


@contextmanager
def _cli_logging(verbosity: int) -> Iterator[None]:
    """Temporarily configure the ``hpx`` logger for one CLI-owned run."""
    from rich.logging import RichHandler

    from .console import _status_console

    level = logging.WARNING
    if verbosity >= 2:
        level = logging.DEBUG
    elif verbosity >= 1:
        level = logging.INFO

    logger = logging.getLogger("hpx")
    previous_handlers = list(logger.handlers)
    previous_level = logger.level
    if not previous_handlers:
        handler = RichHandler(
            console=_status_console,
            show_time=False,
            show_path=False,
            markup=False,
            keywords=[],
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level)
    try:
        yield
    finally:
        logger.handlers[:] = previous_handlers
        logger.setLevel(previous_level)
