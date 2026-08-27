"""Every clean-window configuration, checked against what the firmware runs.

The window contract has four dimensions -- probe, power firmware mode, window
mode, and power mode -- and the bugs in #125 were all the same shape: a rule
applied correctly in the cell someone looked at and wrongly in a cell nobody
enumerated. Three review rounds found eight of them one at a time, each fix
landing in one branch and missing the next.

So the enumeration is the test. Each cell asks the questions a real run
depends on:

* the plan must describe the window the firmware was built to run, because
  `capture_gated` RAISES on a disagreement and internal mode divides energy by
  it;
* the gate check must ACCEPT a perfect run, for the same reason;
* the capture deadline must outlast the window it contains, or the poller
  misses the falling edge.

The firmware-side length is derived here from the config and the templates'
own rules -- deliberately NOT from `predicted_window_ms`, which is the thing
under test. If the two ever have to be reconciled again, this file is where
the disagreement surfaces.
"""

from __future__ import annotations

from tests.pipeline_context_helpers import set_profile_result

from pathlib import Path

import pytest

from helia_profiler.config import DEFAULT_POWER_WINDOW_TARGET_MS, load_config
from helia_profiler.pipeline import PipelineContext
from helia_profiler.platform import get_soc_for_board
from helia_profiler.power.diagnostics import (
    assess_gate_duration,
    gate_relative_tolerance_for,
    probe_runs_inferences,
)
from helia_profiler.results import FirmwareMeta, LayerResult, PlatformInfo, PmuResult
from helia_profiler.stages.capture_power import _estimate_capture_duration
from helia_profiler.stages.plan_power import plan_power_run

#: A real Apollo4 per-inference figure, so the arithmetic lands on realistic
#: counts rather than round numbers that could hide an off-by-one.
PER_INFER_US = 2226
ITERATIONS = 100

#: Targets either side of the 5 s power floor and the 1 s minimum gate.
TARGETS_MS = (1000, 5000, 20000)

CELLS = [
    pytest.param(
        probe,
        firmware,
        window_mode,
        target_ms,
        power_mode,
        id=f"{probe}-{firmware}-{window_mode}-{target_ms}-{power_mode}",
    )
    for probe in ("infer", "busy_loop")
    for firmware in ("dedicated", "shared")
    for window_mode in ("auto", "fixed")
    for target_ms in TARGETS_MS
    for power_mode in ("external", "internal")
]


def _overrides(probe, firmware, window_mode, target_ms, power_mode, model: Path):
    overrides = {
        "model": {"path": str(model)},
        "engine": {"type": "helia-rt"},
        "target": {"board": "apollo4p_blue_kbr_evb"},
        "profiling": {
            "clean_window_probe": probe,
            "window_mode": window_mode,
            "window_target_ms": target_ms,
            "iterations": ITERATIONS,
            "warmup": 5,
        },
        "power": {"enabled": True, "firmware": firmware, "mode": power_mode},
    }
    if power_mode == "internal":
        overrides["power"]["driver"] = "ondevice"
    return overrides


def _expected_effective_target_ms(config) -> int:
    """The window target the firmware render receives, derived independently.

    Deliberately NOT `config.effective_window_target_ms` -- that property is
    what the plan reads, so using it here would make the two move together and
    the comparison vacuous. This restates the rule the templates document: the
    power floor applies only where the window is auto-sized.
    """
    if config.power.enabled and config.profiling.window_mode == "auto":
        return max(config.profiling.window_target_ms, DEFAULT_POWER_WINDOW_TARGET_MS)
    return config.profiling.window_target_ms


def _profile_meta(config, probe: str, window_mode: str) -> FirmwareMeta:
    """What the PROFILE binary reports, given how IT sizes its own window."""
    if not probe_runs_inferences(probe):
        # One spin of the whole window (#112).
        return FirmwareMeta(
            clean_infer_count=1,
            clean_infer_avg_us=_expected_effective_target_ms(config) * 1000,
        )
    if window_mode == "auto":
        target_s = _expected_effective_target_ms(config) / 1000.0
        count = int(
            max(
                config.profiling.window_min,
                min(config.profiling.window_max, target_s / (PER_INFER_US / 1e6)),
            )
        )
    else:
        count = ITERATIONS
    return FirmwareMeta(clean_infer_count=count, clean_infer_avg_us=PER_INFER_US)


