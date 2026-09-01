"""Profile clean-window clock integrity.

Extracted from ``power.diagnostics`` at its size ceiling; the module boundary
follows the responsibility boundary the section header already drew there.
``power.diagnostics`` re-exports every public name (and ``_as_count``) so all
existing import sites keep working.

The checks in ``power.diagnostics`` police the *power* binary's window clock.
These police the clock that measures the PROFILE binary's clean window -- the
number published as clean_infer_avg_us, which is also the reference the power
plan sizes its window from (stages/plan_power.py). It is a different failure
with a different signature, which is why it is a different check:

  * frozen power clock: elapsed_us == 0, or off by a large factor. Loud.
  * stalled profile clock: the window loses a *sub-interval*, so the average
    comes back some percentage low. It is never zero, never inverted, and
    lands squarely inside every plausible range -- 21% low on the Apollo4
    runs in #121, against a 3.9% legitimate build-to-build spread. Nothing
    downstream could tell.

Detection does not need a reference measurement, because the firmware reports
the fault directly. It reports TWO counts, because a dropped debug domain has
been seen doing two different things to the counter:

  * FROZEN (the usual case, and what #121 measured): CYCCNT stops, so every
    iteration wholly inside the stall reads a delta of exactly zero. An
    inference cannot take zero core cycles, so this needs no threshold and
    cannot false-positive.
  * PARTIAL: the counter keeps advancing, but far too slowly. Observed at
    least once on Apollo4, with DWT running at ~0.6% of the expected rate
    through an early-boot window. Such a delta is small but non-zero, so it
    passes the zero test and accumulates uncounted -- which would be worse
    than silence, because the run would then assert "checked, clean" while
    still being wrong. The firmware counts these separately, against a floor
    derived from its own warm reference (an eighth of it; see the
    clean_stalled_iters declaration in main.cc.j2).

Note the evidence for the frozen shape is an aggregate: #121's table records
per-run average cycles, which cannot by itself distinguish "N iterations read
exactly 0" from "a broader set read partially low" -- both fit the same
deficit total. The bimodal shape is the most likely reading of it, not a
demonstrated one, which is the other reason both counts exist.

The host judges; the firmware only reports. Same split as
firmware_window_clock_is_frozen().
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..pipeline import PipelineContext


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


def window_inference_count(ctx: "PipelineContext") -> int | None:
    """Return the inferences that ran inside the measured power window (#240).

    The one resolution of the window's inference count -- the denominator
    every per-inference power metric (energy/inference, TOPS) and the
    gate-duration check divide by. Returns ``None`` when the scope has no
    trustworthy inference window, so callers suppress the metric rather
    than price a whole free-run as one inference:

    * ``gpio_gated_clean_window`` -- the power PLAN's count (the gated
      window's own N), falling back to the profile phase's
      ``clean_infer_count`` only when the plan carries none (shared
      firmware); these differ under ``window_mode: fixed``.
    * ``on_device_gated_inference`` -- the on-target monitor's own
      ``inference_count`` (its integration bracket IS those N).
    * ``free_form_capture`` / ``whole_capture_window`` / anything else --
      ``None`` (no inference-bracketed window).

    A ``busy_loop`` probe runs zero model inferences; callers must gate on
    :func:`probe_runs_inferences` separately -- a real count here does not
    mean model ops executed.
    """
    from .metadata import MeasurementScope  # local: metadata imports this module

    result = ctx.power_result
    if result is None:
        return None
    scope = result.metadata.measurement_scope
    if scope is MeasurementScope.ON_DEVICE_GATED_INFERENCE:
        count = result.metadata.inference_count
        return count if count and count > 0 else None
    if scope is MeasurementScope.GPIO_GATED_CLEAN_WINDOW:
        plan = result.metadata.power_plan
        if isinstance(plan, dict):
            planned = plan.get("inference_count")
            if isinstance(planned, int) and not isinstance(planned, bool) and planned > 0:
                return planned
        meta = ctx.pmu_result.meta if ctx.pmu_result is not None else None
        count = meta.clean_infer_count if meta is not None else None
        return count if count and count > 0 else None
    return None
