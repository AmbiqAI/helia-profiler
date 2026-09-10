"""The gate's width must not ride on a filter the driver is still fitting (#249).

A gated window's duration is the sum of its stat packets' durations. Those were
measured as `u1 - u0` on the `utc` field, which is not a device timestamp:
jsdrv fits a sample-counter-to-UTC map while streaming and publishes it in every
packet as `time.time_map.counter_rate`. Measured on a JS320 that rate read
15,849,906 Hz against a nameplate 16,000,000 -- 9470 ppm low -- and every
packet's `u1 - u0` was 9469 ppm long to match. Early in a session the same fit
was 2.9 % out, which is 143 ms on a 5 s window.

The same packet carries the counter span, which is exact.
"""

from __future__ import annotations

import pytest

from helia_profiler.power.joulescope.stats import (
    _counter_rate_ratio,
    _packet_duration_ticks,
    _process_gated_stats,
    _stats_arrays,
)

pytest.importorskip("numpy")
time64 = pytest.importorskip("pyjoulescope_driver.time64")

NAMEPLATE = 16_000_000.0
#: The rate jsdrv had fitted on the bench when this was diagnosed.
MEASURED_COUNTER_RATE = 15_849_906.047525965
#: Its error early in a session, when #249's 143 ms window came from.
COLD_COUNTER_RATE = NAMEPLATE / 1.0286


def _packet(
    *,
    index: int,
    duration_s: float = 0.002,
    counter_rate: float = MEASURED_COUNTER_RATE,
    current_a: float = 0.004,
    with_samples: bool = True,
    with_time_map: bool = True,
):
    """One stats packet whose utc is scaled by the filter, as the device sends it."""
    span = duration_s * NAMEPLATE
    s0 = 13_043_307_285_148 + int(index * span)
    # utc is the counter mapped through the fitted rate, so it inherits its error.
    u0 = 294_550_940_184_150_708 + int(index * span * time64.SECOND / counter_rate)
    u1 = u0 + int(span * time64.SECOND / counter_rate)
    time_block: dict = {"utc": {"value": [u0, u1]}}
    if with_time_map:
        time_block["time_map"] = {
            "counter_rate": counter_rate,
            "offset_time": u0,
            "offset_counter": s0,
        }
    if with_samples:
        time_block["samples"] = {"value": [s0, s0 + int(span)]}
        time_block["sample_freq"] = {"value": NAMEPLATE}
        # The driver states the same duration itself; carried so a test can
        # check the recomputation against the instrument rather than against
        # the same arithmetic run twice.
        time_block["delta"] = {"value": duration_s}
        time_block["decimate_factor"] = {"value": 16}
    return {
        "time": time_block,
        "_host_time64": u0,
        "signals": {
            "current": {
                "avg": {"value": current_a},
                "max": {"value": current_a},
                "min": {"value": current_a},
                "integral": {"value": current_a * duration_s},
            },
            "power": {
                "avg": {"value": current_a * 1.8},
                "integral": {"value": current_a * 1.8 * duration_s},
            },
        },
    }


# ---------------------------------------------------------------------------
# One packet
# ---------------------------------------------------------------------------


def test_a_packets_duration_comes_from_its_counter_span():
    p = _packet(index=0)
    t = p["time"]
    u0, u1 = (float(v) for v in t["utc"]["value"])

    ticks = _packet_duration_ticks(t, u0, u1, time64.SECOND)

    assert ticks / time64.SECOND == pytest.approx(0.002, rel=1e-12)


def test_the_utc_span_of_that_same_packet_is_long_by_the_filters_error():
    """Pins the defect, so a regression restores a number that is provably wrong."""
    p = _packet(index=0)
    u0, u1 = (float(v) for v in p["time"]["utc"]["value"])

    assert (u1 - u0) / time64.SECOND == pytest.approx(0.002 * 1.009469, rel=1e-5)


def test_a_packet_without_a_counter_span_falls_back_to_utc():
    p = _packet(index=0, with_samples=False)
    t = p["time"]
    u0, u1 = (float(v) for v in t["utc"]["value"])

    assert _packet_duration_ticks(t, u0, u1, time64.SECOND) == u1 - u0


@pytest.mark.parametrize(
    "block",
    [
        pytest.param({}, id="no_samples"),
        pytest.param({"samples": {"value": [5]}}, id="one_endpoint"),
        pytest.param(
            {"samples": {"value": [5, 5]}, "sample_freq": {"value": 16e6}}, id="zero_span"
        ),
        pytest.param({"samples": {"value": [5, 9]}, "sample_freq": {"value": 0}}, id="zero_freq"),
        pytest.param({"samples": {"value": ["a", "b"]}, "sample_freq": {"value": 16e6}}, id="junk"),
    ],
)
def test_an_unusable_counter_span_falls_back_rather_than_returning_nonsense(block):
    assert _packet_duration_ticks(block, 100.0, 350.0, time64.SECOND) == 250.0


