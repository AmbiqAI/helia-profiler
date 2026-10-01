"""Tests for the manual Joulescope passthrough command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from helia_profiler.errors import PowerError


def test_power_on_passes_selected_joulescope_serial():
    from helia_profiler.cli.power_cmd import _cmd_power_on

    driver = MagicMock()
    with (
        patch("helia_profiler.power.get_driver", return_value=driver) as get_driver,
        patch("threading.Event.wait", side_effect=KeyboardInterrupt),
    ):
        _cmd_power_on("joulescope", power_serial="25QG")

    get_driver.assert_called_once_with("joulescope", serial="25QG")
    driver.enable_passthrough.assert_called_once()
    driver.disable_passthrough.assert_called_once()


def test_power_on_names_the_released_driver(capsys):
    from helia_profiler.cli.power_cmd import _cmd_power_on

    driver = MagicMock()
    driver.name = "Joulescope"
    with (
        patch("helia_profiler.power.get_driver", return_value=driver),
        patch("threading.Event.wait", side_effect=KeyboardInterrupt),
    ):
        _cmd_power_on("joulescope")

    assert "Joulescope released." in capsys.readouterr().out


def test_power_on_warns_when_release_fails(capsys, caplog):
    from helia_profiler.cli.power_cmd import _cmd_power_on

    driver = MagicMock()
    driver.name = "Joulescope"
    driver.disable_passthrough.side_effect = RuntimeError("device gone")
    with (
        patch("helia_profiler.power.get_driver", return_value=driver),
        patch("threading.Event.wait", side_effect=KeyboardInterrupt),
        caplog.at_level("WARNING", logger="hpx"),
    ):
        _cmd_power_on("joulescope")

    assert "released" not in capsys.readouterr().out
    assert "Failed to release Joulescope: device gone" in caplog.text


@pytest.mark.parametrize("failing_step", ["get_driver", "enable_passthrough"])
def test_power_on_error_prints_hint_once(capsys, failing_step):
    from helia_profiler.cli.power_cmd import _cmd_power_on

    error = PowerError("Joulescope not found", hint="plug in the Joulescope")
    driver = MagicMock()
    driver.enable_passthrough.side_effect = error
    get_driver = MagicMock(return_value=driver)
    if failing_step == "get_driver":
        get_driver.side_effect = error
    with (
        patch("helia_profiler.power.get_driver", get_driver),
        pytest.raises(SystemExit) as exc_info,
    ):
        _cmd_power_on("joulescope")

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "Error: Joulescope not found" in captured.err
    assert captured.err.lower().count("hint: plug in the joulescope") == 1
