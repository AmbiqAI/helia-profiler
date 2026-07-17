"""Tests for power driver availability probes.

These tests exercise the ``check_available`` paths that translate bare
import failures (missing package OR binary/ABI mismatch) into actionable
``PowerError`` instances.  They do not require any hardware.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from helia_profiler.errors import PowerError
from helia_profiler.power.joulescope.device import _open_device
from helia_profiler.power.joulescope.driver import JoulescopeDriver


class TestJoulescopeAvailability:
    def test_missing_package_raises_with_install_hint(self):
        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name == "pyjoulescope_driver":
                raise ImportError("No module named 'pyjoulescope_driver'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            with pytest.raises(PowerError) as exc_info:
                JoulescopeDriver().check_available()
        err = exc_info.value
        assert "not installed" in str(err)
        assert "pip install" in (err.hint or "")

    def test_abi_mismatch_raises_with_numpy_hint(self):
        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name == "pyjoulescope_driver":
                raise ValueError("numpy.dtype size changed, may indicate binary incompatibility")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            with pytest.raises(PowerError) as exc_info:
                JoulescopeDriver().check_available()
        err = exc_info.value
        assert "failed to import" in str(err)
        assert "numpy" in (err.hint or "").lower()
        assert "force-reinstall" in (err.hint or "")


class TestJoulescopeOpenRetries:
    def test_busy_once_then_open_succeeds(self):
        class FakeDriver:
            def __init__(self):
                self.open_calls = 0

            def device_paths(self):
                return ["u/js110/004204"]

            def open(self, device_path):
                self.open_calls += 1
                if self.open_calls == 1:
                    raise RuntimeError("libusb claim failed: busy")

        fake_driver = FakeDriver()

        with (
            patch(
                "helia_profiler.power.joulescope.device._get_shared_driver",
                return_value=fake_driver,
            ),
            patch("helia_profiler.power.joulescope.device.time.sleep"),
        ):
            drv, device_path, family = _open_device(serial=None)

        assert drv is fake_driver
        assert device_path == "u/js110/004204"
        assert family == "js110"
        assert fake_driver.open_calls == 2


class _FakeJsDriver:
    """Minimal fake ``pyjoulescope_driver.Driver`` for open/close refcounting."""

    def __init__(self, device_path: str = "u/js110/004204") -> None:
        self.device_path = device_path
        self.open_calls = 0
        self.close_calls = 0
        self.published: dict[str, object] = {}

    def device_paths(self):
        return [self.device_path]

    def open(self, device_path):
        self.open_calls += 1

    def close(self, device_path):
        self.close_calls += 1

    def publish(self, topic, value):
        self.published[topic] = value

    def publish_and_wait(self, req_topic, req_value, value_topic, timeout):
        return 0


class TestJoulescopeSyncControllerRelease:
    """``JoulescopeSyncController.release()`` device-close behaviour."""

    def _make_controller(self, monkeypatch: pytest.MonkeyPatch, fake_drv: _FakeJsDriver):
        from helia_profiler.power.joulescope.sync import JoulescopeSyncController
        from helia_profiler.power.sync import SyncWiring

        monkeypatch.setattr(
            "helia_profiler.power.joulescope.device._get_shared_driver",
            lambda: fake_drv,
        )
        wiring = SyncWiring(
            lockstep=True, gate_input_index=0, state_input_index=1, go_output_index=0
        )
        return JoulescopeSyncController(serial=None, wiring=wiring)

    def test_release_closes_the_device_it_opened(self, monkeypatch: pytest.MonkeyPatch):
        from helia_profiler.power.joulescope import device as device_mod

        device_mod._open_refcounts.clear()
        fake_drv = _FakeJsDriver()
        controller = self._make_controller(monkeypatch, fake_drv)

        controller.arm()  # triggers _ensure() -> _open_device
        assert fake_drv.open_calls == 1
        assert device_mod._open_refcounts.get("u/js110/004204") == 1

        controller.release()

        assert fake_drv.close_calls == 1
        assert "u/js110/004204" not in device_mod._open_refcounts
        # Idempotent: releasing again re-arms (which re-opens transiently to
        # write GO) and closes again — balanced, no refcount leak either way.
        controller.release()
        assert fake_drv.open_calls == fake_drv.close_calls
        assert "u/js110/004204" not in device_mod._open_refcounts

    def test_release_does_not_close_device_still_held_by_active_capture(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A concurrent opener (e.g. an in-flight ``capture_gated``) keeps the
        shared handle open even after the sync controller releases its own
        reference — verified via the shared refcount in ``device.py``.
        """
        from helia_profiler.power.joulescope import device as device_mod
        from helia_profiler.power.joulescope.device import _close_device, _open_device

        device_mod._open_refcounts.clear()
        fake_drv = _FakeJsDriver()
        controller = self._make_controller(monkeypatch, fake_drv)

        controller.arm()  # sync controller opens the device (refcount -> 1)
        # Simulate capture_gated independently opening the same path.
        capture_drv, capture_path, _family = _open_device(serial=None)
        assert device_mod._open_refcounts["u/js110/004204"] == 2

        controller.release()
        # Sync controller's own reference is gone, but capture's is not.
        assert fake_drv.close_calls == 0
        assert device_mod._open_refcounts["u/js110/004204"] == 1

        _close_device(capture_drv, capture_path)
        assert fake_drv.close_calls == 1
        assert "u/js110/004204" not in device_mod._open_refcounts

    def test_js320_uses_bitmap_gpo_commands(self, monkeypatch: pytest.MonkeyPatch):
        from helia_profiler.power.joulescope import device as device_mod

        device_mod._open_refcounts.clear()
        fake_drv = _FakeJsDriver("u/js320/25QG")
        controller = self._make_controller(monkeypatch, fake_drv)

        controller.arm()
        controller.signal_go()

        assert fake_drv.published == {
            "u/js320/25QG/s/gpo/+/!clr": 1,
            "u/js320/25QG/s/gpo/+/!set": 1,
        }
