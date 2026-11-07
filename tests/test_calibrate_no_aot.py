"""What ``hpx calibrate`` does on a host without the aot extra."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from helia_profiler.calibration import calibrate
from helia_profiler.cli.app import app
from helia_profiler.errors import CalibrationError, EngineMissingError
from tests.calibration_bundles import two_bundles


def test_a_host_without_heliaaot_gets_the_install_hint(tmp_path: Path, monkeypatch) -> None:
    auto, wide = two_bundles(tmp_path)
    for name in [m for m in sys.modules if m == "helia_aot" or m.startswith("helia_aot.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "helia_aot", None)
    with pytest.raises(EngineMissingError, match="heliaAOT package not installed") as caught:
        calibrate([auto, wide], output=tmp_path / "out.json")
    assert caught.value.hint == (
        "Install helia-aot: pip install 'helia-profiler[aot]' or pip install helia-aot"
    )
    result = CliRunner().invoke(
        app, ["calibrate", str(auto), str(wide), "--output", str(tmp_path / "cli.json")]
    )
    assert result.exit_code == 1, result.output
    assert "heliaAOT package not installed" in result.output
    assert "pip install 'helia-profiler[aot]'" in result.output


def test_a_bad_tolerance_is_refused_before_any_bundle_is_read(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    with pytest.raises(CalibrationError, match="--tolerance must be a fraction"):
        calibrate([auto], output=tmp_path / "out.json", tolerance=float("nan"))
    result = CliRunner().invoke(
        app, ["calibrate", str(auto), "-o", str(tmp_path / "o.json"), "--tolerance", "nan"]
    )
    assert result.exit_code == 1 and "--tolerance must be a fraction" in result.output
