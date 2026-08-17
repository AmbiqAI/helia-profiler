"""Power-capture diagnostic helpers.

Keep host/device sync metadata and failure classification in one small module so
capture wrappers and instrument drivers do not each invent their own diagnostic
shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from .sync import DeviceState

if TYPE_CHECKING:
    from .base import PowerResult


class GateFailureKind(StrEnum):
    """Classified gated-capture transition failure."""

    NO_GATE_RISE = "no_gate_rise"
    NO_GATE_FALL = "no_gate_fall"
    NO_STATS_WINDOW = "no_stats_window"


@dataclass(frozen=True)
class SyncHandshakeMetadata:
    """Host-observed lockstep handshake metadata."""

    lockstep: bool
    ready_wait_s: float | None = None
    ready_observed: bool | None = None
    last_state: DeviceState | None = None

    def to_metadata(self) -> dict[str, object]:
        metadata: dict[str, object] = {"lockstep": self.lockstep}
        if self.ready_wait_s is not None:
            metadata["ready_wait_s"] = self.ready_wait_s
        if self.ready_observed is not None:
            metadata["ready_observed"] = self.ready_observed
        if self.last_state is not None:
            metadata["last_state"] = self.last_state.value
        return metadata


@dataclass(frozen=True)
class GateTransitionTiming:
    """Host-observed GPI gate transition timings."""

    capture_to_gate_rise_s: float | None = None
    capture_to_gate_fall_s: float | None = None
    go_release_to_gate_rise_s: float | None = None

    def to_metadata(self) -> dict[str, float]:
        metadata: dict[str, float] = {}
        if self.capture_to_gate_rise_s is not None:
            metadata["capture_to_gate_rise_s"] = self.capture_to_gate_rise_s
        if self.capture_to_gate_fall_s is not None:
            metadata["capture_to_gate_fall_s"] = self.capture_to_gate_fall_s
        if self.go_release_to_gate_rise_s is not None:
            metadata["go_release_to_gate_rise_s"] = self.go_release_to_gate_rise_s
        return metadata


@dataclass(frozen=True)
class GateFailure:
    """Classified gated-capture failure with user-facing text."""

    kind: GateFailureKind
    message: str
    hint: str

    def to_metadata(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "message": self.message,
            "hint": self.hint,
        }


@dataclass(frozen=True)
class GateDurationIntegrity:
    """Agreement between a measured gate and the expected inference window."""

    measured_s: float
    expected_s: float
    tolerance_s: float
    minimum_s: float = 0.0

    @property
    def valid(self) -> bool:
        return (
            self.measured_s >= self.minimum_s
            and abs(self.measured_s - self.expected_s) <= self.tolerance_s
        )

    @property
    def ratio(self) -> float:
        return self.measured_s / self.expected_s if self.expected_s > 0 else 0.0


def assess_gate_duration(
    *,
    measured_s: float,
    clean_infer_count: int,
    clean_infer_avg_us: int,
    stats_rate_hz: int,
    minimum_s: float = 0.0,
    relative_tolerance: float = 0.01,
) -> GateDurationIntegrity:
    """Compare a gate against ``N * inference_time`` with instrument jitter allowance."""
    expected_s = clean_infer_count * clean_infer_avg_us / 1_000_000.0
    inference_slack_s = clean_infer_avg_us / 2_000_000.0
    packet_slack_s = 2.0 / max(1, stats_rate_hz)
    cross_binary_slack_s = expected_s * relative_tolerance
    return GateDurationIntegrity(
        measured_s=measured_s,
        expected_s=expected_s,
        tolerance_s=max(inference_slack_s, packet_slack_s, cross_binary_slack_s),
        minimum_s=minimum_s,
    )


# ---------------------------------------------------------------------------
# Firmware window-clock integrity
# ---------------------------------------------------------------------------
#
# The dedicated power binary times its own measured window and reports the
# result as HPX_POWER_ELAPSED_US. That clock is independent of every host
# measurement, which makes it the one number that can be silently wrong without
# anything else looking unhealthy: energy is integrated in hardware over real
# time, the completed/requested counts still match, and the gate edges are
# still observed. Two real regressions have taken exactly this shape --
# Apollo4 over-reported its window ~7x (the debug domain the binary powers down
# holds DWT), and Apollo3 reported exactly 0 (nothing holds that domain up on a
# free-running binary). Both published confidently wrong average power and
# current while every other check passed.
#
# The policy lives here so the collect stage (which raises/warns at capture
# time) and evaluation.validity (the downstream authority over an already
# captured run) cannot disagree about it.

#: Externally-referenced tolerance. The firmware clock and the host-timestamped
#: gate edges measure the SAME physical window, so real agreement is tight:
#: 0.065% on Apollo4 Blue Plus and 0.064% on Apollo3 Blue Plus (bench, 2026-08).
#: 5% is ~75x that observed error -- generous enough that instrument jitter,
#: packet quantization and gate-edge poll resolution can never trip it, while
#: still catching the 0x and 7x failures. Warning-only for now: two boards is
#: not a wide enough envelope to make this fatal.
EXTERNAL_WINDOW_CLOCK_TOLERANCE = 0.05

#: Internally-referenced tolerance, TWO-SIDED. Internal mode has no host-timed
#: gate, so the only plan-based reference is N x the reference inference time
#: measured by a DIFFERENT binary (the transport-attached profile build). That
#: cross-binary comparison is legitimately loose, so 5% would false-fail valid
#: runs. The evidence bounding it:
#:   - worst legitimate disagreement observed: 14% (Apollo4, profile clean-loop
#:     757-786 us against a true 866 us window),
#:   - Apollo3 agreed to 0.1%,
#:   - build-to-build swings of ~4% in the profile metric alone, most likely
#:     code-layout / XIP-cache sensitivity rather than instrumentation.
#: 25% sits comfortably above 14% + a few points of build noise while still
#: being tight enough to be useful to the internal-mode (INA228) user, who has
#: no external instrument to fall back on. Do not tighten below the 14%
#: observation without new cross-binary timing evidence.
#:
#: MUST stay two-sided. The one real disagreement had the power window SLOWER
#: than the profile prediction -- the opposite of the "power binary does less
#: logging, so it must be faster" intuition -- so encoding a direction here
#: would have missed it.
INTERNAL_WINDOW_CLOCK_TOLERANCE = 0.25

#: Slack on the host wall-clock ceiling (see :func:`assess_window_clock_ceiling`).
#: The envelope already over-counts the measured window by seconds (flash exit,
#: boot, model init, terminal emit), so this covers only timestamp granularity
#: and small host clock adjustments -- not measurement uncertainty.
WINDOW_CLOCK_CEILING_SLACK_S = 0.25

#: Shared user-facing explanation for a frozen firmware window clock.
FROZEN_WINDOW_CLOCK_HINT = (
    "The firmware completed its inferences but timed the window with a clock "
    "that never advanced. On Cortex-M4F parts DWT->CYCCNT lives in the "
    "CoreSight debug power domain, which the dedicated power binary either "
    "powers down itself or -- free-running with no debugger asserting "
    "CDBGPWRUPREQ -- has nothing holding up. Rebuild the power firmware on a "
    "revision whose SocCapabilities.power_window_timer resolves to STIMER for "
    "this SoC; energy and charge from an external instrument are unaffected, "
    "but average power, average current and elapsed time are not."
)


def firmware_window_clock_is_frozen(
    *, elapsed_us: int | None, completed_count: int
) -> bool:
    """True when firmware completed work but reported zero elapsed time.

    An inference cannot take zero time, so this is never a legitimate reading;
    it is the exact signature of a window timed by a counter that is not
    powered. Kept separate from :func:`assess_window_clock` because it needs no
    reference measurement at all -- it is checkable in every mode, on every
    board, with no instrument attached.
    """
    return completed_count > 0 and elapsed_us == 0


@dataclass(frozen=True)
class WindowClockAgreement:
    """Agreement between the firmware's own window clock and a reference."""

    elapsed_us: int
    reference_s: float
    #: Which independent measurement ``reference_s`` came from, so a warning
    #: can name it and a reader knows how much to trust the comparison.
    reference_source: str
    relative_tolerance: float

    @property
    def elapsed_s(self) -> float:
        return self.elapsed_us / 1_000_000.0

    @property
    def ratio(self) -> float:
        return self.elapsed_s / self.reference_s if self.reference_s > 0 else 0.0

    @property
    def relative_error(self) -> float:
        if self.reference_s <= 0:
            return 0.0
        return abs(self.elapsed_s - self.reference_s) / self.reference_s

    @property
    def agrees(self) -> bool:
        return self.relative_error <= self.relative_tolerance

    def to_metadata(self) -> dict[str, float | int | str]:
        return {
            "elapsed_us": self.elapsed_us,
            "elapsed_s": round(self.elapsed_s, 6),
            "reference_s": round(self.reference_s, 6),
            "reference_source": self.reference_source,
            "relative_error": round(self.relative_error, 6),
            "relative_tolerance": self.relative_tolerance,
            "ratio": round(self.ratio, 6),
        }


