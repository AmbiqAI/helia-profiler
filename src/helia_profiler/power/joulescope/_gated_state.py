"""Poller-owned gate transitions; instrument effects remain in capture orchestration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from ..completion import GatedCompletion

log = logging.getLogger("hpx")


@dataclass(frozen=True)
class _GateEvidence:
    """Stable internal readout after admitted poller processing has drained."""

    poll_samples: tuple[tuple[int, int], ...]
    poll_reads: tuple[tuple[int, int, int], ...]
    first_high_at: float | None
    first_low_after_high_at: float | None
    saw_any_gate_rise: bool
    saw_any_gate_fall: bool
    short_pulses_ignored: int
    short_pulse_phases: tuple[tuple[str, int], ...]
    short_pulse_first_s: float | None
    short_pulse_last_s: float | None
    below_plan_highs: int
    below_plan_rise_at: float | None
    below_plan_fall_at: float | None
    early_stopped: bool


class _GatedCaptureState:
    """Single-writer control state, read by orchestration only after drain."""

    def __init__(
        self,
        *,
        minimum_gate_s: float,
        plan_floor_s: float,
        require_done_state: bool,
        capture_start: float,
        monotonic: Callable[[], float],
        wall_time: Callable[[], float],
    ) -> None:
        self._minimum_gate_s = minimum_gate_s
        self._plan_floor_s = plan_floor_s
        self._require_done_state = require_done_state
        self._capture_start = capture_start
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._poll_samples: list[tuple[int, int]] = []
        self._poll_reads: list[tuple[int, int, int]] = []
        self._short_pulse_phases: dict[str, int] = {}
        self._first_high_at: float | None = None
        self._first_low_after_high_at: float | None = None
        self._saw_any_gate_rise: bool = False
        self._saw_any_gate_fall: bool = False
        self._short_pulses_ignored: int = 0
        self._short_pulse_first_s: float | None = None
        self._short_pulse_last_s: float | None = None
        self._below_plan_highs: int = 0
        self._below_plan_rise_at: float | None = None
        self._below_plan_fall_at: float | None = None
        self._early_stopped: bool = False
        self._windows_done = 0
        self._prev_level = 0
        self._high_seen = False
        self._high_phase = "unknown"
        self._complete_at: float | None = None
        self._completion = GatedCompletion()
        self._completion_pending = False

    def observe(
        self,
        *,
        read_start: int,
        sample_tick: int,
        level: int,
        state_high: bool,
        phase: str,
    ) -> bool:
        """Record a poll and return whether the caller must dispatch gate rise."""
        rose = False
        self._completion_pending = self._completion.observe(
            state_high=state_high,
            gate_high=bool(level),
            go_signaled=phase == "go_signaled",
        )
        if phase != "go_signaled":
            # Flash/reset pin levels are not protocol events. Keep the
            # latest bitfield for READY qualification, but establish a
            # fresh low/high gate history only after GO is sent.
            self._poll_samples.clear()
            self._poll_reads[:] = [(read_start, sample_tick, level)]
            self._high_seen = False
            self._first_high_at = None
            self._first_low_after_high_at = None
            self._prev_level = level
            return False
        # GO may be asserted between poll iterations.  In that case
        # the first retained post-GO sample is already high, while
        # the preceding level carries the known pre-GO low
        # state.  Preserve that state in the segmenter input so the
        # real falling edge can close a complete window.
        if not self._poll_samples and not self._prev_level and level:
            prior_tick = self._poll_reads[-1][1] if self._poll_reads else read_start
            self._poll_samples.append((prior_tick, 0))
            if not self._poll_reads:
                self._poll_reads.append((read_start, read_start, 0))
        self._poll_samples.append((sample_tick, level))
        self._poll_reads.append((read_start, sample_tick, level))
        if level and not self._prev_level:
            self._high_seen = True
            self._saw_any_gate_rise = True
            self._high_phase = phase
            if self._first_high_at is None:
                self._first_high_at = self._monotonic()
                log.debug(
                    "gate-race timeline: GPI rise detected t=%.3f (first poll sample=%s)",
                    self._wall_time(),
                    len(self._poll_samples) == 1,
                )
                rose = True
        elif self._prev_level and not level and self._high_seen:
            self._saw_any_gate_fall = True
            if self._first_low_after_high_at is None:
                self._first_low_after_high_at = self._monotonic()
                log.debug("gate-race timeline: GPI fall detected t=%.3f", self._wall_time())
            high_duration_s = (
                self._monotonic() - self._first_high_at if self._first_high_at is not None else 0.0
            )
            if high_duration_s >= self._plan_floor_s:
                self._windows_done += 1
                if (
                    not self._require_done_state
                    and self._windows_done >= 1
                    and self._complete_at is None
                ):
                    self._complete_at = self._monotonic()
            elif high_duration_s >= self._minimum_gate_s:
                # Too short for the plan: keep waiting for the real
                # window, but keep this high as the fallback candidate.
                self._below_plan_highs += 1
                self._below_plan_rise_at = self._first_high_at
                self._below_plan_fall_at = self._first_low_after_high_at
                log.debug(
                    "GPIO-high of %.6fs is short of the plan; waiting",
                    high_duration_s,
                )
                self._first_high_at = None
                self._first_low_after_high_at = None
                self._high_seen = False
            else:
                self._short_pulses_ignored += 1
                self._short_pulse_phases[self._high_phase] = (
                    self._short_pulse_phases.get(self._high_phase, 0) + 1
                )
                pulse_start_s = (
                    self._first_high_at - self._capture_start
                    if self._first_high_at is not None
                    else 0.0
                )
                self._short_pulse_first_s = (
                    pulse_start_s
                    if self._short_pulse_first_s is None
                    else min(self._short_pulse_first_s, pulse_start_s)
                )
                self._short_pulse_last_s = max(self._short_pulse_last_s or 0.0, pulse_start_s)
                log.debug(
                    "Ignoring %.6fs GPIO-high pulse shorter than the %.3fs minimum power gate",
                    high_duration_s,
                    self._minimum_gate_s,
                )
                self._first_high_at = None
                self._first_low_after_high_at = None
                self._high_seen = False
        self._prev_level = level
        return rose

    @property
    def completion_pending(self) -> bool:
        return self._completion_pending

    def confirm_completion(self, valid: bool) -> None:
        """Apply this poll's retained-stream validation, after caller effects."""
        if not valid:
            self._complete_at = None
        elif self._complete_at is None:
            self._complete_at = self._monotonic()

    def guard_elapsed(self, guard_s: float) -> bool:
        if self._complete_at is not None and self._monotonic() - self._complete_at >= guard_s:
            self._early_stopped = True
            return True
        return False

    def snapshot(self) -> _GateEvidence:
        return _GateEvidence(
            poll_samples=tuple(self._poll_samples),
            poll_reads=tuple(self._poll_reads),
            first_high_at=self._first_high_at,
            first_low_after_high_at=self._first_low_after_high_at,
            saw_any_gate_rise=self._saw_any_gate_rise,
            saw_any_gate_fall=self._saw_any_gate_fall,
            short_pulses_ignored=self._short_pulses_ignored,
            short_pulse_phases=tuple(self._short_pulse_phases.items()),
            short_pulse_first_s=self._short_pulse_first_s,
            short_pulse_last_s=self._short_pulse_last_s,
            below_plan_highs=self._below_plan_highs,
            below_plan_rise_at=self._below_plan_rise_at,
            below_plan_fall_at=self._below_plan_fall_at,
            early_stopped=self._early_stopped,
        )
