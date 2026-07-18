"""Pipeline primitives — Stage protocol, PipelineContext, and PipelineRunner.

The profiling pipeline is a linear sequence of stages.  Each stage reads from
and writes to a shared ``PipelineContext``.  The runner iterates stages,
handles skip logic, wraps exceptions with stage context, and logs progress.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from ._version import __version__
from .artifacts import (
    DeploymentRecord,
    FirmwareArtifact,
    PowerObservation,
    PowerRun,
    PowerRunPlan,
    PowerTerminalEnvelope,
    PowerTerminalRecord,
    ProfileRun,
)
from .config import ProfileConfig
from .engines.base import EngineAdapter, EngineArtifacts
from .errors import HpxError
from .platform import BoardDef, SocDef
from .model_analysis import ModelAnalysis
from .placement import Placement
from .power.base import PowerResult
from .results import MemoryPlan, PmuResult, RunMetadata, BinarySections
from .target.probe.base import FlashBackend, Probe, ResetController

log = logging.getLogger("hpx")


@dataclass(frozen=True)
class ProgressUpdate:
    """User-meaningful progress within a pipeline stage."""

    message: str
    kind: Literal["status", "checkpoint"] = "status"
    completed: int | None = None
    total: int | None = None
    unit: str | None = None
    eta_s: float | None = None
    min_verbosity: int = 0


ProgressSink = Callable[[ProgressUpdate], None]


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
    resolved_jlink_serial: str | None = None
    probe: Probe | None = None
    flash_backend: FlashBackend | None = None
    reset_controller: ResetController | None = None

    # Engine preparation (stage: prepare_engine)
    engine_adapter: EngineAdapter | None = None
    engine_artifacts: EngineArtifacts | None = None

    # Firmware generation (stage: generate_firmware)
    firmware_dir: Path | None = None

    # Build (stage: build_firmware)
    build_dir: Path | None = None
    binary_path: Path | None = None
    binary_sections: BinarySections | None = None
    #: Path to the dedicated transport-free power binary (hpx_profiler_power),
    #: built alongside hpx_profiler only when config.power.enabled. WP3 wires
    #: this into the power-capture flash/run path; this stage only exposes it.
    power_binary_path: Path | None = None

    # Explicit major-stage artifacts. Legacy path fields above remain mirrored
    # during the staged migration so existing internals and tests keep working.
    profile_firmware: FirmwareArtifact | None = None
    power_firmware: FirmwareArtifact | None = None
    deployed_power_firmware: FirmwareArtifact | None = None
    power_plan: PowerRunPlan | None = None

    # Grouped immutable workflow records. Legacy fields above and capture
    # result fields below remain mirrored until reports/API migrate.
    profile_run: ProfileRun | None = None
    power_run: PowerRun | None = None

    # Model analysis (stage: analyze_model — optional)
    model_analysis: ModelAnalysis | None = None

    # Memory plan (stage: plan_memory)
    memory_plan: MemoryPlan | None = None

    #: Resolved arena placement region.  Set by :class:`PlanMemoryStage`
    #: from split model placement controls / compatibility presets and the
    #: SoC memory layout.
    arena_region: Placement | None = None

    #: Resolved weights placement region.  Set by :class:`PlanMemoryStage`.
    #: For TFLM/heliaRT this drives the section attribute applied to
    #: ``model_data[]``.
    weights_region: Placement | None = None

    # Capture (stage: capture_pmu / capture_power)
    pmu_result: PmuResult | None = None
    power_result: PowerResult | None = None

    # Run metadata — enriched by stages, written to report
    run_metadata: RunMetadata = field(default_factory=RunMetadata)

    # Report (stage: generate_report)
    report_paths: list[Path] = field(default_factory=list)

    #: Optional long-lived power driver handle owned by the pipeline.
    #:
    #: Stages that need to keep a Joulescope (or other power driver) USB
    #: handle open across multiple stages assign it here; the
    #: :class:`PipelineRunner` ``finally`` block calls
    #: ``disable_passthrough()`` on whatever is stored to leave hardware in
    #: a clean state.  Most drivers latch the relay in hardware and release
    #: the handle immediately, so this stays ``None`` for typical runs.
    power_driver_handle: Any | None = None

    #: True if :class:`EnsureBoardPoweredStage` skipped passthrough (no JS,
    #: driver missing, ambiguous selection, etc.). Used by downstream stages
    #: (notably ``flash_firmware``) to surface a "is your EVB powered?" hint
    #: when failures look connection-related.
    passthrough_skipped: bool = False

    progress_sink: ProgressSink | None = field(default=None, repr=False, compare=False)

    def publish_profile_firmware(self, firmware: FirmwareArtifact) -> None:
        if firmware.role != "profile":
            raise ValueError("Profile run requires a profile firmware artifact.")
        self.profile_run = ProfileRun(firmware=firmware)
        self.profile_firmware = firmware
        self.pmu_result = None

    def publish_profile_deployment(self, deployment: DeploymentRecord) -> None:
        if self.profile_run is None:
            raise ValueError("Profile firmware must be published before deployment.")
        if deployment.firmware != self.profile_run.firmware:
            raise ValueError("Profile deployment must reference the current firmware artifact.")
        self.profile_run = replace(self.profile_run, deployment=deployment)

    def publish_profile_result(self, result: PmuResult) -> None:
        if self.profile_run is None or self.profile_run.deployment is None:
            raise ValueError("Profile firmware must be deployed before capture.")
        self.profile_run = replace(self.profile_run, result=result)
        self.pmu_result = result

    def publish_power_plan(self, plan: PowerRunPlan) -> None:
        self.power_run = PowerRun(plan=plan)
        self.power_plan = plan
        self.power_firmware = None
        self.deployed_power_firmware = None
        self.power_binary_path = None
        self.power_result = None

    def publish_power_firmware(self, firmware: FirmwareArtifact) -> None:
        if self.power_run is None:
            raise ValueError("Power plan must be published before firmware.")
        if self.power_run.plan.firmware_mode != "dedicated":
            raise ValueError("Shared power runs do not accept dedicated firmware artifacts.")
        if firmware.role != "power":
            raise ValueError("Power run requires a power firmware artifact.")
        self.power_run = replace(
            self.power_run,
            firmware=firmware,
            deployment=None,
            observation=None,
        )
        self.power_firmware = firmware
        self.deployed_power_firmware = None
        self.power_result = None

    def publish_power_deployment(self, deployment: DeploymentRecord) -> None:
        if self.power_run is None or self.power_run.firmware is None:
            raise ValueError("Power firmware must be published before deployment.")
        if deployment.firmware != self.power_run.firmware:
            raise ValueError("Power deployment must reference the current firmware artifact.")
        self.power_run = replace(self.power_run, deployment=deployment)
        self.deployed_power_firmware = deployment.firmware

    def publish_power_observation(self, observation: PowerObservation) -> None:
        if self.power_run is None:
            raise ValueError("Power plan must be published before capture.")
        if (
            self.power_run.plan.firmware_mode == "dedicated"
            and self.power_run.deployment is None
        ):
            raise ValueError("Dedicated power firmware must be deployed before capture.")
        observation.result.metadata.update(
            {
                "observation_mode": observation.mode,
                "integrity": observation.integrity,
                "gate_rise_observed": observation.gate_rise_observed,
                "gate_fall_observed": observation.gate_fall_observed,
                "observation_deadline_s": observation.deadline_s,
            }
        )
        self.power_run = replace(self.power_run, observation=observation)
        self.power_result = observation.result

    def publish_power_terminal(self, terminal: PowerTerminalRecord) -> None:
        if self.power_run is None or self.power_run.deployment is None:
            raise ValueError("Power firmware must be deployed before terminal status.")
        if self.power_run.observation is None and self.config.power.mode.value != "internal":
            raise ValueError("Power observation must complete before terminal status.")
        if self.power_run.terminal is not None:
            raise ValueError("Power terminal status has already been published.")
        self.power_run = replace(self.power_run, terminal=terminal)

    def publish_power_terminal_envelope(self, envelope: PowerTerminalEnvelope) -> None:
        self.publish_power_terminal(envelope.terminal)
        assert self.power_run is not None
        self.power_run = replace(
            self.power_run,
            on_device_summary=envelope.measurement,
        )

    def publish_power_result(self, result: PowerResult) -> None:
        """Compatibility publisher for non-observing drivers and tests."""
        metadata = result.metadata
        valid_gate = metadata.get("measurement_scope") == "gpio_gated_clean_window"
        mode: Literal["gpio_gated", "free_form"] = (
            "gpio_gated" if valid_gate else "free_form"
        )
        self.publish_power_observation(
            PowerObservation(
                mode=mode,
                result=result,
                gate_rise_observed=bool(metadata.get("gate_rise_observed", valid_gate)),
                gate_fall_observed=bool(metadata.get("gate_fall_observed", valid_gate)),
                deadline_s=float(
                    metadata.get("capture_safety_bound_s", result.summary.duration_s)
                ),
                integrity="valid" if valid_gate else "degraded",
            )
        )

    def report_progress(
        self,
        message: str,
        *,
        kind: Literal["status", "checkpoint"] = "status",
        completed: int | None = None,
        total: int | None = None,
        unit: str | None = None,
        eta_s: float | None = None,
        min_verbosity: int = 0,
    ) -> None:
        """Publish an optional progress update without depending on a UI."""
        if self.progress_sink is not None:
            self.progress_sink(
                ProgressUpdate(
                    message=message,
                    kind=kind,
                    completed=completed,
                    total=total,
                    unit=unit,
                    eta_s=eta_s,
                    min_verbosity=min_verbosity,
                )
            )


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
        progress_sink: ProgressSink | None = None,
    ) -> None:
        self._stages = list(stages)
        self._console = console  # Optional HpxConsole — avoids circular import
        self._progress_sink = progress_sink

    def run(self, config: ProfileConfig) -> PipelineContext:
        """Set up the working directory, run all stages, and clean up."""
        work_dir, should_cleanup = _resolve_work_dir(config)
        ctx = PipelineContext(config=config, work_dir=work_dir)
        if self._progress_sink is not None:
            ctx.progress_sink = self._progress_sink
        elif self._console is not None:
            ctx.progress_sink = self._console.progress_update

        # Seed run metadata with immutable fields
        ctx.run_metadata.hpx_version = __version__
        ctx.run_metadata.run_id = str(uuid.uuid4())
        ctx.run_metadata.timestamp = datetime.now(timezone.utc).isoformat()
        ctx.run_metadata.config_snapshot = _serialize_config(config)

        try:
            total_stages = len(self._stages)
            for stage_index, stage in enumerate(self._stages, start=1):
                if stage.should_skip(ctx):
                    log.info("[skip] %s", stage.name)
                    if self._console is not None:
                        self._console.stage_skip(stage.name)
                    continue

                log.info("[start] %s", stage.name)
                if self._console is not None:
                    self._console.stage_start(stage.name, stage_index, total_stages)
                try:
                    stage.run(ctx)
                except KeyboardInterrupt:
                    if self._console is not None:
                        self._console._stop_spinner()
                    raise
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
            # Release any long-lived power driver handle stashed by an
            # earlier stage (e.g. EnsureBoardPoweredStage).  Most drivers
            # latch the relay in hardware and release the handle eagerly,
            # so this is a no-op for typical runs.
            handle = ctx.power_driver_handle
            if handle is not None:
                try:
                    handle.disable_passthrough()
                except Exception:  # pragma: no cover — best-effort cleanup
                    log.debug("Joulescope passthrough release failed (ignored)")
            if should_cleanup:
                shutil.rmtree(work_dir, ignore_errors=True)

        return ctx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_cache_work_dir(config: ProfileConfig) -> Path:
    """Deterministic cache path keyed on board-toolchain-engine."""
    key = f"{config.target.board}-{config.target.toolchain.value}-{config.engine.type.value}"
    return Path.home() / ".cache" / "helia-profiler" / "workspaces" / key


def _resolve_work_dir(config: ProfileConfig) -> tuple[Path, bool]:
    """Return (work_dir, should_cleanup).

    Resolution order:
    1. Explicit ``--work-dir`` — used as-is, never cleaned up.
    2. Default cache directory under ``~/.cache/helia-profiler/workspaces/``
       keyed on ``{board}-{toolchain}-{engine}``.  Enables incremental
       cmake builds across runs.  Never cleaned up automatically.
    """
    if config.work_dir is not None:
        wd = config.work_dir.resolve()
        wd.mkdir(parents=True, exist_ok=True)
        return wd, False

    # Persistent cache directory — enables incremental builds
    wd = _default_cache_work_dir(config)
    if config.clean:
        if wd.exists():
            shutil.rmtree(wd, ignore_errors=True)
            log.info("Cleaned cached work directory: %s", wd)
    wd.mkdir(parents=True, exist_ok=True)
    return wd, False


def _serialize_config(config: ProfileConfig) -> dict[str, Any]:
    """Produce a JSON-safe snapshot of the active configuration.

    Walks the full :class:`ProfileConfig` dataclass tree via
    :func:`dataclasses.asdict`, then coerces non-JSON-native leaves
    (``Path`` \u2192 ``str``, ``Enum`` \u2192 ``.value``, ``set``/``tuple`` \u2192
    ``list``) so the resulting dict can be round-tripped through
    ``json.dumps``.  Adding new fields to any sub-config is automatically
    reflected in the run-metadata snapshot \u2014 no hand-maintained mirror
    to drift.
    """
    from dataclasses import fields, is_dataclass
    from enum import Enum
    from types import MappingProxyType

    def _coerce(value: Any) -> Any:
        if is_dataclass(value):
            return {field.name: _coerce(getattr(value, field.name)) for field in fields(value)}
        if isinstance(value, (dict, MappingProxyType)):
            return {k: _coerce(v) for k, v in value.items()}
        if isinstance(value, dict):
            return {k: _coerce(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_coerce(v) for v in value]
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, Path):
            return str(value)
        return value

    return _coerce(config)