def assess_window_clock(
    *, elapsed_us: int, reference_s: float, reference_source: str, relative_tolerance: float
) -> WindowClockAgreement | None:
    """Compare the firmware window clock against an independent reference.

    Returns ``None`` when there is nothing to compare against (no positive
    reference), so callers never have to invent a verdict from missing data.
    """
    if reference_s <= 0:
        return None
    return WindowClockAgreement(
        elapsed_us=elapsed_us,
        reference_s=reference_s,
        reference_source=reference_source,
        relative_tolerance=relative_tolerance,
    )


def gated_window_reference_s(result: "PowerResult") -> tuple[float, str] | None:
    """Most precise host-measured duration of the gated window, with its source.

    Prefers ``gated_windows`` over ``summary.duration_s``. Both are summed from
    the same instrument ``dur_ticks`` at time64 resolution, so neither is
    numerically coarser -- but ``summary.duration_s`` means "the gated window"
    only on the gated path; on the degraded/free-form path the same field holds
    the WHOLE capture window, which would silently turn this check into a
    comparison against an unrelated (much longer) interval. ``gated_windows``
    can only ever mean the gate, so it is the correct source and the fallback
    is used only when the gated path produced a summary but no window list.

    Returns ``None`` when no gated duration is available at all.
    """
    windows_total = sum(window.duration_s for window in result.gated_windows)
    if windows_total > 0:
        return windows_total, "gated_windows"
    summary_duration = result.summary.duration_s
    if summary_duration > 0:
        return summary_duration, "capture_summary"
    return None


