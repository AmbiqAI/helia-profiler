"""The busy_loop plan and the firmware it renders must describe ONE window.

The busy_loop probe is the only clean-window shape whose length the FIRMWARE
chooses (``_busy_loop_calibration.j2`` sizes its spin from
``window_target_ms``), and nothing tells the host how long that turned out
to be.  Every host-side consumer of ``count x reference_us`` -- the external
gate check, which RAISES; the internal window-clock check; the INA228
accumulator-cadence guard -- trusts the plan to have predicted the firmware's
number, and two rules once computed it differently (#125).

These tests read the number out of the GENERATED C rather than the plan, so
a second rule cannot come back without failing them.
"""

from __future__ import annotations

from tests.pipeline_context_helpers import set_profile_result

import re
from pathlib import Path

import pytest

from helia_profiler.engines import TFLM_ENGINE_HEADER
from helia_profiler.engines.base import TflmArtifacts
from helia_profiler.firmware import _jinja_env
from helia_profiler.firmware.context import FirmwareRenderContext
from helia_profiler.pipeline import PipelineContext
from helia_profiler.results import FirmwareMeta, PmuResult
from helia_profiler.stages.plan_power import plan_power_run

from .conftest import make_pmu_ctx

#: ``_busy_loop_calibration.j2`` turns the target into STIMER ticks with this
#: expression, so the millisecond figure the compiled firmware spins for is
#: recoverable from the rendered C.
_SPIN_TARGET_RE = re.compile(r"HPX_STIMER_HZ \* \(uint64_t\)(\d+)U\) / 1000ULL")


def _firmware_spin_target_ms(ctx: PipelineContext) -> int:
    """The window length the generated C is actually built to spin for."""
    # from_pipeline_context asserts the engine stage has run; nothing about the
    # window target depends on which engine.
    ctx.engine_artifacts = TflmArtifacts(engine_header=TFLM_ENGINE_HEADER)
    template_vars = FirmwareRenderContext.from_pipeline_context(ctx).to_template_vars()
    rendered = _jinja_env.get_template("_busy_loop_calibration.j2").render(**template_vars)
    match = _SPIN_TARGET_RE.search(rendered)
    assert match is not None, "spin target not found in the rendered calibration"
    return int(match.group(1))


def _busy_loop_ctx(tmp_path: Path, *, window_mode: str, window_target_ms: int) -> PipelineContext:
    ctx = make_pmu_ctx(
        tmp_path,
        board="apollo4p_blue_kbr_evb",
        power_enabled=True,
        extra={
            "profiling": {
                "clean_window_probe": "busy_loop",
                "window_target_ms": window_target_ms,
                "window_mode": window_mode,
            },
            "power": {"firmware": "dedicated"},
        },
    )
    # What the profile pass reports under busy_loop: one spin, whole window.
    set_profile_result(
        ctx,
        PmuResult(
            meta=FirmwareMeta(clean_infer_count=1, clean_infer_avg_us=window_target_ms * 1000),
            layers=[],
        ),
    )
    return ctx


@pytest.mark.parametrize("window_mode", ["auto", "fixed"])
@pytest.mark.parametrize("window_target_ms", [1000, 5000, 9000])
def test_plan_window_equals_the_window_the_firmware_is_built_to_spin(
    tmp_path: Path, window_mode: str, window_target_ms: int
):
    """``count x reference_us`` must equal the C's own spin target, exactly.

    Reproduced before the fix at ``window_mode: fixed`` / 1000 ms: firmware
    built to spin 1000 ms, plan describing 5000 ms.
    """
    ctx = _busy_loop_ctx(tmp_path, window_mode=window_mode, window_target_ms=window_target_ms)

    plan = plan_power_run(ctx)
    firmware_ms = _firmware_spin_target_ms(ctx)

    assert plan.inference_count is not None
    assert plan.reference_inference_us is not None
    plan_ms = plan.inference_count * plan.reference_inference_us / 1000
    assert plan_ms == firmware_ms, (
        f"host plans a {plan_ms:.0f} ms window against firmware built to spin "
        f"{firmware_ms} ms ({window_mode=}, {window_target_ms=})"
    )
    # target_duration_ms is published in the plan metadata and read as the
    # window length wherever no count is available, so it must agree too.
    assert plan.target_duration_ms == firmware_ms


@pytest.mark.parametrize("window_mode", ["auto", "fixed"])
def test_the_external_gate_check_accepts_the_window_the_firmware_runs(
    tmp_path: Path, window_mode: str
):
    """End to end: the check that RAISES must accept a perfect run.

    `capture_gated` rejects rather than warns, so a plan that overstates the
    window does not degrade the result -- it ends the run. This drives the
    real `assess_gate_duration` with the real plan and a gate exactly as long
    as the generated C spins for.
    """
    from helia_profiler.power.diagnostics import (
        assess_gate_duration,
        gate_relative_tolerance_for,
    )

    ctx = _busy_loop_ctx(tmp_path, window_mode=window_mode, window_target_ms=1000)
    plan = plan_power_run(ctx)
    firmware_ms = _firmware_spin_target_ms(ctx)

    assert plan.inference_count is not None
    assert plan.reference_inference_us is not None
    integrity = assess_gate_duration(
        measured_s=firmware_ms / 1000,
        clean_infer_count=plan.inference_count,
        clean_infer_avg_us=plan.reference_inference_us,
        stats_rate_hz=ctx.config.power.stats_rate_hz,
        relative_tolerance=gate_relative_tolerance_for(plan.count_source),
    )

    assert integrity.valid, (
        f"gate check rejects a perfect run: measured {integrity.measured_s:.3f}s "
        f"vs expected {integrity.expected_s:.3f}s ({window_mode=})"
    )
