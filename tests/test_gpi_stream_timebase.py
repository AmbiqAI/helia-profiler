"""The streamed-GPI time base is derived, so it has to be observable (#249).

`_segment_streamed_gpi` places both gate edges with a per-sample spacing
inferred from frame timestamps, because the JS320 reports its raw sample rate
while delivering decimated samples. When a gate window disagrees with the
firmware clock, that inference is the first suspect — and until now nothing
recorded what it saw.
"""

from __future__ import annotations

import pytest

from helia_profiler.power.joulescope.stats import streamed_gpi_timebase

pytest.importorskip("numpy")
time64 = pytest.importorskip("pyjoulescope_driver.time64")


def _frames(spacings_per_sample: list[float], samples: int = 8, rate: float = 2_000_000.0):
    """Frames whose utc advances by the given per-sample spacing each time."""
    frames, utc = [], 1_000_000.0
    for spacing in spacings_per_sample:
        frames.append({"utc": utc, "rate": rate, "data": [0] * samples})
        utc += spacing * samples
    frames.append({"utc": utc, "rate": rate, "data": [0] * samples})
    return frames


def test_a_steady_stream_reports_no_spread():
    tick = time64.SECOND / 250_000.0
    d = streamed_gpi_timebase(_frames([tick] * 20))

    assert d["frame_count"] == 21
    assert d["tick_per_sample"] == pytest.approx(tick)
    assert d["tick_per_sample_source"] == "median_frame_spacing"
    assert d["spacing_relative_spread"] == pytest.approx(0.0)
    assert d["implied_rate_hz"] == pytest.approx(250_000.0)


def test_the_reported_rate_is_compared_against_what_the_frames_imply():
    """The instrument's 8:1 decimation must be visible, not silently absorbed."""
    tick = time64.SECOND / 250_000.0
    d = streamed_gpi_timebase(_frames([tick] * 10, rate=2_000_000.0))

    assert d["reported_rate_hz"] == 2_000_000.0
    assert d["implied_rate_hz"] == pytest.approx(250_000.0)
    assert d["reported_over_implied_rate"] == pytest.approx(8.0)


def test_a_dropped_frame_skews_the_mean_but_not_the_median():
    """The median is chosen precisely to survive frame drops.

    One dropped frame doubles a single spacing. A mean would carry that into
    every edge placement in the capture; the median must not, and the spread
    must still report that the input was not steady.
    """
    tick = time64.SECOND / 250_000.0
    with_a_gap = [tick] * 8 + [tick * 9.0] + [tick] * 8

    d = streamed_gpi_timebase(_frames(with_a_gap))

    assert d["tick_per_sample"] == pytest.approx(tick)
    # A mean over the same input lands far away; this pins the median choice.
    assert d["tick_per_sample"] < sum(with_a_gap) / len(with_a_gap) * 0.9
    assert d["spacing_relative_spread"] > 7.0
    assert d["spacing_max_tick"] == pytest.approx(tick * 9.0)


def test_frames_that_carry_no_samples_are_counted_not_hidden():
    tick = time64.SECOND / 250_000.0
    frames = _frames([tick] * 5)
    frames.insert(2, {"utc": 0.0, "rate": 2_000_000.0, "data": []})

    d = streamed_gpi_timebase(frames)

    assert d["dropped_or_empty_frames"] == 1
    assert d["frame_count"] == 6


def test_a_single_frame_falls_back_to_the_reported_rate_and_says_so():
    """With no consecutive pair there is nothing to derive from."""
    d = streamed_gpi_timebase([{"utc": 1.0, "rate": 2_000_000.0, "data": [0] * 8}])

    assert d["tick_per_sample_source"] == "reported_rate"
    assert d["tick_per_sample"] == pytest.approx(time64.SECOND / 2_000_000.0)


def test_no_frames_reports_an_empty_capture_rather_than_raising():
    assert streamed_gpi_timebase([]) == {"frame_count": 0}
