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


#: How far the measured gate may sit from the expected window, per
#: ``count_source``.
#:
#: The question this answers is how well the host could KNOW the window length
#: before the run. EVERY case here is cross-boot -- the gate belongs to the
#: power boot while the per-unit reference was timed by the profile boot, and
#: in ``shared`` mode capture/__init__.py reads both count and reference
#: straight off ``pmu_result.meta``. So the floor is the cross-boot spread:
#:
#:   * ``firmware_auto`` -- the shared-firmware case, where the host has no
#:     plan at all and BOTH numbers come from the other boot. This carried
#:     0.01 until the per-unit slack below stopped masking it, which put a
#:     shared busy_loop run on a +/-1% band: two boots' spins differing 1.2%
#:     made `capture_gated` RAISE on a healthy run (found by review).
#:   * ``configured`` / ``profile_guided`` -- N counted inferences, timed by
#:     the other boot. The same cross-boot spread.
#:   * ``probe_window`` -- nothing is counted. The busy_loop window is a spin
#:     whose length is PREDICTED from a per-boot calibration pass, so its
#:     error is the calibration's transfer error rather than a timing spread,
#:     and it gets the loosest band. 0.25 still TIGHTENS this case: the
#:     per-unit slack used to work out to half the window (the unit IS the
#:     window here), which no other bound could ever exceed.
#:
#: A count_source missing from this map would take the default and could not
#: state its own reasoning, so tests/test_power.py pins the map against the
#: Literal's own arguments.
_GATE_RELATIVE_TOLERANCE: dict[str, float] = {
    "firmware_auto": 0.10,
    "configured": 0.10,
    "profile_guided": 0.10,
    "probe_window": 0.25,
}

#: Used when a count_source is not in the map. Loose rather than tight on
#: purpose: an unmapped source is a bug, and the tight branch turns that bug
#: into a run that cannot complete instead of one that merely warns.
DEFAULT_GATE_RELATIVE_TOLERANCE = 0.10


def gate_relative_tolerance_for(count_source: str) -> float:
    """Gate tolerance implied by how the plan's count was chosen."""
    return _GATE_RELATIVE_TOLERANCE.get(count_source, DEFAULT_GATE_RELATIVE_TOLERANCE)


def assess_gate_duration(
    *,
    measured_s: float,
    clean_infer_count: int,
    clean_infer_avg_us: int,
    stats_rate_hz: int,
    minimum_s: float = 0.0,
    # Deliberately NOT DEFAULT_GATE_RELATIVE_TOLERANCE: that default exists for
    # a caller that HAS a count_source and finds it unmapped, where a loose
    # band turns a bug into a warning instead of a failed run. A caller with no
    # plan at all is the opposite situation -- this check is only advisory
    # there, so loosening it would silently stop flagging real truncation.
    relative_tolerance: float = 0.01,
) -> GateDurationIntegrity:
    """Compare a gate against ``N * inference_time`` with instrument jitter allowance."""
    expected_s = clean_infer_count * clean_infer_avg_us / 1_000_000.0
    # Half of one unit of work, because a window can end part-way through a
    # unit. With a SINGLE unit there is no partial-unit boundary to allow for,
    # and the term would be half the entire measurement -- swamping every
    # other bound and leaving a +/-50% check that calls almost anything valid.
    # A probe_window plan is exactly that shape (one unit lasting the whole
    # window), and so is a 1-inference counted window.
    inference_slack_s = (
        clean_infer_avg_us / 2_000_000.0 if clean_infer_count > 1 else 0.0
    )
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
# anything else looking unhealthy: the completed/requested counts still match,
# and the gate edges are still observed. Two real regressions have taken
# exactly this shape -- Apollo4 over-reported its window ~7x (the debug domain
# the binary powers down holds DWT), and Apollo3 reported exactly 0 (nothing
# holds that domain up on a free-running binary).
#
# What that costs depends on the mode, and the difference matters:
#   * INTERNAL: the firmware clock IS the denominator. capture/power_terminal.py
#     requires MEASUREMENT_DURATION_US == ELAPSED_US, so average power and
#     current are computed from the broken number and are wrong by the same
#     factor. Only the integrated energy and charge survive.
#   * EXTERNAL: the instrument owns every published power number. A broken
#     firmware clock corrupts elapsed_us and nothing else -- the Apollo3
#     baseline capture reported elapsed_us=0 and still had average power correct
#     to 0.19% against the fixed run.
# Severity follows that split; see the collect stage and evaluation.validity.
#
# The policy lives here so the collect stage (which raises/warns at capture
# time) and evaluation.validity (the downstream authority over an already
# captured run) cannot disagree about it.

