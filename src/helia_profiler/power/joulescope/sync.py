"""Joulescope GPI/GPO lock-step sync controller."""

from __future__ import annotations

import time
from typing import Any

from ..sync import DeviceState, SyncWiring
from .device import _open_device


class JoulescopeSyncController:
    """3-wire lock-step controller backed by Joulescope GPI/GPO.

    Drives OUTPUT0 (go), reads INPUT0 (gate) and INPUT1 (state) on the same
    shared process-wide driver used for capture, so it composes with an active
    gated capture without re-opening the relay.
    """

    def __init__(self, *, serial: str | None, wiring: SyncWiring) -> None:
        self._serial = serial
        self._wiring = wiring
        self._driver: Any = None
        self._path: str | None = None

    @property
    def lockstep(self) -> bool:
        return True

    def _ensure(self) -> tuple[Any, str]:
        if self._driver is None:
            self._driver, self._path, _family = _open_device(self._serial)
        return self._driver, str(self._path)

    def _read_input(self, index: int) -> bool:
        driver, path = self._ensure()
        value = driver.publish_and_wait(
            f"{path}/s/gpi/+/!req", 0, f"{path}/s/gpi/+/!value", timeout=0.5
        )
        return bool(int(value) & (1 << index))

    def _write_go(self, high: bool) -> None:
        driver, path = self._ensure()
        driver.publish(f"{path}/s/gpo/{self._wiring.go_output_index}/value", 1 if high else 0)

    def arm(self) -> None:
        self._write_go(False)

    def wait_ready(self, *, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._read_input(self._wiring.state_input_index):
                return True
            time.sleep(0.005)
        return False

    def signal_go(self) -> None:
        self._write_go(True)

    def read_state(self) -> DeviceState:
        if self._read_input(self._wiring.gate_input_index):
            return DeviceState.RUNNING
        if self._read_input(self._wiring.state_input_index):
            return DeviceState.READY
        return DeviceState.UNKNOWN

    def release(self) -> None:
        try:
            self._write_go(False)
        except Exception:  # pragma: no cover - defensive
            pass
