"""Deterministic boundary controls for poller-owned gate decisions."""

from types import SimpleNamespace

import pytest

from helia_profiler.power.joulescope._gated_state import _GatedCaptureState


def state_at(*, required=False):
    clock = SimpleNamespace(now=1000.0)
    state = _GatedCaptureState(
        minimum_gate_s=0.25,
        plan_floor_s=0.5,
        require_done_state=required,
        capture_start=1000.0,
        monotonic=lambda: clock.now,
        wall_time=lambda: clock.now,
    )
    return state, clock


def observe(state, *, level, phase="go_signaled", tick=0, state_high=False):
    return state.observe(
        read_start=tick,
        sample_tick=tick + 1,
        level=level,
        phase=phase,
        state_high=state_high,
    )


@pytest.mark.parametrize(
    "duration,short,below,complete",
    [
        (0.125, 1, 0, False),
        (0.25, 0, 1, False),
        (0.375, 0, 1, False),
        (0.5, 0, 0, True),
        (0.625, 0, 0, True),
    ],
)
def test_minimum_and_plan_floor_classify_exact_boundaries(duration, short, below, complete):
    state, clock = state_at()
    assert observe(state, level=1)
    clock.now += duration
    assert not observe(state, level=0, tick=10)
    evidence = state.snapshot()
    assert evidence.short_pulses_ignored == short
    assert evidence.below_plan_highs == below
    assert state.guard_elapsed(0) is complete


def test_post_go_rise_retains_known_low_without_promoting_pre_go_high():
    state, _ = state_at()
    assert not observe(state, level=1, phase="waiting_ready", tick=10)
    assert not observe(state, level=0, phase="waiting_ready", tick=20)
    assert observe(state, level=1, tick=30)
    assert state.snapshot().poll_samples == ((21, 0), (31, 1))


def test_required_completion_uses_validation_time_and_resets_on_lost_eligibility():
    state, clock = state_at(required=True)
    observe(state, level=0, state_high=False)
    observe(state, level=1, state_high=False)
    clock.now += 0.5
    observe(state, level=0, state_high=True)
    assert state.completion_pending
    assert not state.guard_elapsed(0)
    # Validation takes time: the guard starts after it, not at the gate fall.
    clock.now += 0.25
    state.confirm_completion(True)
    clock.now += 0.125
    assert not state.guard_elapsed(0.25)
    observe(state, level=0, state_high=False)
    assert not state.completion_pending
    state.confirm_completion(False)
    clock.now += 1
    assert not state.guard_elapsed(0.25)
    observe(state, level=0, state_high=True)
    state.confirm_completion(True)
    clock.now += 0.25
    assert state.guard_elapsed(0.25)


def test_readout_retains_original_evidence_after_later_observations():
    state, clock = state_at()
    observe(state, level=1)
    before = state.snapshot()
    clock.now += 0.125
    observe(state, level=0, tick=10)
    assert before.poll_samples == ((0, 0), (1, 1))
    assert not before.saw_any_gate_fall
    assert before.short_pulse_phases == ()
    assert state.snapshot().short_pulse_phases == (("go_signaled", 1),)
