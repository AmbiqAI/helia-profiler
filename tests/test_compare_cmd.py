from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from helia_profiler.cli.compare_cmd import _cmd_compare
from helia_profiler.evaluation import VerdictStatus


def _kwargs(**overrides):
    values = {
        "baseline": Path("baseline"),
        "candidate": Path("candidate"),
        "output_dir": None,
        "profile": None,
        "top_layers": 10,
        "validation": False,
    }
    values.update(overrides)
    return values


def test_compare_command_loads_and_forwards_profile():
    profile = object()
    result = SimpleNamespace(verdict=SimpleNamespace(status=VerdictStatus.PASS))
    with (
        patch("helia_profiler.evaluation.ComparisonProfile.load", return_value=profile),
        patch("helia_profiler.evaluation.compare_runs", return_value=result) as compare,
        patch("helia_profiler.console.HpxConsole.print_compare") as render,
    ):
        _cmd_compare(**_kwargs(profile=Path("policy.json")))

    compare.assert_called_once_with(Path("baseline"), Path("candidate"), profile=profile)
    render.assert_called_once()


def test_compare_command_exits_one_for_failed_verdict():
    result = SimpleNamespace(verdict=SimpleNamespace(status=VerdictStatus.FAIL))
    with (
        patch("helia_profiler.evaluation.compare_runs", return_value=result),
        patch("helia_profiler.console.HpxConsole.print_compare"),
        pytest.raises(SystemExit) as exc,
    ):
        _cmd_compare(**_kwargs())

    assert exc.value.code == 1


def test_compare_command_keeps_warn_verdict_successful():
    result = SimpleNamespace(verdict=SimpleNamespace(status=VerdictStatus.WARN))
    with (
        patch("helia_profiler.evaluation.compare_runs", return_value=result),
        patch("helia_profiler.console.HpxConsole.print_compare") as render,
    ):
        _cmd_compare(**_kwargs())

    render.assert_called_once()