# ---------------------------------------------------------------------------
# The window built from them
# ---------------------------------------------------------------------------


def _window(packets, *, bounds=None):
    """Return the GatedPowerWindow, which is what production consumes.

    `gated_window_reference_s` sums `GatedPowerWindow.duration_s`, and its
    docstring is explicit that `summary.duration_s` only looks like a harmless
    stand-in. Assert the side that is read.
    """
    mid = [0.5 * sum(float(v) for v in p["time"]["utc"]["value"]) for p in packets]
    windows = [bounds] if bounds else [(min(mid) - 1, max(mid) + 1)]
    gated, summary = _process_gated_stats(
        packets=packets,
        poll_samples=[],
        io_voltage=1.8,
        prefer_device_time=True,
        minimum_window_s=0.0,
        windows_override=windows,
    )
    assert gated, "no window produced"
    assert gated[0].duration_s == pytest.approx(summary.duration_s, rel=1e-12)
    return gated[0]


def test_a_five_second_window_is_five_seconds_whatever_the_filter_says():
    """The #249 headline: a 2.86 % filter error must not widen the gate."""
    packets = [_packet(index=i, counter_rate=COLD_COUNTER_RATE) for i in range(2500)]

    summary = _window(packets)

    assert summary.duration_s == pytest.approx(5.0, rel=1e-9)


def test_the_same_window_measured_on_utc_would_have_read_143_ms_long():
    packets = [
        _packet(index=i, counter_rate=COLD_COUNTER_RATE, with_samples=False) for i in range(2500)
    ]

    summary = _window(packets)

    assert summary.duration_s == pytest.approx(5.143, abs=1e-3)


def test_average_power_and_current_no_longer_inherit_the_error():
    """Both divide by the window, so both were understated by the same factor."""
    packets = [
        _packet(index=i, counter_rate=COLD_COUNTER_RATE, current_a=0.004) for i in range(500)
    ]

    summary = _window(packets)

    assert summary.avg_current_a == pytest.approx(0.004, rel=1e-9)
    assert summary.avg_power_w == pytest.approx(0.004 * 1.8, rel=1e-9)


def test_the_window_is_unchanged_when_the_filter_is_already_converged():
    """No silent shift on a healthy capture: a converged fit must be a no-op."""
    packets = [_packet(index=i, counter_rate=NAMEPLATE) for i in range(1000)]

    summary = _window(packets)

    assert summary.duration_s == pytest.approx(2.0, rel=1e-9)


def test_energy_is_untouched_because_the_device_integrated_it():
    packets = [_packet(index=i, counter_rate=COLD_COUNTER_RATE) for i in range(500)]

    summary = _window(packets)

    assert summary.energy_j == pytest.approx(500 * 0.004 * 1.8 * 0.002, rel=1e-9)


def test_a_scale_error_cancels_out_of_the_selection_at_a_real_boundary():
    """The load-bearing claim: edges and midpoints share the utc axis, so a
    boundary drawn on that axis selects the same packets however far the fit
    has drifted. Asserting monotonic midpoints would prove nothing -- this
    draws a boundary mid-capture and counts what falls inside.
    """
    selected = {}
    for label, rate in (("converged", NAMEPLATE), ("drifting", COLD_COUNTER_RATE)):
        packets = [_packet(index=i, counter_rate=rate) for i in range(200)]
        # A boundary placed at the same COUNTER position in both captures:
        # packet 50's start and packet 150's end, expressed on each capture's
        # own utc axis, exactly as an edge derived from that axis would be.
        lo = float(packets[50]["time"]["utc"]["value"][0])
        hi = float(packets[149]["time"]["utc"]["value"][1])
        window = _window(packets, bounds=(lo, hi))
        selected[label] = window.duration_s

    assert selected["converged"] == pytest.approx(100 * 0.002, rel=1e-9)
    assert selected["drifting"] == pytest.approx(selected["converged"], rel=1e-9)


# ---------------------------------------------------------------------------
# The diagnostic
# ---------------------------------------------------------------------------


def test_the_filters_error_is_published_as_a_ratio():
    d = _counter_rate_ratio([_packet(index=i) for i in range(5)])

    assert d is not None
    assert d["counter_rate_hz"] == pytest.approx(MEASURED_COUNTER_RATE)
    assert d["sample_freq_hz"] == pytest.approx(NAMEPLATE)
    assert d["utc_over_counter_rate"] == pytest.approx(1.009469, rel=1e-5)
    assert d["packets_with_time_map"] == 5


