"""Tests for hpx doctor dependency reporting."""

from __future__ import annotations

from unittest.mock import patch

from helia_profiler.doctor import collect_checks


def _which_all(name: str) -> str:
    return f"/usr/bin/{name}"


def test_collect_checks_reports_required_python_separately() -> None:
    with patch("shutil.which", side_effect=_which_all), patch(
        "importlib.util.find_spec",
        side_effect=lambda name: object() if name == "neuralspotx" else None,
    ), patch("builtins.__import__", side_effect=__import__):
        checks, required_python, optional = collect_checks()

    assert any(label == "ARM GCC toolchain" and path for label, _binary, path in checks)
    assert required_python == [("neuralspotx Python package", "neuralspotx", True)]
    assert any(label == "heliaAOT compiler" for label, _name, _available in optional)


def test_collect_checks_marks_missing_required_python() -> None:
    with patch("shutil.which", side_effect=_which_all), patch(
        "importlib.util.find_spec",
        return_value=None,
    ), patch("builtins.__import__", side_effect=__import__):
        _checks, required_python, _optional = collect_checks()

    assert required_python == [("neuralspotx Python package", "neuralspotx", False)]