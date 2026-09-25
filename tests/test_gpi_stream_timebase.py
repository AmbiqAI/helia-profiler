"""The streamed-GPI time base is derived, so it has to be observable (#249).

`_segment_streamed_gpi` places both gate edges with a per-sample spacing
inferred from frame timestamps. When a gate window disagrees with the
firmware clock, that inference is the first suspect, so these tests pin
what it saw.
"""

from __future__ import annotations

import pytest

from helia_profiler.power.joulescope.stats import _streamed_gpi_timebase

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
    d = _streamed_gpi_timebase(_frames([tick] * 20))

    assert d["frame_count"] == 21
    assert d["tick_per_sample"] == pytest.approx(tick)
    assert d["tick_per_sample_source"] == "median_frame_spacing"
    assert d["spacing_relative_spread"] == pytest.approx(0.0)
    assert d["implied_rate_hz"] == pytest.approx(250_000.0)


def test_the_reported_rate_is_compared_against_what_the_frames_imply():
    """A rate the frame timestamps contradict must be visible."""
    tick = time64.SECOND / 250_000.0
    d = _streamed_gpi_timebase(_frames([tick] * 10, rate=2_000_000.0))

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

    d = _streamed_gpi_timebase(_frames(with_a_gap))

    assert d["tick_per_sample"] == pytest.approx(tick)
    assert d["tick_per_sample"] < sum(with_a_gap) / len(with_a_gap) * 0.9
    assert d["spacing_relative_spread"] > 7.0
    assert d["spacing_max_tick"] == pytest.approx(tick * 9.0)


def test_frames_that_carry_no_samples_are_counted_not_hidden():
    tick = time64.SECOND / 250_000.0
    frames = _frames([tick] * 5)
    frames.insert(2, {"utc": 0.0, "rate": 2_000_000.0, "data": []})

    d = _streamed_gpi_timebase(frames)

    assert d["dropped_or_empty_frames"] == 1
    assert d["frame_count"] == 6


def test_a_single_frame_falls_back_to_the_reported_rate_and_says_so():
    """With no consecutive pair there is nothing to derive from."""
    d = _streamed_gpi_timebase([{"utc": 1.0, "rate": 2_000_000.0, "data": [0] * 8}])

    assert d["tick_per_sample_source"] == "reported_rate"
    assert d["tick_per_sample"] == pytest.approx(time64.SECOND / 2_000_000.0)


def test_no_frames_reports_an_empty_capture_rather_than_raising():
    assert _streamed_gpi_timebase([]) == {"frame_count": 0}


def test_frame_spacing_divides_by_the_frame_it_started_from():
    """Uneven frames are the only shape that can tell the two apart.

    Frame `cur` begins at its own utc and holds `N_cur` samples, so the next
    frame's first sample sits at `utc_cur + N_cur * spacing`. Dividing by the
    NEXT frame's count gives the same answer whenever sizes are equal, which
    every other fixture here is — only uneven sizes pin this expression.
    """
    from helia_profiler.power.joulescope.stats import _frame_spacings

    tick = time64.SECOND / 125_000.0
    sizes = [8, 12, 8, 20]
    frames, utc = [], 1_000_000.0
    for n in sizes:
        frames.append({"utc": utc, "rate": 125_000.0, "data": [0] * n})
        utc += tick * n

    spacings = _frame_spacings(frames)

    assert len(spacings) == len(sizes) - 1
    assert all(s == pytest.approx(tick, rel=1e-9) for s in spacings)


def test_the_segmenter_places_edges_correctly_across_uneven_frames():
    """The same expression, on the production path that decides a gate."""
    from helia_profiler.power.joulescope.stats import _segment_streamed_gpi

    tick = time64.SECOND / 125_000.0
    frames, utc = [], 1_000_000.0
    for levels in ([0] * 8, [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0], [0] * 8):
        frames.append({"utc": utc, "rate": 125_000.0, "data": levels})
        utc += tick * len(levels)

    windows = _segment_streamed_gpi(frames)

    assert len(windows) == 1
    rise, fall = windows[0]
    assert (fall - rise) == pytest.approx(10 * tick, rel=1e-9)


@pytest.mark.parametrize("rate, data", [(1.0, []), (0.0, [0]), (-1.0, [0])])
def test_a_wholly_unusable_stream_reports_its_discarded_input(rate, data):
    assert _streamed_gpi_timebase([{"utc": 0.0, "rate": rate, "data": data}]) == {
        "frame_count": 0,
        "dropped_or_empty_frames": 1,
    }


@pytest.mark.parametrize("bad_rate", [0.0, -1.0, float("inf"), float("nan")])
def test_excluded_frame_does_not_bridge_spacing_or_gate_edges(bad_rate):
    from helia_profiler.power.joulescope.stats import _segment_streamed_gpi

    tick = time64.SECOND / 1000
    frames = [
        {"utc": 0, "rate": 1000.0, "data": [0, 1, 1, 1, 1, 1, 1, 1]},
        {"utc": 8 * tick, "rate": bad_rate, "data": [1] * 8},
        {"utc": 16 * tick, "rate": 1000.0, "data": [1, 0, 1, 1, 0, 0, 0, 0]},
    ]
    d = _streamed_gpi_timebase(frames)
    assert d["spacing_sample_count"] == 0
    assert d["tick_per_sample"] == tick
    assert d["tick_per_sample_source"] == "reported_rate"
    assert d["dropped_or_empty_frames"] == 1
    assert _segment_streamed_gpi(frames) == [(18 * tick, 20 * tick)]


def test_gpi_bytes_unpack_earliest_sample_first():
    """The driver packs uint1 GPI samples 8 per byte, LSB first."""
    from helia_profiler.power.joulescope.stats import _unpack_gpi_levels

    # 0b11100000: a rise after the fifth sample; 0b00000111: a fall after the third.
    levels = _unpack_gpi_levels([0x00, 0xE0, 0xFF, 0x07])

    assert levels.tolist() == [0] * 8 + [0] * 5 + [1] * 3 + [1] * 8 + [1] * 3 + [0] * 5


def test_unpacked_frames_imply_the_reported_rate():
    """Packed bytes read as samples would imply one eighth of it."""
    import numpy as np

    from helia_profiler.power.joulescope.stats import _unpack_gpi_levels

    rate = 1_000_000.0
    samples_per_frame = 8 * 6396
    tick = time64.SECOND / rate
    frames = [
        {
            "utc": i * samples_per_frame * tick,
            "rate": rate,
            "data": _unpack_gpi_levels(np.zeros(samples_per_frame // 8, dtype=np.uint8)),
        }
        for i in range(4)
    ]

    d = _streamed_gpi_timebase(frames)

    assert d["implied_rate_hz"] == pytest.approx(rate)
    assert d["reported_over_implied_rate"] == pytest.approx(1.0)


def test_a_single_packed_frame_keeps_mid_byte_edges():
    """One frame has no spacing pair, so the reported rate places edges."""
    import numpy as np

    from helia_profiler.power.joulescope.stats import (
        _segment_streamed_gpi,
        _unpack_gpi_levels,
    )

    rate = 1_000_000.0
    levels = np.zeros(4000, dtype=np.uint8)
    levels[1003:3005] = 1
    packed = np.packbits(levels, bitorder="little")
    frames = [{"utc": 0, "rate": rate, "data": _unpack_gpi_levels(packed)}]

    ((rise, fall),) = _segment_streamed_gpi(frames)

    tick = time64.SECOND / rate
    assert rise == pytest.approx(1003 * tick)
    assert fall == pytest.approx(3005 * tick)
