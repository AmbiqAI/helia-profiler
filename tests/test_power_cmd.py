"""Tests for the manual Joulescope passthrough command."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def test_power_on_passes_selected_joulescope_serial():
    from helia_profiler.cli.power_cmd import _cmd_power_on

    driver = MagicMock()
    with (
        patch("helia_profiler.power.get_driver", return_value=driver) as get_driver,
        patch("signal.pause", side_effect=KeyboardInterrupt),
    ):
        _cmd_power_on(SimpleNamespace(driver="joulescope-js320", power_serial="25QG"))

    get_driver.assert_called_once_with("joulescope-js320", serial="25QG")
    driver.enable_passthrough.assert_called_once()
    driver.disable_passthrough.assert_called_once()
