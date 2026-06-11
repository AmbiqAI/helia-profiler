from __future__ import annotations

import types

import pytest

from helia_profiler.capture import readiness
from helia_profiler.capture.readiness import (
    open_jlink_with_retry,
    poll_until,
    resume_if_halted,
)
from helia_profiler.errors import CaptureError


# ---------------------------------------------------------------------------
# poll_until
# ---------------------------------------------------------------------------


def test_poll_until_returns_true_immediately_when_predicate_satisfied(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(readiness.time, "sleep", lambda s: sleeps.append(s))

    assert poll_until(lambda: True, timeout_s=1.0) is True
    assert sleeps == []  # never slept — predicate was already true


def test_poll_until_polls_until_predicate_flips(monkeypatch):
    monkeypatch.setattr(readiness.time, "sleep", lambda _s: None)
    calls = {"n": 0}

    def predicate() -> bool:
        calls["n"] += 1
        return calls["n"] >= 3

    assert poll_until(predicate, timeout_s=10.0, interval_s=0.01) is True
    assert calls["n"] == 3


def test_poll_until_times_out(monkeypatch):
    # Advance a fake monotonic clock so the deadline is reached deterministically.
    clock = {"t": 0.0}
    monkeypatch.setattr(readiness.time, "monotonic", lambda: clock["t"])

    def fake_sleep(s: float) -> None:
        clock["t"] += s

    monkeypatch.setattr(readiness.time, "sleep", fake_sleep)

    assert poll_until(lambda: False, timeout_s=0.5, interval_s=0.1) is False
    assert clock["t"] >= 0.5


# ---------------------------------------------------------------------------
# resume_if_halted
# ---------------------------------------------------------------------------


class _FakeJLink:
    def __init__(self, *, halted: bool):
        self._halted = halted
        self.restart_calls = 0

    def halted(self) -> bool:
        return self._halted

    def restart(self) -> None:
        self.restart_calls += 1
        self._halted = False


def test_resume_if_halted_restarts_when_halted(monkeypatch):
    monkeypatch.setattr(readiness.time, "sleep", lambda _s: None)
    jlink = _FakeJLink(halted=True)

    assert resume_if_halted(jlink) is True
    assert jlink.restart_calls == 1


def test_resume_if_halted_noop_when_running(monkeypatch):
    monkeypatch.setattr(readiness.time, "sleep", lambda _s: None)
    jlink = _FakeJLink(halted=False)

    assert resume_if_halted(jlink) is False
    assert jlink.restart_calls == 0


# ---------------------------------------------------------------------------
# open_jlink_with_retry
# ---------------------------------------------------------------------------


class _FakeJLinkException(Exception):
    pass


def _install_fake_pylink(monkeypatch) -> None:
    fake_pylink = types.SimpleNamespace(
        JLinkInterfaces=types.SimpleNamespace(SWD=1),
        errors=types.SimpleNamespace(JLinkException=_FakeJLinkException),
    )
    import sys

    monkeypatch.setitem(sys.modules, "pylink", fake_pylink)


class _RetryJLink:
    def __init__(self, *, fail_first: int):
        self.open_calls = 0
        self.connect_calls = 0
        self.close_calls = 0
        self._fail_first = fail_first

    def open(self, serial_no=None) -> None:
        self.open_calls += 1

    def disable_dialog_boxes(self) -> None:
        pass

    def set_tif(self, tif) -> None:
        pass

    def connect(self, device, speed) -> None:
        self.connect_calls += 1
        if self.connect_calls <= self._fail_first:
            raise _FakeJLinkException("target not ready")

    def close(self) -> None:
        self.close_calls += 1


def test_open_jlink_with_retry_succeeds_first_try(monkeypatch):
    _install_fake_pylink(monkeypatch)
    monkeypatch.setattr(readiness.time, "sleep", lambda _s: None)
    jlink = _RetryJLink(fail_first=0)

    open_jlink_with_retry(jlink, device="AP510NFA-CBR", timeout_s=5.0)

    assert jlink.connect_calls == 1
    assert jlink.close_calls == 0


def test_open_jlink_with_retry_retries_until_ready(monkeypatch):
    _install_fake_pylink(monkeypatch)
    monkeypatch.setattr(readiness.time, "sleep", lambda _s: None)
    jlink = _RetryJLink(fail_first=2)

    open_jlink_with_retry(jlink, device="AP510NFA-CBR", timeout_s=10.0, interval_s=0.01)

    assert jlink.connect_calls == 3
    assert jlink.close_calls == 2  # closed after each failed attempt


def test_open_jlink_with_retry_raises_capture_error_on_timeout(monkeypatch):
    _install_fake_pylink(monkeypatch)
    clock = {"t": 0.0}
    monkeypatch.setattr(readiness.time, "monotonic", lambda: clock["t"])

    def fake_sleep(s: float) -> None:
        clock["t"] += s

    monkeypatch.setattr(readiness.time, "sleep", fake_sleep)
    jlink = _RetryJLink(fail_first=10_000)  # never succeeds

    with pytest.raises(CaptureError, match="Timed out attaching"):
        open_jlink_with_retry(jlink, device="AP510NFA-CBR", timeout_s=0.3, interval_s=0.1)
