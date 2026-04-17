"""Pipeline primitives — Stage protocol, PipelineContext, and PipelineRunner.

The profiling pipeline is a linear sequence of stages.  Each stage reads from
and writes to a shared ``PipelineContext``.  The runner iterates stages,
handles skip logic, wraps exceptions with stage context, and logs progress.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .config import ProfileConfig
from .engines.base import EngineAdapter, EngineArtifacts
from .errors import HpxError
from .platform import BoardDef, SocDef

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

    # Capture (stage: capture_pmu / capture_power)
    pmu_raw: dict[str, Any] | None = None
    power_raw: dict[str, Any] | None = None

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

    def __init__(self, stages: list[Stage]) -> None:
        self._stages = list(stages)

    def run(self, config: ProfileConfig) -> PipelineContext:
        """Set up the working directory, run all stages, and clean up."""
        work_dir, should_cleanup = _resolve_work_dir(config)
        ctx = PipelineContext(config=config, work_dir=work_dir)

        try:
            for stage in self._stages:
                if stage.should_skip(ctx):
                    log.info("[skip] %s", stage.name)
                    continue

                log.info("[start] %s", stage.name)
                try:
                    stage.run(ctx)
                except HpxError:
                    raise  # already typed — propagate as-is
                except Exception as exc:
                    raise HpxError(
                        f"Unexpected error in stage '{stage.name}': {exc}",
                        hint="This is likely a bug in heliaPROFILER. "
                             "Please file an issue with the full traceback.",
                    ) from exc
                log.info("[done]  %s", stage.name)

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
