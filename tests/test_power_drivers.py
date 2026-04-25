"""Tests for power driver availability probes.

These tests exercise the ``check_available`` paths that translate bare
import failures (missing package OR binary/ABI mismatch) into actionable
``PowerError`` instances.  They do not require any hardware.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from helia_profiler.errors import PowerError
from helia_profiler.power.joulescope_driver import JoulescopeDriver
from helia_profiler.power.joulescope_js220 import JoulescopeJS220Driver


class TestJS110Availability:
    def test_missing_package_raises_with_install_hint(self):
        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name == "joulescope":
                raise ImportError("No module named 'joulescope'")
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
            if name == "joulescope":
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


class TestJS220Availability:
    def test_missing_package_raises_with_install_hint(self):
        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name == "pyjoulescope_driver":
                raise ImportError("No module named 'pyjoulescope_driver'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            with pytest.raises(PowerError) as exc_info:
                JoulescopeJS220Driver().check_available()
        assert "not installed" in str(exc_info.value)

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
                JoulescopeJS220Driver().check_available()
        err = exc_info.value
        assert "failed to import" in str(err)
        assert "numpy" in (err.hint or "").lower()
