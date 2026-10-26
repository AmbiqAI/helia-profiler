"""Top-level profiling orchestrator.

Composes the pipeline stages and delegates to ``PipelineRunner``.
"""

from __future__ import annotations

from .config import ProfileConfig
from .pipeline import PipelineContext, PipelineObserver, PipelineRunner, ProgressSink
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


def build_default_pipeline(
    observer: PipelineObserver | None = None,
    *,
    progress_sink: ProgressSink | None = None,
) -> PipelineRunner:
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
        observer=observer,
        progress_sink=progress_sink,
    )


def run_profile(
    config: ProfileConfig,
    *,
    observer: PipelineObserver | None = None,
    progress_sink: ProgressSink | None = None,
) -> PipelineContext:
    """Return the final ``PipelineContext`` with all captured data and report
    paths. Raises ``HpxError`` (or a subclass) on failure — errors are never
    swallowed silently.
    """
    pipeline = build_default_pipeline(observer=observer, progress_sink=progress_sink)
    ctx = pipeline.run(config)
    if observer is not None:
        observer.profile_done(ctx)
    return ctx