def _firmware_window_s(config, probe, firmware, window_mode, plan) -> float:
    """The window length derived from the templates' rules, not from the plan.

    * busy_loop: `_busy_loop_calibration.j2` spins for the `window_target_ms`
      template variable, which `FirmwareRenderContext` fills from
      `effective_window_target_ms`.
    * dedicated + counted: `render_power_source()` rebuilds with
      `clean_iters=N`, so the firmware runs exactly the host's N.
    * shared + counted: nothing is rebuilt -- `main.cc.j2` runs `iterations`
      in fixed mode, or auto-sizes into `[window_min, window_max]`.
    """
    if not probe_runs_inferences(probe):
        return _expected_effective_target_ms(config) / 1000.0
    if firmware == "dedicated":
        return (plan.inference_count or 0) * PER_INFER_US / 1e6
    if window_mode == "fixed":
        return ITERATIONS * PER_INFER_US / 1e6
    target_s = _expected_effective_target_ms(config) / 1000.0
    # Whole iterations: the firmware runs a counted loop, so the fractional
    # part of the target never happens.
    count = int(
        max(
            config.profiling.window_min,
            min(config.profiling.window_max, target_s / (PER_INFER_US / 1e6)),
        )
    )
    return count * PER_INFER_US / 1e6


def _assess(ctx, config, plan, measured_s: float):
    """The gate check exactly as capture/__init__.py builds it."""
    return assess_gate_duration(
        measured_s=measured_s,
        clean_infer_count=(plan.inference_count or ctx.pmu_result.meta.clean_infer_count),
        clean_infer_avg_us=(plan.reference_inference_us or ctx.pmu_result.meta.clean_infer_avg_us),
        stats_rate_hz=config.power.stats_rate_hz,
        minimum_s=1.0,
        relative_tolerance=gate_relative_tolerance_for(config.profiling.clean_window_probe),
    )


