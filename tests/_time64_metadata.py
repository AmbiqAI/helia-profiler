"""Time64 metadata for pure statistics tests, with device access rejected."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from types import ModuleType, SimpleNamespace

import pytest

time64 = SimpleNamespace(SECOND=1 << 30)


@pytest.fixture(autouse=True)
def time64_metadata(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    attempts: list[bool] = []

    def reject_access(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("Device access is outside these statistics tests")

    metadata = ModuleType("pyjoulescope_driver")
    metadata.__dict__.update(time64=time64, Driver=reject_access, device_paths=reject_access)
    monkeypatch.setitem(sys.modules, "pyjoulescope_driver", metadata)
    yield
    assert not attempts, "A statistics test attempted device access"
