"""Tests for capture/transport.py — heartbeat-aware line collection."""

from __future__ import annotations

import re

from helia_profiler.transport.protocol import (
    WINDOW_BUDGET_CAP_S,
    WINDOW_BUDGET_MARGIN_S,
    WINDOW_BUDGET_SAFETY,
    collect_lines,
    window_budget_s,
)


def _canned_reader(chunks: list[bytes]):
    """Return a read_fn that yields each chunk on successive calls, then b''."""
    it = iter(chunks + [b""] * 1000)

    def read() -> bytes:
        return next(it)

    return read


def test_collect_lines_returns_on_hpx_end():
    read = _canned_reader(
        [
            b"--- HPX_START ---\n",
            b"HPX_VERSION=1\n",
            b"--- HPX_END ---\n",
        ]
    )
    lines = collect_lines(read, transport_name="TEST")
    assert lines[0] == "--- HPX_START ---"
    assert lines[-1] == "--- HPX_END ---"
    assert len(lines) == 3


def test_collect_lines_strips_leading_peripheral_reenable_glitch():
    """A UART/ITM peripheral re-enabled mid-run (e.g. released around a gated
    power window and restored afterward) can glitch a single leading byte on
    its first transmission -- observed as a stray non-ASCII byte prepended to
    an otherwise-clean protocol line.  That silently broke the
    ``^HPX_KEY=value$`` metadata parser downstream, dropping the clean-window
    result and falling back to a whole-capture power estimate.
    """
    read = _canned_reader(
        [
            "--- HPX_START ---\n".encode(),
            "\ufffdHPX_CLEAN_INFER_COUNT=236\n".encode("utf-8"),
            b"HPX_CLEAN_INFER_AVG_US=21116\n",
            b"--- HPX_END ---\n",
        ]
    )
    lines = collect_lines(read, transport_name="TEST")
    assert "HPX_CLEAN_INFER_COUNT=236" in lines
    assert "HPX_CLEAN_INFER_AVG_US=21116" in lines


def test_heartbeat_refreshes_inactivity_timer(monkeypatch):
    """Heartbeat lines should reset the inactivity deadline."""
    # Feed: START, then pause, then HEARTBEAT, then pause, then END.
    # With a 0.2s heartbeat timeout this run would abort without heartbeats,
    # but each heartbeat must keep it alive to reach HPX_END.
    script = [
        b"--- HPX_START ---\n",
        b"",  # quiet
        b"HPX_HEARTBEAT phase=infer pass=0 iter=0 layer=5\n",
        b"",
        b"HPX_HEARTBEAT phase=infer pass=0 iter=0 layer=10\n",
        b"",
        b"--- HPX_END ---\n",
    ]
    it = iter(script)

    def read() -> bytes:
        try:
            return next(it)
        except StopIteration:
            return b""

    lines = collect_lines(
        read,
        transport_name="TEST",
        heartbeat_timeout_s=1.0,
        poll_interval_s=0.01,
    )
    assert "--- HPX_END ---" in lines
    hb_lines = [l for l in lines if l.startswith("HPX_HEARTBEAT")]
    assert len(hb_lines) == 2


def test_hang_detected_when_no_heartbeat():
    """When firmware goes silent after HPX_START, capture returns within the
    heartbeat timeout instead of waiting for the overall timeout."""
    # After START, reader always returns b"" (no further data).
    it = iter([b"--- HPX_START ---\n"])

    def read() -> bytes:
        try:
            return next(it)
        except StopIteration:
            return b""

    import time as _t

    t0 = _t.monotonic()
    lines = collect_lines(
        read,
        transport_name="TEST",
        heartbeat_timeout_s=0.3,
        poll_interval_s=0.01,
    )
    elapsed = _t.monotonic() - t0
    # Should bail shortly after heartbeat_timeout_s, nowhere near 600s.
    assert elapsed < 2.0
    # HPX_END was never seen.
    assert "--- HPX_END ---" not in lines


def test_collect_lines_invokes_on_line_callback():
    seen: list[str] = []

    read = _canned_reader(
        [
            b"--- HPX_START ---\n",
            b"HPX_VERSION=1\n",
            b"--- HPX_END ---\n",
        ]
    )
    lines = collect_lines(
        read,
        transport_name="TEST",
        on_line=lambda line, _ts: seen.append(line),
    )

    assert seen == lines


# ---------------------------------------------------------------------------
# Clean-window "announce and extend"
# ---------------------------------------------------------------------------


def test_window_budget_parses_est_ms():
    budget = window_budget_s("HPX_HEARTBEAT phase=clean_window_begin iters=200 est_ms=1000")
    assert budget == 1.0 * WINDOW_BUDGET_SAFETY + WINDOW_BUDGET_MARGIN_S


def test_window_budget_none_for_zero_or_missing_est():
    # est_ms=0 → no usable estimate (a power or fixed-mode busy-loop build,
    # or a measurement that degraded at runtime, #164) — keep the normal
    # heartbeat behaviour.
    assert window_budget_s("HPX_HEARTBEAT phase=clean_window_begin iters=3 est_ms=0") is None
    # A normal heartbeat is not a window announce.
    assert window_budget_s("HPX_HEARTBEAT phase=infer pass=0 iter=0 layer=5") is None
    # Malformed estimate is ignored rather than raising.
    assert window_budget_s("HPX_HEARTBEAT phase=clean_window_begin est_ms=abc") is None


