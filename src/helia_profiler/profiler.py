"""Top-level profiling orchestrator.

Composes the pipeline stages and delegates to ``PipelineRunner``.
"""

from __future__ import annotations

from .config import ProfileConfig
from .pipeline import PipelineContext, PipelineObserver, PipelineRunner, ProgressSink, Stage
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
        build_stages() + capture_stages(), observer=observer, progress_sink=progress_sink
    )


def build_stages(*, model_sha256: str | None = None) -> list[Stage]:
    return [
        PreflightStage(),
        EnsureBoardPoweredStage(),
        ResolvePlatformStage(model_sha256=model_sha256),
        ResolveJLinkProbeStage(),
        PrepareEngineStage(),
        AnalyzeModelStage(),
        PlanMemoryStage(),
        GenerateFirmwareStage(),
        BuildFirmwareStage(),
        VerifyPlacementStage(),
    ]


def capture_stages() -> list[Stage]:
    return [
        FlashFirmwareStage(),
        CapturePmuStage(),
        PlanPowerRunStage(),
        BuildPowerFirmwareStage(),
        FlashPowerFirmwareStage(),
        CapturePowerStage(),
        CollectPowerTerminalStage(),
        GenerateReportStage(),
    ]


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
