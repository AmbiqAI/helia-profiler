import importlib
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest
from typer import BadParameter
from typer.main import get_command
from typer.testing import CliRunner

from helia_profiler import power
from helia_profiler.cli.app import app
from helia_profiler.config import Aggregation, PowerFirmware, Transport
from helia_profiler.engines import EngineType
from helia_profiler.placement import Placement
from helia_profiler.power import list_drivers
from helia_profiler.target.lifecycle import ResetStrategy

VALUES = {
    "engine": list(EngineType),
    "runtime_arena_location": [p for p in Placement if p != Placement.MRAM],
    "runtime_weights_location": list(Placement),
    "core_override": ["cm4", "cm55"],
    "transport": list(Transport),
    "aggregation": list(Aggregation),
    "power_driver": list_drivers(),
    "power_mode": ["external", "internal"],
    "power_firmware": list(PowerFirmware),
    "power_reset_strategy": list(ResetStrategy),
    "output_format": ["csv", "json"],
    "format": ["table", "csv", "json"],
    "kind": ["debug", "swpoi"],
    "driver": ["joulescope"],
    "power": ["both", "on", "off"],
    "suite": ["smoke", "models-rt", "models-aot", "complete"],
}
SITES = [("profile", n, None) for n in list(VALUES)[:11]] + [
    ("analyze", "engine", None),
    ("analyze", "format", "table"),
    ("doctor", "engine", None),
    ("doctor", "transport", None),
    ("target reset", "kind", "debug"),
    ("power-on", "driver", "joulescope"),
    ("validate", "power", "off"),
    ("validate", "suite", None),
]


@pytest.mark.parametrize("path,name,default", SITES)
def test_choice_contract(path, name, default):
    command = cast(Any, get_command(app))
    for part in path.split():
        command = command.commands[part]
    param = next(p for p in command.params if p.name == name)
    expected = [str(v) for v in VALUES[name]]
    assert list(param.type.choices) == expected
    assert param.default == default
    for value in expected:
        assert param.type.convert(value, param, None) == value
    for invalid in ("not-a-choice", expected[0].upper()):
        with pytest.raises(BadParameter, match="is not one of"):
            param.type.convert(invalid, param, None)
    result = CliRunner().invoke(app, [*path.split(), "--help"], env={"COLUMNS": "1000"})
    assert result.exit_code == 0, result.output
    help_text = "".join(result.output.split())
    assert "|".join(expected) in help_text


def test_cli_uses_only_public_choice_api():
    root = Path(__file__).resolve().parents[1] / "src" / "helia_profiler" / "cli"
    for name in ("app.py", "inspect_app.py", "validation_app.py"):
        assert "typer._types" not in (root / name).read_text(encoding="utf-8")


def test_registered_driver_strings(monkeypatch):
    module = importlib.import_module("helia_profiler.cli.app")
    original = power._DRIVERS.copy()
    names = ["MixedCase", "a-b", "_ignore_", "_name_", "name", "value"]
    seen = Mock()
    monkeypatch.setattr("helia_profiler.cli.profile_cmd._cmd_profile", seen)
    try:
        for name in names:
            power.register_driver(name, power.resolve_driver_class("ina228"))
        fresh = importlib.reload(module).app
        for name in names:
            seen.reset_mock()
            args = ["profile", "model.tflite", "--power-driver", name]
            result = CliRunner().invoke(fresh, args)
            assert result.exit_code == 0, result.output
            seen.assert_called_once()
            assert seen.call_args.kwargs["power_driver"] == name
            assert type(seen.call_args.kwargs["power_driver"]) is str
        seen.reset_mock()
        args = ["profile", "model.tflite", "--power-driver", "mixedcase"]
        assert CliRunner().invoke(fresh, args).exit_code == 2
        seen.assert_not_called()
    finally:
        power._DRIVERS.clear()
        power._DRIVERS.update(original)
        importlib.reload(module)