def test_a_converged_filter_reports_unity():
    d = _counter_rate_ratio([_packet(index=i, counter_rate=NAMEPLATE) for i in range(3)])

    assert d is not None
    assert d["utc_over_counter_rate"] == pytest.approx(1.0, rel=1e-12)


def test_packets_without_a_time_map_report_nothing_rather_than_a_default():
    packets = [_packet(index=i) for i in range(3)]
    for p in packets:
        del p["time"]["time_map"]

    assert _counter_rate_ratio(packets) is None


# ---------------------------------------------------------------------------
# A fit that moves during the capture -- the case the diagnostic exists for
# ---------------------------------------------------------------------------


def _converging(count: int, *, start=COLD_COUNTER_RATE, end=NAMEPLATE):
    """A capture whose fit sweeps from `start` to `end`, as a real one does."""
    return [
        _packet(index=i, counter_rate=start + (end - start) * i / max(1, count - 1))
        for i in range(count)
    ]


def test_the_duration_is_right_even_while_the_fit_is_still_moving():
    """The regime no fixed-rate test reaches: the correction is per packet, so
    a sweeping fit must not leave a residue in the total."""
    packets = _converging(1000)

    assert _window(packets).duration_s == pytest.approx(2.0, rel=1e-9)


def test_a_capture_that_converged_halfway_does_not_report_itself_settled():
    """A median over this returns exactly 1.000000 and hides the sweep -- the
    defect this diagnostic had when first written."""
    packets = [_packet(index=i, counter_rate=COLD_COUNTER_RATE) for i in range(50)]
    packets += [_packet(index=50 + i, counter_rate=NAMEPLATE) for i in range(51)]

    d = _counter_rate_ratio(packets)

    assert d is not None
    assert d["counter_rate_swept_by"] == pytest.approx(0.0286, rel=1e-3)
    assert d["utc_over_counter_rate_first"] == pytest.approx(1.0286, rel=1e-6)
    assert d["utc_over_counter_rate_last"] == pytest.approx(1.0, rel=1e-9)
    assert d["utc_over_counter_rate_max"] > d["utc_over_counter_rate_min"]


def test_a_steady_fit_reports_no_sweep():
    d = _counter_rate_ratio([_packet(index=i, counter_rate=NAMEPLATE) for i in range(20)])

    assert d is not None
    assert d["counter_rate_swept_by"] == pytest.approx(0.0, abs=1e-12)


def test_the_reported_ratio_is_not_dragged_by_one_outlying_packet():
    """Median, not mean: a single bad fit in a settled capture is noise."""
    packets = [_packet(index=i, counter_rate=NAMEPLATE) for i in range(40)]
    packets[7] = _packet(index=7, counter_rate=NAMEPLATE / 2.0)

    d = _counter_rate_ratio(packets)

    assert d is not None
    assert d["utc_over_counter_rate"] == pytest.approx(1.0, rel=1e-9)
    assert d["utc_over_counter_rate_max"] == pytest.approx(2.0, rel=1e-9)


def test_partial_time_map_coverage_is_reported_as_a_fraction_not_a_count():
    """`packets_with_time_map` exists to say the coverage was partial; it must
    count the packets that HAD one, against the total."""
    packets = [_packet(index=i) for i in range(6)]
    packets += [_packet(index=6 + i, with_time_map=False) for i in range(4)]

    d = _counter_rate_ratio(packets)

    assert d is not None
    assert d["packets_with_time_map"] == 6
    assert d["packets_total"] == 10


# ---------------------------------------------------------------------------
# Cross-checks against the instrument's own statement
# ---------------------------------------------------------------------------


def test_the_recomputed_duration_matches_the_delta_the_driver_reports():
    """The strongest available pin: `time.delta` is the driver's own statement
    of the same quantity, so this checks the fix against the instrument rather
    than against the same arithmetic run twice."""
    for duration_s in (0.002, 0.001, 0.005):
        p = _packet(index=0, duration_s=duration_s)
        t = p["time"]
        u0, u1 = (float(v) for v in t["utc"]["value"])

        ticks = _packet_duration_ticks(t, u0, u1, time64.SECOND)

        assert ticks / time64.SECOND == pytest.approx(t["delta"]["value"], rel=1e-12)