#: Externally-referenced tolerance. The firmware clock and the host-timestamped
#: gate edges measure the SAME physical window, so real agreement is tight.
#: Evidence, stated with its actual n:
#:   - Apollo510B: 13 gated runs, all within 0.08% -- the bulk of the sample,
#:   - Apollo3 Blue Plus: ONE valid gated run, 0.064%,
#:   - Apollo4 Blue Plus: ONE valid gated run, 0.065%.
#: 5% is ~60x the worst of those -- generous enough that instrument jitter,
#: packet quantization and gate-edge poll resolution can never trip it, while
#: still catching the 0x and 7x failures. Warning-only: three boards, and only
#: one run on two of them, is not a wide enough envelope to make this fatal.
EXTERNAL_WINDOW_CLOCK_TOLERANCE = 0.05

#: Internally-referenced tolerance, TWO-SIDED. Internal mode has no host-timed
#: gate, so the only plan-based reference is N x the reference inference time
#: measured by a DIFFERENT binary (the transport-attached profile build). That
#: cross-binary comparison is legitimately loose, so 5% would false-fail valid
#: runs. The evidence bounding it:
#:   - worst legitimate disagreement observed: 14.5% (Apollo4, profile
#:     clean-loop 757-786 us against a true 866 us window),
#:   - Apollo3 agreed to 0.8%,
#:   - build-to-build swings of ~4% in the profile metric alone.
#: 25% clears 14.5% plus a few points of build noise while still being tight
#: enough to be useful to the internal-mode (INA228) user, who has no external
#: instrument to fall back on. Do not tighten below the 14.5% observation
#: without new cross-binary timing evidence; the guardrail is rounded up from
#: 14.48% deliberately.
#:
#: Two caveats on that 14.5%, both of which argue against reading it as a
#: characterised bound:
#:   - it is not one run. FOUR runs showed the skew, and in every one the power
#:     window was SLOWER than the profile predicted -- the opposite of the
#:     "power binary does less logging, so it must be faster" intuition. Which
#:     is why this comparison MUST stay two-sided: a directional check would
#:     have missed the only failure mode actually observed.
#:   - it is config-correlated, not obviously build-noise. The skew appears only
#:     in internal-mode/INA228 builds; external builds of the same code agreed
#:     to <0.1%. Code-layout / XIP-cache sensitivity is one hypothesis for that,
#:     not an established explanation -- the INA228 bus traffic inside the
#:     window is at least as plausible. Treat the number as an observation with
#:     an unknown cause.
INTERNAL_WINDOW_CLOCK_TOLERANCE = 0.25

#: Slack on the host wall-clock ceiling (see :func:`assess_window_clock_ceiling`).
#: The envelope already over-counts the measured window by seconds (flash exit,
#: boot, model init, terminal emit), so this covers only timestamp granularity
#: and small host clock adjustments -- not measurement uncertainty.
WINDOW_CLOCK_CEILING_SLACK_S = 0.25

