"""Tests for power driver availability probes.

These tests exercise the ``check_available`` paths that translate bare
import failures (missing package OR binary/ABI mismatch) into actionable
``PowerError`` instances.  They do not require any hardware.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from helia_profiler.errors import PowerError
from helia_profiler.power.joulescope_driver import JoulescopeDriver, _open_device


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
                raise ValueError(
                    "numpy.dtype size changed, may indicate binary incompatibility"
                )
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

        with patch(
            "helia_profiler.power.joulescope_driver._get_shared_driver",
            return_value=fake_driver,
        ), patch("helia_profiler.power.joulescope_driver.time.sleep"):
            drv, device_path, family = _open_device(serial=None)

        assert drv is fake_driver
        assert device_path == "u/js110/004204"
        assert family == "js110"
        assert fake_driver.open_calls == 2
