"""Tests for capture/transport.py — heartbeat-aware line collection."""

from __future__ import annotations

from helia_profiler.capture.transport import collect_lines


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


def test_legacy_kwargs_still_work():
    """timeout_s / line_timeout_s are accepted for back-compat."""
    read = _canned_reader(
        [
            b"--- HPX_START ---\n--- HPX_END ---\n",
        ]
    )
    lines = collect_lines(
        read,
        transport_name="TEST",
        timeout_s=5,
        line_timeout_s=5,
    )
    assert lines[-1] == "--- HPX_END ---"


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
