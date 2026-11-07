"""What ``hpx calibrate`` does on a host without the aot extra."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from helia_profiler.calibration import calibrate
from helia_profiler.cli.app import app
from helia_profiler.errors import CalibrationError, EngineMissingError
from tests.calibration_bundles import GOLDEN, _calibration_schema, two_bundles


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


def _entry(document: dict[str, Any]) -> dict[str, Any]:
    return document["operator_cycles"]["0:fc"][0]


_REJECTED: dict[str, Callable[[dict[str, Any]], Any]] = {
    "operators": lambda d: d.update(operators={}),
    "capture.prices": lambda d: d["capture"].update(prices={}),
    "model.x": lambda d: d["model"].update(x=1),
    "capture.setup.x": lambda d: d["capture"]["setup"].update(x=1),
    "an entry's stdev": lambda d: _entry(d).update(stdev=1.0),
    "an entry without facts": lambda d: _entry(d).pop("facts"),
    "a fact named note": lambda d: _entry(d)["facts"].update(note="x"),
    "negative cycles": lambda d: _entry(d).update(cycles=-1.0),
    "boolean cycles": lambda d: _entry(d).update(cycles=True),
    "an empty list": lambda d: d["operator_cycles"].update({"0:fc": []}),
    "a pairing digest without fields": lambda d: d["capture"]["setup"].update(pairing_fields=None),
    "version 1": lambda d: d.update(schema_version=1),
    "version 3": lambda d: d.update(schema_version=3),
    "null weights_memory": lambda d: d["conditions"].update(weights_memory=None),
    "no target": lambda d: d["conditions"].pop("target"),
    "no capture": lambda d: d.pop("capture"),
}
_ACCEPTED: dict[str, Callable[[dict[str, Any]], Any]] = {
    "minimal conditions": lambda d: d.update(
        conditions={"target": "apollo510_evb", "weights_memory": "dtcm"}
    ),
    "npu_hz": lambda d: d["conditions"].update(npu_hz=96_000_000),
    "an object-valued condition": lambda d: d["conditions"].update(board={"rev": "b"}),
    "null pairing": lambda d: d["capture"]["setup"].update(
        pairing_digest=None, pairing_fields=None
    ),
}


def test_the_exported_schema_rejects_what_hpx_refuses() -> None:
    schema = _calibration_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    committed = json.loads(GOLDEN.read_text(encoding="utf-8"))
    validator.validate(committed)
    for name, change in _REJECTED.items():
        document = json.loads(json.dumps(committed))
        change(document)
        assert not validator.is_valid(document), name
    for name, change in _ACCEPTED.items():
        document = json.loads(json.dumps(committed))
        change(document)
        assert validator.is_valid(document), name