def test_clean_window_announce_survives_blackout_longer_than_heartbeat():
    """A clean-window announce widens the deadline so a silent window longer
    than the normal heartbeat timeout still reaches HPX_END."""
    import time as _t

    released = _t.monotonic() + 0.4  # quiet > heartbeat_timeout, << budget
    state = {"emitted_start": False}

    def read() -> bytes:
        if not state["emitted_start"]:
            state["emitted_start"] = True
            return (
                b"--- HPX_START ---\nHPX_HEARTBEAT phase=clean_window_begin iters=200 est_ms=1000\n"
            )
        if _t.monotonic() >= released:
            return b"--- HPX_END ---\n"
        return b""

    lines = collect_lines(
        read,
        transport_name="TEST",
        heartbeat_timeout_s=0.2,
        poll_interval_s=0.01,
    )
    # Without the announce, the 0.2s heartbeat would bail before 0.4s.
    assert "--- HPX_END ---" in lines


def test_window_budget_is_capped():
    """A garbage warm reading (wrapped CYCCNT delta x large iters) cannot
    fabricate a multi-hour deadline — the budget tops out at the cap (#170)."""
    line = "HPX_HEARTBEAT phase=clean_window_begin iters=6000 est_ms=266000000"
    assert window_budget_s(line) == WINDOW_BUDGET_CAP_S
    # A sane large estimate below the cap is untouched.
    sane = window_budget_s("HPX_HEARTBEAT phase=clean_window_begin iters=6000 est_ms=130000")
    assert sane is not None
    assert sane == 130.0 * WINDOW_BUDGET_SAFETY + WINDOW_BUDGET_MARGIN_S
    assert sane < WINDOW_BUDGET_CAP_S


def test_window_budget_survives_a_line_received_inside_the_window():
    """The announce's budget is a FLOOR until it expires, not a one-shot
    raise: a busy-loop window prints HPX_CLEAN_WINDOW_PROBE=busy_loop right
    after the announce, and before #170 that line reset the inactivity
    deadline to the flat heartbeat timeout — the widened deadline evaporated
    and the silent window was reported as a hang."""
    import time as _t

    released = _t.monotonic() + 0.4  # silence > heartbeat_timeout, << budget
    state = {"step": 0}

    def read() -> bytes:
        if state["step"] == 0:
            state["step"] = 1
            return (
                b"--- HPX_START ---\nHPX_HEARTBEAT phase=clean_window_begin iters=100 est_ms=1000\n"
            )
        if state["step"] == 1:
            # The in-window line that used to discard the held budget.
            state["step"] = 2
            return b"HPX_CLEAN_WINDOW_PROBE=busy_loop\n"
        if _t.monotonic() >= released:
            return b"--- HPX_END ---\n"
        return b""

    lines = collect_lines(
        read,
        transport_name="TEST",
        heartbeat_timeout_s=0.2,
        poll_interval_s=0.01,
    )
    # Without the hold-floor the 0.2s heartbeat bails ~0.2s after the probe
    # line, well before the 0.4s release.
    assert "--- HPX_END ---" in lines


def test_hang_warning_reports_real_silence_not_configured_timeout(caplog, monkeypatch):
    """With a held window budget the wait can exceed the configured timeout by
    the whole budget — the warning must report the actual silence (#170), or
    'no data for 30s' after a minutes-long wait misdirects the reader.

    The safety/margin knobs are shrunk so the divergence (a ~2s budget floor
    against a 0.2s heartbeat timeout) plays out in test time.
    """
    import logging as _logging
    import time as _t

    from helia_profiler.transport import protocol as _proto

    monkeypatch.setattr(_proto, "WINDOW_BUDGET_SAFETY", 1.0)
    monkeypatch.setattr(_proto, "WINDOW_BUDGET_MARGIN_S", 1.0)

    def read() -> bytes:
        if not getattr(read, "sent", False):
            read.sent = True  # ty: ignore[unresolved-attribute]  # one-shot flag stashed on the function object by design
            return (
                b"--- HPX_START ---\nHPX_HEARTBEAT phase=clean_window_begin iters=1 est_ms=1000\n"
            )
        return b""

    t0 = _t.monotonic()
    with caplog.at_level(_logging.WARNING, logger="hpx"):
        collect_lines(
            read,
            transport_name="TEST",
            heartbeat_timeout_s=0.2,
            poll_interval_s=0.01,
        )
    waited = _t.monotonic() - t0
    # The 1s est at 1.0 safety + 1s margin holds a ~2s floor past the 0.2s
    # timeout.
    assert waited > 1.0
    hang = [r for r in caplog.records if "no data for" in r.getMessage()]
    assert hang, "expected the hang warning"
    silence_match = re.search(r"no data for (\d+)s", hang[-1].getMessage())
    assert silence_match is not None
    reported = float(silence_match.group(1))
    # Reports the real ~2s silence, not the configured 0.2s.
    assert reported >= 1.0


def test_no_announce_still_times_out_on_silence():
    """Sanity: without a usable announce the normal heartbeat still trips on a
    blackout, so the extension is doing real work in the test above."""
    import time as _t

    released = _t.monotonic() + 0.4
    state = {"emitted_start": False}

    def read() -> bytes:
        if not state["emitted_start"]:
            state["emitted_start"] = True
            # est_ms=0 → no extension.
            return b"--- HPX_START ---\nHPX_HEARTBEAT phase=clean_window_begin iters=3 est_ms=0\n"
        if _t.monotonic() >= released:
            return b"--- HPX_END ---\n"
        return b""

    lines = collect_lines(
        read,
        transport_name="TEST",
        heartbeat_timeout_s=0.2,
        poll_interval_s=0.01,
    )
    assert "--- HPX_END ---" not in lines
