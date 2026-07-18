"""Public programmatic API for heliaPROFILER.

This is the primary entry point for library users::

    from helia_profiler import profile, ProfileConfig, ModelConfig, EngineConfig, EngineType

    config = ProfileConfig(
        model=ModelConfig(path=Path("my_model.tflite")),
        engine=EngineConfig(type=EngineType.HELIA_RT),
    )
    result = profile(config)
    print(f"{result.total_cycles:,.0f} total cycles across {result.layer_count} layers")

The CLI (``hpx``) is a thin wrapper around this same function.
"""

from __future__ import annotations

from .config import ProfileConfig
from .results import ProfileResult


def profile(config: ProfileConfig) -> ProfileResult:
    """Run a full profiling session.

    This is the main programmatic entry point.  It builds the default pipeline,
    executes all stages, and returns a typed :class:`ProfileResult`.

    Raises :class:`HpxError` (or a subclass) on failure.
    """
    from .profiler import run_profile

    ctx = run_profile(config)

    assert ctx.pmu_result is not None

    return ProfileResult(
        pmu=ctx.pmu_result,
        power=ctx.power_result,
        power_observation=(
            ctx.power_run.observation if ctx.power_run is not None else None
        ),
        power_terminal=(
            ctx.power_run.terminal if ctx.power_run is not None else None
        ),
        on_device_power=(
            ctx.power_run.on_device_summary if ctx.power_run is not None else None
        ),
        metadata=ctx.run_metadata,
        report_paths=list(ctx.report_paths),
    )