@dataclass(frozen=True)
class WindowClockCeiling:
    """Host wall-clock bound on how long the firmware's window could have been.

    The measured window is strictly contained in the interval between the
    moment the host started the power binary and the moment it collected the
    terminal record. A firmware window longer than that whole interval is
    physically impossible, whatever the plan says and whatever board it is.

    This is the internal-mode counterpart to external mode's gate comparison:
    it needs no instrument, no plan and no per-board timing, which makes it the
    check that catches the Apollo4-style ~7x inflation class for the INA228
    user, who has neither a host-timed gate nor anything else to cross-check
    against.

    The envelope deliberately over-counts -- it includes flash-tool exit, boot,
    engine/model init, and the post-window terminal emit and collect wait -- so
    it is a loose ceiling, not a duration estimate. Breaching it is therefore a
    strong signal rather than a marginal one.
    """

    elapsed_us: int
    host_envelope_s: float
    slack_s: float

    @property
    def elapsed_s(self) -> float:
        return self.elapsed_us / 1_000_000.0

    @property
    def limit_s(self) -> float:
        return self.host_envelope_s + self.slack_s

    @property
    def exceeded(self) -> bool:
        return self.elapsed_s > self.limit_s

    @property
    def ratio(self) -> float:
        return self.elapsed_s / self.host_envelope_s if self.host_envelope_s > 0 else 0.0

    def to_metadata(self) -> dict[str, float | int]:
        return {
            "elapsed_us": self.elapsed_us,
            "elapsed_s": round(self.elapsed_s, 6),
            "host_envelope_s": round(self.host_envelope_s, 6),
            "slack_s": self.slack_s,
            "ratio": round(self.ratio, 6),
        }


def assess_window_clock_ceiling(
    *, elapsed_us: int | None, host_envelope_s: float | None
) -> WindowClockCeiling | None:
    """Bound the firmware window by the host's own start-to-collect interval.

    Returns ``None`` when either side is missing or non-positive -- notably the
    frozen-clock case, which :func:`firmware_window_clock_is_frozen` reports far
    more precisely.
    """
    if elapsed_us is None or elapsed_us <= 0:
        return None
    if host_envelope_s is None or host_envelope_s <= 0:
        return None
    return WindowClockCeiling(
        elapsed_us=elapsed_us,
        host_envelope_s=host_envelope_s,
        slack_s=WINDOW_CLOCK_CEILING_SLACK_S,
    )