#: Shared user-facing explanation for a frozen firmware window clock.
#:
#: Two causes produce this identical signature, and the hint must name both --
#: ``SocCapabilities.power_window_timer`` now resolves to ``stimer`` for EVERY
#: registered SoC, so on current firmware the debug domain is no longer even
#: the likelier of the two.
FROZEN_WINDOW_CLOCK_HINT = (
    "The firmware completed its inferences but timed the window with a clock "
    "that never advanced. Two causes produce this exact signature. (1) A DWT-"
    "timed window on a Cortex-M4F part: DWT->CYCCNT lives in the CoreSight "
    "debug power domain, which the dedicated power binary either powers down "
    "itself or -- free-running with no debugger asserting CDBGPWRUPREQ -- has "
    "nothing holding up; rebuild on a revision whose "
    "SocCapabilities.power_window_timer resolves to STIMER for this SoC. "
    "(2) A STIMER-timed window whose 32.768 kHz XTAL source is stopped or "
    "absent on this board, leaving the counter at zero; check that the target "
    "populates and starts XT. In internal (on-device monitor) mode the "
    "reported duration is the denominator for average power and current, so "
    "both are wrong by the same factor and only integrated energy and charge "
    "survive. In external mode the instrument owns those numbers and only "
    "elapsed_us is affected."
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


#: Clean-window probes that never execute a model inference.  The ``busy_loop``
#: diagnostic replaces the whole window body with one calibrated CPU spin whose
#: length comes from ``window_target_ms``, so a run using it does exactly one
#: unit of work no matter what the host planned.
_NON_INFERENCE_PROBES = frozenset({"busy_loop"})


def probe_runs_inferences(clean_window_probe: str) -> bool:
    """Whether *clean_window_probe* executes the model inside the window.

    Every host-side rule that reasons about "how many inferences ran" or "how
    long N inferences should take" is meaningless when this is False, so the
    rules ask here rather than each spelling ``!= "busy_loop"`` for itself.
    """
    return clean_window_probe not in _NON_INFERENCE_PROBES


def expected_terminal_requested_count(
    *, inference_count: int | None, clean_window_probe: str
) -> int | None:
    """What the firmware's terminal report should call ``requested_count``.

    Normally the host's planned inference count: firmware renders
    ``clean_iters_n`` from it and reports it straight back.

    The ``busy_loop`` probe is the exception.  It runs no inferences at all --
    one calibrated spin window, sized from ``window_target_ms`` -- so firmware
    reports 1 requested and 1 completed (see ``_power_terminal_success.j2``).
    A host that still expected N would reject every such run as "incomplete
    inference execution", which is exactly what happened before this was
    centralized: the diagnostic could not finish a run on any board, and the
    window duration it exists to expose was never consumed.

    Returns ``None`` when there is no planned count to check against.
    """
    if inference_count is None:
        return None
    if not probe_runs_inferences(clean_window_probe):
        return 1
    return inference_count


# ---------------------------------------------------------------------------
# Profile clean-window clock integrity
# ---------------------------------------------------------------------------
#
# The two checks above police the *power* binary's window clock. This one
# polices the clock that measures the PROFILE binary's clean window -- the
# number published as clean_infer_avg_us, which is also the reference the power
# plan sizes its window from (stages/plan_power.py). It is a different failure
# with a different signature, which is why it is a different check:
#
#   * frozen power clock: elapsed_us == 0, or off by a large factor. Loud.
#   * stalled profile clock: the window loses a *sub-interval*, so the average
#     comes back some percentage low. It is never zero, never inverted, and
#     lands squarely inside every plausible range -- 21% low on the Apollo4
#     runs in #121, against a 3.9% legitimate build-to-build spread. Nothing
#     downstream could tell.
#
# Detection does not need a reference measurement, because the firmware reports
# the fault directly. It reports TWO counts, because a dropped debug domain has
# been seen doing two different things to the counter:
#
#   * FROZEN (the usual case, and what #121 measured): CYCCNT stops, so every
#     iteration wholly inside the stall reads a delta of exactly zero. An
#     inference cannot take zero core cycles, so this needs no threshold and
#     cannot false-positive.
#   * PARTIAL: the counter keeps advancing, but far too slowly. Observed at
#     least once on Apollo4, with DWT running at ~0.6% of the expected rate
#     through an early-boot window. Such a delta is small but non-zero, so it
#     passes the zero test and accumulates uncounted -- which would be worse
#     than silence, because the run would then assert "checked, clean" while
#     still being wrong. The firmware counts these separately, against a floor
#     derived from its own warm reference (an eighth of it; see the
#     clean_stalled_iters declaration in main.cc.j2).
#
# Note the evidence for the frozen shape is an aggregate: #121's table records
# per-run average cycles, which cannot by itself distinguish "N iterations read
# exactly 0" from "a broader set read partially low" -- both fit the same
# deficit total. The bimodal shape is the most likely reading of it, not a
# demonstrated one, which is the other reason both counts exist.
#
# The host judges; the firmware only reports. Same split as
# firmware_window_clock_is_frozen().


def _as_count(value: object) -> int | None:
    """Parse a firmware-reported count, or ``None`` if it is not a number.

    The parser leaves any ``HPX_KEY=value`` it cannot int() as a *string*, so a
    torn transport line (``HPX_CLEAN_PARTIAL_ITERS=1 7``) arrives here as text.
    int() on that raises, and this function is reached from ``evaluate_run()``,
    which would take the whole evaluation down over a corrupt diagnostic field.
    Unparseable is treated as unreported, which the callers already model.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class CleanWindowStall:
    """Clean-window iterations whose DWT delta was zero or implausibly low."""

    #: Deltas of exactly zero -- a frozen counter. Cannot be legitimate.
    stalled_iters: int
    #: Deltas below the firmware's warm-derived floor but non-zero -- a counter
    #: that kept advancing, far too slowly.
    partial_iters: int
    #: ``None`` when the firmware reported no usable iteration count, in which
    #: case the counts are known but their scale is not.
    total_iters: int | None
    #: The warm reference the partial floor was derived from. ``0`` means the
    #: floor was zero and the partial check could not fire at all.
    ref_cycles: int | None = None

    @property
    def affected_iters(self) -> int:
        return self.stalled_iters + self.partial_iters

    @property
    def partial_check_inoperative(self) -> bool:
        """The partial check was compiled in but could not fire.

        The floor is ``ref_cycles >> 3``, so a zero reference makes it zero and
        no unsigned delta is below it. That happens when every warm sample was
        itself frozen -- the documented "usual case" of the very fault this is
        meant to catch -- so it must not read as "checked, clean".
        """
        return self.ref_cycles is not None and self.ref_cycles <= 0

    @property
    def total_is_unknown(self) -> bool:
        return not self.total_iters or self.total_iters <= 0

    @property
    def counts_are_inconsistent(self) -> bool:
        """More affected iterations than the window ran.

        Structurally impossible, so the report itself is corrupt -- a torn
        transport line can inflate one field while the other parses cleanly.
        Still a fault worth raising; just not one whose fractions mean
        anything, so they are clamped rather than published as the >100%
        nonsense a raw division gives.
        """
        if self.total_is_unknown:
            return False
        return self.affected_iters > (self.total_iters or 0)

    @property
    def affected_fraction(self) -> float:
        if self.total_is_unknown:
            return 0.0
        return min(1.0, self.affected_iters / (self.total_iters or 1))

    @property
    def understatement_lower_bound(self) -> float:
        """Minimum fraction by which ``clean_infer_avg_us`` reads low.

        Both shapes contribute, and both bounds are sound:

        * a frozen iteration contributed exactly 0 to a sum that should have
          carried a full inference, so it costs the full ``1/total`` each;
        * a partial iteration contributed *something*, but by construction less
          than an eighth of the warm reference, so it costs at least
          ``0.875/total``.

        Using the frozen count alone made a pure-partial stall report "~0.0%
        low" while every iteration in the window was affected, which is worse
        than saying nothing. Still a lower bound: partials are bounded above by
        the floor, not pinned to it.
        """
        if self.total_is_unknown:
            return 0.0
        lost = self.stalled_iters + 0.875 * self.partial_iters
        return min(1.0, lost / (self.total_iters or 1))

    def to_metadata(self) -> dict[str, float | int | bool | None]:
        metadata: dict[str, float | int | bool | None] = {
            "stalled_iters": self.stalled_iters,
            "partial_iters": self.partial_iters,
            "total_iters": self.total_iters,
            "affected_fraction": round(self.affected_fraction, 6),
            "understatement_lower_bound": round(self.understatement_lower_bound, 6),
        }
        if self.ref_cycles is not None:
            metadata["ref_cycles"] = self.ref_cycles
        if self.counts_are_inconsistent:
            metadata["counts_are_inconsistent"] = True
        if self.total_is_unknown:
            metadata["total_is_unknown"] = True
        if self.partial_check_inoperative:
            metadata["partial_check_inoperative"] = True
        return metadata


def assess_clean_window_stall(
    *,
    stalled_iters: object,
    partial_iters: object,
    clean_infer_count: object,
    ref_cycles: object = None,
) -> CleanWindowStall | None:
    """Report a stalled profile clean window, or ``None`` when there is none.

    ``None`` means "nothing to say": the firmware reported no counts (a window
    that is not DWT-timed per iteration, or firmware predating the check), or
    it reported zero for both AND its partial check was operative. A zero pair
    with a dead floor is NOT healthy -- the check could not have fired -- so
    that case returns a record rather than None.

    Counts are accepted as ``object`` and parsed defensively: the parser hands
    through unparseable ``HPX_*`` values as strings.
    """
    stalled = _as_count(stalled_iters)
    partial = _as_count(partial_iters)
    total = _as_count(clean_infer_count)
    ref = _as_count(ref_cycles)
    if stalled is None and partial is None:
        return None
    stalled = max(0, stalled or 0)
    partial = max(0, partial or 0)
    floor_dead = ref is not None and ref <= 0
    if stalled <= 0 and partial <= 0 and not floor_dead:
        return None
    return CleanWindowStall(
        stalled_iters=stalled,
        partial_iters=partial,
        total_iters=total,
        ref_cycles=ref,
    )


#: Fraction of the expected DWT rate below which the counter is judged broken.
#: The probe times a known nsx_delay_us() interval, so the expected reading is
#: SystemCoreClock * probe_us / 1e6 exactly -- no model behaviour enters it.
#: Half is far below anything nsx_delay_us's own calibration error could
#: produce and far above the observed fault (a frozen counter reads 0; the
#: partial-counting case measured ~0.6% of rate), so the band between "healthy"
#: and "flagged" is roughly two orders of magnitude wide.
DWT_RATE_MIN_RATIO = 0.5


@dataclass(frozen=True)
class CleanWindowClockRate:
    """DWT's measured rate against an independent clock, before the window."""

    measured_cycles: int
    probe_us: int
    system_clock_hz: int

    @property
    def expected_cycles(self) -> float:
        return self.system_clock_hz * self.probe_us / 1_000_000.0

    @property
    def ratio(self) -> float:
        expected = self.expected_cycles
        return self.measured_cycles / expected if expected > 0 else 0.0

    @property
    def is_broken(self) -> bool:
        return self.ratio < DWT_RATE_MIN_RATIO

    def to_metadata(self) -> dict[str, float | int]:
        return {
            "measured_cycles": self.measured_cycles,
            "expected_cycles": round(self.expected_cycles, 1),
            "probe_us": self.probe_us,
            "ratio": round(self.ratio, 6),
            "min_ratio": DWT_RATE_MIN_RATIO,
        }


def assess_clean_window_clock_rate(
    *, rate_cycles: object, probe_us: object, system_clock_hz: object
) -> CleanWindowClockRate | None:
    """Compare the firmware's DWT rate probe against its closed-form expectation.

    This is the only clean-window check whose reference does not come from DWT.
    The in-window counters are DWT-relative and therefore scale-invariant: a
    uniform slowdown moves the warm reference and the counted iterations by the
    same factor and cancels exactly. Returns ``None`` when the firmware did not
    report a probe, or reported one with no usable clock to expect against.
    """
    measured = _as_count(rate_cycles)
    probe = _as_count(probe_us)
    clock = _as_count(system_clock_hz)
    if measured is None or not probe or not clock or probe <= 0 or clock <= 0:
        return None
    return CleanWindowClockRate(
        measured_cycles=max(0, measured),
        probe_us=probe,
        system_clock_hz=clock,
    )


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
    """Host-measured duration of the gated window, with its source name.

    ``gated_windows`` is the ONLY acceptable source. ``summary.duration_s`` is
    numerically identical to it on the gated path (both are summed from the
    same instrument ``dur_ticks`` at time64 resolution), which makes it look
    like a harmless fallback -- it is not. The single code path that leaves
    ``gated_windows`` empty is ``capture_gated.py``'s no-usable-window branch,
    and that branch returns the DEGRADED free-form result whose
    ``summary.duration_s`` is the whole capture window. So the fallback could
    only ever fire in exactly the case where it is wrong.

    That is not hypothetical: on two real degraded Apollo4 artifacts
    (ap4-js110-2, ap4-js110-smoke) the firmware clock was accurate to 0.16%,
    yet comparing it against the 19.2 s free-form capture instead of the ~5 s
    window produced a 73.9% "disagreement" -- a second, misleading issue piled
    on top of the ``power.observation_degraded`` that already described the
    real problem.

    Returns ``None`` when there is no gated window, which callers treat as
    "nothing to compare against" rather than substituting a worse reference.
    """
    windows_total = sum(window.duration_s for window in result.gated_windows)
    if windows_total <= 0:
        return None
    return windows_total, "gated_windows"


@dataclass(frozen=True)
class WindowClockCeiling:
    """Host wall-clock bound on how long the firmware's window could have been.

    The measured window is strictly contained in the interval between the
    moment the host started the power binary and the moment it collected the
    terminal record. A firmware window longer than that whole interval is
    physically impossible, whatever the plan says and whatever board it is.

    Scope, stated honestly: this is a REDUNDANT backstop, not the primary
    internal-mode check. In every shipped flow the 25% plan comparison can run
    wherever this one can -- ``BuildPowerFirmwareStage`` skips unless the plan
    carries ``inference_count``, and the pipeline only ever derives that count
    FROM ``reference_inference_us`` -- so the plan check sees every case this
    one does, and sees it far more sensitively. (The exception is an
    externally-supplied N with no profile phase to measure the reference, which
    the API allows but no shipped caller constructs; there this is the only
    window-clock check there is.)

    What it adds is independence: it is the only check that still works if the
    plan reference is ITSELF corrupted -- a bad profile-phase measurement
    feeding a bad expectation -- because it depends on nothing but two host
    timestamps.

    It is also blunt. The envelope deliberately over-counts -- it includes
    flash-tool exit, boot, engine/model init, and the post-window terminal emit
    and collect wait -- so a window has to be inflated by roughly 2-3x before it
    breaches, and more than that when the window is a small fraction of the
    phase. It will not notice a 30% error. Treat a breach as conclusive and
    silence as uninformative.
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


#: ``no_gate_rise`` hint for a run that had lock-step OFF on a board wired for
#: it. This exact combination has a specific, non-wiring cause that presents as
#: a wiring fault and has cost real bench time (issue #114: headers re-seated
#: on an Apollo4 Blue Plus before the policy flag was found). Without
#: lock-step, ``kSyncLockstep`` bakes false, ``hpx_sync_wait_go()`` compiles to
#: a no-op, and the target free-runs its measured window straight out of reset
#: -- so the gate can rise, and on a multi-second window also fall, before the
#: host's GPI poller is armed. The fix names itself, so it goes first; the
#: wiring checks stay as the fallback because a genuinely dead gate wire looks
#: identical from here.
NO_GATE_RISE_LOCKSTEP_HINT = (
    "This capture ran with power.lockstep disabled while the state/GO pins ARE "
    "configured, which is the likeliest cause: with lock-step off the firmware "
    "never waits for the host, so it can open AND close its measured window "
    "before the Joulescope GPI poller is armed. Set power.lockstep: true — or "
    "drop an explicit power.lockstep: false and let it auto-enable. If the gate "
    "is still missed with lock-step on, then check GO/state/gate wiring, "
    "confirm the firmware reached the power window wait state, and verify the "
    "selected reset strategy relaunches the firmware before capture."
)

#: ``no_gate_rise`` hint when lock-step cannot be the explanation -- it was
#: already on, or the board has no state/GO wires to run it over.
NO_GATE_RISE_WIRING_HINT = (
    "Check GO/state/gate wiring, confirm the firmware reached the power "
    "window wait state, and verify the selected reset strategy relaunches "
    "the firmware before capture."
)


def classify_gate_failure(
    *,
    saw_gate_rise: bool,
    saw_gate_fall: bool = False,
    duration_s: float,
    lockstep: bool | None = None,
    lockstep_wiring_available: bool = False,
) -> GateFailure:
    """Classify why a gated capture produced no complete high window.

    ``lockstep`` is the effective handshake state for this run and
    ``lockstep_wiring_available`` whether the board carries the state/GO wires
    at all (see :attr:`PowerConfig.lockstep_wiring_available`, the single
    source of that predicate). Together they select which ``no_gate_rise``
    hint applies. ``lockstep=None`` means the caller does not know, which
    keeps the wiring-only hint.
    """
    if not saw_gate_rise:
        lockstep_is_the_suspect = lockstep is False and lockstep_wiring_available
        return GateFailure(
            kind=GateFailureKind.NO_GATE_RISE,
            message=(
                "No GPIO gate rising edge detected during Joulescope gated capture"
                + (
                    " (lock-step is disabled but this board is wired for it — "
                    "power.lockstep: true is the likely fix)"
                    if lockstep_is_the_suspect
                    else ""
                )
            ),
            hint=(
                NO_GATE_RISE_LOCKSTEP_HINT
                if lockstep_is_the_suspect
                else NO_GATE_RISE_WIRING_HINT
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
    "NO_GATE_RISE_LOCKSTEP_HINT",
    "NO_GATE_RISE_WIRING_HINT",
    "WINDOW_CLOCK_CEILING_SLACK_S",
    "CleanWindowClockRate",
    "CleanWindowStall",
    "DWT_RATE_MIN_RATIO",
    "GateDurationIntegrity",
    "GateFailure",
    "GateFailureKind",
    "GateTransitionTiming",
    "SyncHandshakeMetadata",
    "WindowClockAgreement",
    "WindowClockCeiling",
    "assess_clean_window_clock_rate",
    "assess_clean_window_stall",
    "assess_gate_duration",
    "assess_run_window_clock",
    "assess_window_clock",
    "assess_window_clock_ceiling",
    "classify_gate_failure",
    "firmware_window_clock_is_frozen",
    "gated_window_reference_s",
    "window_clock_ceiling_from_metadata",
]