def test_a_decreasing_counter_pair_falls_back_instead_of_going_negative():
    """A negative duration does not merely misreport: `_process_gated_stats`
    drops a window whose duration is <= 0, so the run would lose the gate
    entirely rather than fall back to utc."""
    t = {
        "samples": {"value": [900, 100]},
        "sample_freq": {"value": NAMEPLATE},
        "utc": {"value": [0, 250]},
    }

    assert _packet_duration_ticks(t, 100.0, 350.0, time64.SECOND) == 250.0


# ---------------------------------------------------------------------------
# The driver's own divisor, and the cross-check path
# ---------------------------------------------------------------------------


def test_delta_is_preferred_because_it_is_the_integrals_own_divisor():
    """jsdrv builds `charge` and `energy` as `avg * delta`. Taking the same
    `delta` back makes `energy_j / duration_s` exact by construction; deriving
    it again from `samples` would be a reimplementation that can drift."""
    p = _packet(index=0, duration_s=0.002)
    t = p["time"]
    # Make the counter span disagree with delta: whichever the code prefers
    # is then visible in the result.
    t["samples"] = {"value": [0, int(0.003 * NAMEPLATE)]}
    u0, u1 = (float(v) for v in t["utc"]["value"])

    ticks = _packet_duration_ticks(t, u0, u1, time64.SECOND)

    assert ticks / time64.SECOND == pytest.approx(0.002, rel=1e-12)


def test_a_packet_without_delta_still_uses_its_counter_span():
    p = _packet(index=0, duration_s=0.002)
    t = p["time"]
    del t["delta"]
    u0, u1 = (float(v) for v in t["utc"]["value"])

    assert _packet_duration_ticks(t, u0, u1, time64.SECOND) / time64.SECOND == pytest.approx(
        0.002, rel=1e-12
    )


@pytest.mark.parametrize("bad", [0, -0.002, "x", None])
def test_an_unusable_delta_falls_through_to_the_counter_span(bad):
    p = _packet(index=0, duration_s=0.002)
    t = p["time"]
    t["delta"] = {"value": bad}
    u0, u1 = (float(v) for v in t["utc"]["value"])

    assert _packet_duration_ticks(t, u0, u1, time64.SECOND) / time64.SECOND == pytest.approx(
        0.002, rel=1e-12
    )


def test_the_fullrate_cross_check_counts_samples_rather_than_measuring_edges():
    """The full-rate path is what you reach for to corroborate the gated one.
    Its samples are spaced at the nameplate rate while its window edges are on
    the fitted utc axis, so measuring the edges would leave it disagreeing with
    the gated result by exactly the error this fix removes.
    """
    import numpy as np

    from helia_profiler.power.joulescope.stats import _fullrate_energy_over_windows

    sr = 1_000_000.0
    n = 20_000
    # Poll edges land on the utc axis, which here runs 2.86 % fast.
    scale = 1.0286
    rise = int(0.005 * time64.SECOND * scale)
    fall = int(0.015 * time64.SECOND * scale)

    # Two anchors, so the sample->utc conversion can be taken from the data
    # rather than from the nameplate rate, putting samples on the same axis as
    # the edges.
    out = _fullrate_energy_over_windows(
        cur_chunks=[np.full(n, 0.004, dtype=np.float32)],
        volt_chunks=[np.full(n, 1.8, dtype=np.float32)],
        anchors=[(0, 0, sr), (n - 1, int((n - 1) / sr * time64.SECOND * scale), sr)],
        poll_samples=[(0, 0), (rise, 1), (fall, 0)],
    )

    assert out is not None
    window = out["windows"][0]
    # 10 ms of real samples at 1 MSPS, whatever the fitted axis claims.
    assert window["duration_s"] == pytest.approx(0.010, rel=2e-3)
    assert window["mean_current_a"] == pytest.approx(0.004, rel=1e-6)
    assert window["charge_c"] == pytest.approx(0.004 * 0.010, rel=2e-3)


def test_the_fullrate_cross_check_falls_back_to_nameplate_with_one_anchor():
    """A single anchor cannot state the conversion, so the nameplate rate
    stands -- and the window is then as wide as the fitted axis says."""
    import numpy as np

    from helia_profiler.power.joulescope.stats import _fullrate_energy_over_windows

    sr = 1_000_000.0
    n = 20_000
    rise = int(0.005 * time64.SECOND)
    fall = int(0.015 * time64.SECOND)

    out = _fullrate_energy_over_windows(
        cur_chunks=[np.full(n, 0.004, dtype=np.float32)],
        volt_chunks=[np.full(n, 1.8, dtype=np.float32)],
        anchors=[(0, 0, sr)],
        poll_samples=[(0, 0), (rise, 1), (fall, 0)],
    )

    assert out is not None
    assert out["windows"][0]["duration_s"] == pytest.approx(0.010, rel=2e-3)