@pytest.fixture()
def cell(tmp_path: Path):
    def _build(probe, firmware, window_mode, target_ms, power_mode):
        model = tmp_path / "m.tflite"
        if not model.exists():
            model.write_bytes(b"\x00")
        config = load_config(
            None, _overrides(probe, firmware, window_mode, target_ms, power_mode, model)
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        set_profile_result(
            ctx,
            PmuResult(
                meta=_profile_meta(config, probe, window_mode),
                layers=[LayerResult(id=0, op="CONV_2D", cycles=213_696.0)],
            ),
        )
        # 213,696 cycles at 96 MHz == PER_INFER_US, so the capture estimate's
        # own arithmetic agrees with the window arithmetic above.
        ctx.run_metadata.platform = PlatformInfo(cpu_clock_mhz=96)
        # Without a resolved SoC _estimate_capture_duration returns None and
        # the deadline check below passes without checking anything -- which
        # it silently did until a mutation exposed it.
        ctx.soc = get_soc_for_board(config.target.board)
        plan = plan_power_run(ctx)
        ctx.publish_power_plan(plan)
        return ctx, config, plan

    return _build


@pytest.mark.parametrize(("probe", "firmware", "window_mode", "target_ms", "power_mode"), CELLS)
def test_the_plan_describes_the_window_the_firmware_runs(
    cell, probe, firmware, window_mode, target_ms, power_mode
):
    ctx, config, plan = cell(probe, firmware, window_mode, target_ms, power_mode)
    expected_s = _firmware_window_s(config, probe, firmware, window_mode, plan)

    if plan.inference_count and plan.reference_inference_us:
        # Stated in microseconds -- must agree exactly.
        planned_s = plan.inference_count * plan.reference_inference_us / 1e6
        expected = pytest.approx(expected_s, rel=1e-6)
    elif firmware == "shared" and window_mode == "auto":
        # The one cell the host cannot predict exactly, in principle: the
        # firmware sizes this window itself, as
        # `target_cyc / clean_warm_cyc` (integer division -- main.cc.j2),
        # from ITS OWN warm measurement rather than the host's reference. So
        # the window lands on a whole number of inferences at or just under
        # the target, and one inference of quantization is the tightest bound
        # that is actually true. Still far tighter than the 5x and 90x
        # disagreements this file exists to catch.
        planned_s = plan.target_duration_ms / 1000.0
        expected = pytest.approx(expected_s, abs=PER_INFER_US / 1e6)
    else:
        # Stated only as target_duration_ms, an integer-millisecond field, so
        # its resolution is the bound.
        planned_s = plan.target_duration_ms / 1000.0
        expected = pytest.approx(expected_s, abs=1e-3)

    assert planned_s == expected, (
        f"plan describes {planned_s:.3f}s against firmware built to run "
        f"{expected_s:.3f}s ({planned_s / expected_s:.2f}x)"
    )


@pytest.mark.parametrize(
    ("probe", "firmware", "window_mode", "target_ms", "power_mode"),
    [c for c in CELLS if c.values[4] == "external"],
)
def test_the_gate_check_accepts_a_perfect_run(
    cell, probe, firmware, window_mode, target_ms, power_mode
):
    """`capture_gated` raises, so a perfect run must never trip it.

    The 1 s minimum gate is a separate, deliberate floor -- a window shorter
    than that fails the contract by design -- so cells below it assert the
    ratio instead, which is what says the plan and the firmware agree.
    """
    ctx, config, plan = cell(probe, firmware, window_mode, target_ms, power_mode)
    measured_s = _firmware_window_s(config, probe, firmware, window_mode, plan)

    integrity = _assess(ctx, config, plan, measured_s)

    assert integrity.ratio == pytest.approx(1.0, rel=1e-6), (
        f"gate check expects {integrity.expected_s:.3f}s for a window that "
        f"runs {integrity.measured_s:.3f}s"
    )
    if measured_s >= 1.0:
        assert integrity.valid, "a perfect run above the minimum gate must pass"


@pytest.mark.parametrize(("probe", "firmware", "window_mode", "target_ms", "power_mode"), CELLS)
def test_the_capture_deadline_outlasts_the_window(
    cell, probe, firmware, window_mode, target_ms, power_mode
):
    """A bound inside the window misses the falling edge entirely."""
    ctx, config, plan = cell(probe, firmware, window_mode, target_ms, power_mode)
    window_s = _firmware_window_s(config, probe, firmware, window_mode, plan)

    estimated = _estimate_capture_duration(ctx)

    assert estimated is not None, "the estimate must exist, or this checks nothing"
    assert estimated > window_s, (
        f"capture bound {estimated:.1f}s is inside a {window_s:.1f}s window"
    )


#: What the gate check must do about an IMPERFECT run, stated in physical
#: terms rather than in terms of the constants under test -- otherwise the
#: assertion just restates whatever the tolerance happens to be.
#:
#: A counted window is two boots of the same N inferences, so a few percent of
#: cross-boot spread is normal and must pass. A predicted window is two
#: independent calibrations of a CPU spin: `_busy_loop_calibration.j2` accepts
#: a calibration reading anywhere in [16, 8192] STIMER ticks, which at
#: 32768 Hz is a per-boot quantization floor of up to 6.25% -- so a 15% miss
#: is inside what a healthy board can produce and must not end the run.
HEALTHY_MISS = {"infer": 0.05, "busy_loop": 0.15}

#: And what it must still reject. A window off by this much is not jitter --
#: it is a mis-sized or mis-measured window, and in internal mode it scales
#: average power and current by the same factor.
#:
#: 0.40 rather than something larger on purpose: the widest wrong band this
#: code has actually shipped was +/-50% (the per-unit slack applied to a
#: one-unit window), and a threshold outside that band cannot see it. The
#: bound has to sit between the widest correct band and the widest wrong one.
BROKEN_MISS = 0.40


@pytest.mark.parametrize(
    ("probe", "firmware", "window_mode", "target_ms", "power_mode"),
    [c for c in CELLS if c.values[4] == "external"],
)
def test_the_gate_check_tolerates_a_healthy_imperfect_run(
    cell, probe, firmware, window_mode, target_ms, power_mode
):
    """Too tight is as much a bug as too loose -- `capture_gated` RAISES.

    Every band regression in #125 was this: a healthy run killed because the
    check was keyed on the wrong thing. Perfect-run cells cannot see it.
    """
    ctx, config, plan = cell(probe, firmware, window_mode, target_ms, power_mode)
    window_s = _firmware_window_s(config, probe, firmware, window_mode, plan)
    if window_s * (1 + HEALTHY_MISS[probe]) < 1.0:
        pytest.skip("below the deliberate 1 s minimum gate")

    integrity = _assess(ctx, config, plan, window_s * (1 + HEALTHY_MISS[probe]))

    assert integrity.valid, (
        f"a run {HEALTHY_MISS[probe]:.0%} over a {window_s:.3f}s window is "
        f"rejected (band {integrity.tolerance_s:.3f}s)"
    )


@pytest.mark.parametrize(
    ("probe", "firmware", "window_mode", "target_ms", "power_mode"),
    [c for c in CELLS if c.values[4] == "external"],
)
def test_the_gate_check_still_rejects_a_genuinely_wrong_window(
    cell, probe, firmware, window_mode, target_ms, power_mode
):
    """And loosening a band must not disarm it."""
    ctx, config, plan = cell(probe, firmware, window_mode, target_ms, power_mode)
    window_s = _firmware_window_s(config, probe, firmware, window_mode, plan)

    integrity = _assess(ctx, config, plan, window_s * (1 + BROKEN_MISS))

    assert not integrity.valid, (
        f"a run {BROKEN_MISS:.0%} over a {window_s:.3f}s window passes "
        f"(band {integrity.tolerance_s:.3f}s)"
    )
