"""Top-level profiling orchestrator.

Composes the pipeline stages and delegates to ``PipelineRunner``.
"""

from __future__ import annotations

import logging

from .config import ProfileConfig
from .pipeline import PipelineContext, PipelineRunner
from .stages import (
    BuildFirmwareStage,
    CapturePmuStage,
    CapturePowerStage,
    FlashFirmwareStage,
    GenerateFirmwareStage,
    GenerateReportStage,
    PrepareEngineStage,
    ResolvePlatformStage,
)

log = logging.getLogger("hpx")


def build_default_pipeline() -> PipelineRunner:
    """Create the standard profiling pipeline with all stages."""
    return PipelineRunner([
        ResolvePlatformStage(),
        PrepareEngineStage(),
        GenerateFirmwareStage(),
        BuildFirmwareStage(),
        FlashFirmwareStage(),
        CapturePmuStage(),
        CapturePowerStage(),
        GenerateReportStage(),
    ])


def run_profile(config: ProfileConfig) -> PipelineContext:
    """Execute the full profiling pipeline.

    Returns the final ``PipelineContext`` with all captured data and report
    paths.  Raises ``HpxError`` (or a subclass) on failure — errors are never
    swallowed silently.
    """
    _setup_logging(config.verbose)
    pipeline = build_default_pipeline()
    return pipeline.run(config)


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
        handler.setFormatter(logging.Formatter("[hpx] %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level)
