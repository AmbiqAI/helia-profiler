from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from helia_profiler.cli.options import _build_cli_overrides


def test_conflicting_and_repeated_options() -> None:
    assert _build_cli_overrides(
        ensure_power=True,
        no_ensure_power=True,
        compiler_launcher="ccache",
        no_compiler_launcher=True,
        pmu_counters=["cpu:default", "cpu:cycles,instructions"],
        nsx_module=["bsp:ref=old", "bsp:path=/local", "bsp:ref=new"],
        per_layer=False,
        power=False,
    ) == {
        "target": {"ensure_board_powered": False},
        "build": {
            "compiler_launcher": "none",
            "nsx_modules": {"bsp": {"ref": "new", "path": "/local"}},
        },
        "profiling": {"per_layer": False, "pmu_counters": {"cpu": ["cycles", "instructions"]}},
        "verbose": 0,
    }
    assert _build_cli_overrides() == {"verbose": 0}


def test_pmu_error_precedes_nsx_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        _build_cli_overrides(nsx_module=["invalid"], pmu_counters=["invalid"])
    assert exc.value.code == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == (
        "Error: --pmu-counters format is GROUP:SELECT (e.g. cpu:default, mve:all). Got: 'invalid'\n"
    )


def test_unknown_parameter_precedes_parsing(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(TypeError) as exc:
        _build_cli_overrides(typo=True, pmu_counters=["invalid"])
    assert str(exc.value) == "unknown profile CLI parameter(s): ['typo']"
    assert capsys.readouterr() == ("", "")


def test_shared_options_have_no_execution_dependencies() -> None:
    cli_root = Path(__file__).resolve().parents[1] / "src/helia_profiler/cli"
    for node in ast.walk(ast.parse((cli_root / "options.py").read_text())):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert (node.module or "").split(".")[0] in sys.stdlib_module_names
        elif isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] in sys.stdlib_module_names for alias in node.names)
    source = ast.parse((cli_root / "run_cmds.py").read_text())
    for node in ast.walk(source):
        if isinstance(node, ast.ImportFrom):
            assert "profile_cmd" not in (node.module or "").split(".")
            assert all(alias.name != "profile_cmd" for alias in node.names)
        elif isinstance(node, ast.Import):
            assert all("profile_cmd" not in alias.name.split(".") for alias in node.names)
