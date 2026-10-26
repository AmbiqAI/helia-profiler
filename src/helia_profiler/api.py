"""Public programmatic API for heliaPROFILER.

This is the primary entry point for library users::

    from helia_profiler import profile, ProfileConfig, ModelConfig, EngineConfig, EngineType

    config = ProfileConfig(
        model=ModelConfig(path=Path("my_model.tflite")),
        engine=EngineConfig(type=EngineType.HELIA_RT),
    )
    result = profile(config)
    if result.total_cycles is not None:
        print(f"{result.total_cycles:,.0f} total cycles across {result.layer_count} layers")

The CLI (``hpx profile``) shares the internal execution helper and typed
result projection with this function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._result_view import export_result as export_result
from ._result_view import observations as observations
from .records import inspect_result as inspect_result
from .records import load_result as load_result

if TYPE_CHECKING:
    from .config import ProfileConfig
    from .pipeline import PipelineObserver, ProgressSink
    from .results import ProfileResult


def profile(
    config: ProfileConfig,
    *,
    progress_sink: ProgressSink | None = None,
) -> ProfileResult:
    """Run a full profiling session.

    This is the main programmatic entry point.  It builds the default pipeline,
    executes all stages, and returns a typed :class:`ProfileResult`.

    The result includes the stored report evaluation, whose payload retains
    the experimental :class:`RunEvaluation` contract. Invalid evaluations
    are returned normally, including when ``output.fail_on_invalid`` is set;
    that exit policy belongs to the CLI.

    Raises :class:`HpxError` (or a subclass) on failure.
    """
    return _execute_profile(config, progress_sink=progress_sink)


def _execute_profile(
    config: ProfileConfig,
    *,
    observer: PipelineObserver | None = None,
    progress_sink: ProgressSink | None = None,
) -> ProfileResult:
    """Execute once and project the completed context for API and CLI callers."""
    from .profiler import run_profile
    from .results import ProfileResult

    ctx = run_profile(config, observer=observer, progress_sink=progress_sink)

    return ProfileResult(
        pmu=ctx.captured_pmu,
        power=ctx.power_result,
        power_observation=(ctx.power_run.observation if ctx.power_run is not None else None),
        power_terminal=(ctx.power_run.terminal if ctx.power_run is not None else None),
        on_device_power=(ctx.power_run.on_device_summary if ctx.power_run is not None else None),
        metadata=ctx.run_metadata,
        report_paths=list(ctx.report_paths),
        evaluation=ctx.run_evaluation,
    )
