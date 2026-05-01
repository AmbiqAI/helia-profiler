"""Tests for power driver availability probes.

These tests exercise the ``check_available`` paths that translate bare
import failures (missing package OR binary/ABI mismatch) into actionable
``PowerError`` instances.  They do not require any hardware.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from helia_profiler.errors import PowerError
from helia_profiler.power.joulescope_driver import JoulescopeDriver


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