def window_clock_ceiling_from_metadata(
    data: dict[str, object],
) -> WindowClockCeiling | None:
    """Rebuild a ceiling from stored metadata, re-deriving the verdict.

    The collect stage records the two measurements; downstream policy recomputes
    ``exceeded`` from them rather than trusting a stored boolean, so the two
    cannot drift apart the way a cached verdict would.
    """
    try:
        return WindowClockCeiling(
            elapsed_us=int(data["elapsed_us"]),  # type: ignore[arg-type]
            host_envelope_s=float(data["host_envelope_s"]),  # type: ignore[arg-type]
            slack_s=float(data["slack_s"]),  # type: ignore[arg-type]
        )
    except (KeyError, TypeError, ValueError):
        return None


def assess_run_window_clock(
    *,
    elapsed_us: int | None,
    internal_mode: bool,
    gated_result: "PowerResult | None",
    planned_inference_count: int | None,
    planned_inference_us: int | None,
) -> WindowClockAgreement | None:
    """Resolve reference + tolerance for one run and compare the window clock.

    Mode selects the reference, and the reference selects the tolerance -- they
    are not independent knobs, so they are chosen together here rather than at
    each call site. External mode gets the host-timed gate (same physical
    window, tight bound); internal mode gets the host plan (a different binary's
    timing, loose bound). Returns ``None`` whenever no usable reference exists,
    which callers treat as "nothing to say", not "passed".
    """
    if elapsed_us is None or elapsed_us <= 0:
        # 0 is the frozen-clock case, handled by
        # firmware_window_clock_is_frozen() with a far better message; a ratio
        # of 0.0 here would only restate it less clearly.
        return None
    if not internal_mode:
        if gated_result is None:
            return None
        reference = gated_window_reference_s(gated_result)
        if reference is None:
            return None
        reference_s, reference_source = reference
        tolerance = EXTERNAL_WINDOW_CLOCK_TOLERANCE
    else:
        if not planned_inference_count or not planned_inference_us:
            return None
        reference_s = planned_inference_count * planned_inference_us / 1_000_000.0
        reference_source = "planned_window"
        tolerance = INTERNAL_WINDOW_CLOCK_TOLERANCE
    return assess_window_clock(
        elapsed_us=elapsed_us,
        reference_s=reference_s,
        reference_source=reference_source,
        relative_tolerance=tolerance,
    )


def classify_gate_failure(
    *, saw_gate_rise: bool, saw_gate_fall: bool = False, duration_s: float
) -> GateFailure:
    """Classify why a gated capture produced no complete high window."""
    if not saw_gate_rise:
        return GateFailure(
            kind=GateFailureKind.NO_GATE_RISE,
            message="No GPIO gate rising edge detected during Joulescope gated capture",
            hint=(
                "Check GO/state/gate wiring, confirm the firmware reached the power "
                "window wait state, and verify the selected reset strategy relaunches "
                "the firmware before capture."
            ),
        )
    if saw_gate_fall:
        return GateFailure(
            kind=GateFailureKind.NO_STATS_WINDOW,
            message="GPIO gate edges were observed but no Joulescope stats window was selected",
            hint=(
                "The device completed its gated window, but host GPIO timestamps did not "
                "overlap the instrument stats timeline. Retain the diagnostic artifact and "
                "check Joulescope callback timing before trusting power data."
            ),
        )
    return GateFailure(
        kind=GateFailureKind.NO_GATE_FALL,
        message="GPIO gate rose but did not fall during Joulescope gated capture",
        hint=(
            "The firmware entered the measured window but did not close it before "
            f"the {duration_s:.1f}s safety bound. Increase power.duration_s or "
            "check for firmware hangs inside the clean window."
        ),
    )


__all__ = [
    "EXTERNAL_WINDOW_CLOCK_TOLERANCE",
    "FROZEN_WINDOW_CLOCK_HINT",
    "INTERNAL_WINDOW_CLOCK_TOLERANCE",
    "WINDOW_CLOCK_CEILING_SLACK_S",
    "GateDurationIntegrity",
    "GateFailure",
    "GateFailureKind",
    "GateTransitionTiming",
    "SyncHandshakeMetadata",
    "WindowClockAgreement",
    "WindowClockCeiling",
    "assess_gate_duration",
    "assess_run_window_clock",
    "assess_window_clock",
    "assess_window_clock_ceiling",
    "classify_gate_failure",
    "firmware_window_clock_is_frozen",
    "gated_window_reference_s",
    "window_clock_ceiling_from_metadata",
]
