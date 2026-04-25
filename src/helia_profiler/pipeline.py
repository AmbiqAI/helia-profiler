"""Pipeline primitives — Stage protocol, PipelineContext, and PipelineRunner.

The profiling pipeline is a linear sequence of stages.  Each stage reads from
and writes to a shared ``PipelineContext``.  The runner iterates stages,
handles skip logic, wraps exceptions with stage context, and logs progress.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ._version import __version__
from .config import ProfileConfig
from .engines.base import EngineAdapter, EngineArtifacts
from .errors import HpxError
from .platform import BoardDef, SocDef
from .model_analysis import ModelAnalysis
from .power.base import PowerResult
from .results import MemoryPlan, PmuResult, RunMetadata, BinarySections

log = logging.getLogger("hpx")


# ---------------------------------------------------------------------------
# Pipeline context — mutable accumulator passed through every stage
# ---------------------------------------------------------------------------


@dataclass
class PipelineContext:
    """Mutable state bag that flows through all pipeline stages.

    Stages read their inputs and write their outputs here.  Fields start as
    ``None`` and are populated by the stage responsible for producing them.
    """

    config: ProfileConfig
    work_dir: Path

    # Platform resolution (stage: resolve_platform)
    soc: SocDef | None = None
    board: BoardDef | None = None

    # Engine preparation (stage: prepare_engine)
    engine_adapter: EngineAdapter | None = None
    engine_artifacts: EngineArtifacts | None = None

    # Firmware generation (stage: generate_firmware)
    firmware_dir: Path | None = None

    # Build (stage: build_firmware)
    build_dir: Path | None = None
    binary_path: Path | None = None
    binary_sections: BinarySections | None = None

    # Model analysis (stage: analyze_model — optional)
    model_analysis: ModelAnalysis | None = None

    # Memory plan (stage: plan_memory)
    memory_plan: MemoryPlan | None = None

    # Capture (stage: capture_pmu / capture_power)
    pmu_result: PmuResult | None = None
    power_result: PowerResult | None = None

    # Run metadata — enriched by stages, written to report
    run_metadata: RunMetadata = field(default_factory=RunMetadata)

    # Report (stage: generate_report)
    report_paths: list[Path] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Stage protocol — each pipeline step implements this
# ---------------------------------------------------------------------------


@runtime_checkable
class Stage(Protocol):
    """Interface for a single pipeline stage."""

    @property
    def name(self) -> str:
        """Short human-readable stage name shown in logs."""
        ...

    def should_skip(self, ctx: PipelineContext) -> bool:
        """Return True if this stage should be skipped for this run."""
        ...

    def run(self, ctx: PipelineContext) -> None:
        """Execute the stage, reading from and writing to *ctx*.

        Raise a specific ``HpxError`` subclass on failure.
        """
        ...


# ---------------------------------------------------------------------------
# Pipeline runner — lightweight sequential executor
# ---------------------------------------------------------------------------


class PipelineRunner:
    """Executes a sequence of ``Stage`` objects against a ``PipelineContext``."""

    def __init__(
        self,
        stages: list[Stage],
        console: Any | None = None,
    ) -> None:
        self._stages = list(stages)
        self._console = console  # Optional HpxConsole — avoids circular import

    def run(self, config: ProfileConfig) -> PipelineContext:
        """Set up the working directory, run all stages, and clean up."""
        work_dir, should_cleanup = _resolve_work_dir(config)
        ctx = PipelineContext(config=config, work_dir=work_dir)

        # Seed run metadata with immutable fields
        ctx.run_metadata.hpx_version = __version__
        ctx.run_metadata.run_id = str(uuid.uuid4())
        ctx.run_metadata.timestamp = datetime.now(timezone.utc).isoformat()
        ctx.run_metadata.config_snapshot = _serialize_config(config)

        try:
            for stage in self._stages:
                if stage.should_skip(ctx):
                    log.info("[skip] %s", stage.name)
                    if self._console is not None:
                        self._console.stage_skip(stage.name)
                    continue

                log.info("[start] %s", stage.name)
                if self._console is not None:
                    self._console.stage_start(stage.name)
                try:
                    stage.run(ctx)
                except HpxError:
                    if self._console is not None:
                        self._console._stop_spinner()
                    raise  # already typed — propagate as-is
                except Exception as exc:
                    if self._console is not None:
                        self._console._stop_spinner()
                    raise HpxError(
                        f"Unexpected error in stage '{stage.name}': {exc}",
                        hint="This is likely a bug in heliaPROFILER. "
                        "Please file an issue with the full traceback.",
                    ) from exc
                log.info("[done]  %s", stage.name)
                if self._console is not None:
                    self._console.stage_done(stage.name)

            # Signal end of pipeline — clears any live spinner.
            if self._console is not None:
                self._console.pipeline_done()

        finally:
            if should_cleanup:
                shutil.rmtree(work_dir, ignore_errors=True)

        return ctx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_work_dir(config: ProfileConfig) -> tuple[Path, bool]:
    """Return (work_dir, should_cleanup)."""
    if config.work_dir is not None:
        wd = config.work_dir.resolve()
        wd.mkdir(parents=True, exist_ok=True)
        return wd, False

    wd = Path(tempfile.mkdtemp(prefix="hpx_"))
    return wd, not config.keep_work_dir


def _serialize_config(config: ProfileConfig) -> dict[str, Any]:
    """Produce a JSON-safe snapshot of the active configuration."""
    return {
        "model": {
            "path": str(config.model.path),
            "arena_size": config.model.arena_size,
            "model_location": config.model.model_location,
        },
        "engine": {
            "type": config.engine.type.value,
            "backend": config.engine.backend,
            "config": config.engine.config,
        },
        "target": {
            "board": config.target.board,
            "toolchain": config.target.toolchain,
        },
        "profiling": {
            "pmu_presets": list(config.profiling.pmu_presets),
            "per_layer": config.profiling.per_layer,
            "iterations": config.profiling.iterations,
            "warmup": config.profiling.warmup,
        },
        "power": {
            "enabled": config.power.enabled,
            "driver": config.power.driver,
            "mode": config.power.mode,
            "duration_s": config.power.duration_s,
        },
        "output": {
            "format": config.output.format,
            "dir": str(config.output.dir),
            "model_explorer": config.output.model_explorer,
        },
    }
