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

from helia_profiler.calibration import calibrate, read_calibration
from helia_profiler.cli.app import app
from helia_profiler.errors import CalibrationError, EngineMissingError
from tests.calibration_bundles import GOLDEN, _calibration_schema, flat, two_bundles


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
    assert "heliaAOT package not installed" in flat(result.output)
    assert "pip install 'helia-profiler[aot]'" in flat(result.output)


def test_a_bad_tolerance_is_refused_before_any_bundle_is_read(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    with pytest.raises(CalibrationError, match="--tolerance must be a fraction"):
        calibrate([auto], output=tmp_path / "out.json", tolerance=float("nan"))
    result = CliRunner().invoke(
        app, ["calibrate", str(auto), "-o", str(tmp_path / "o.json"), "--tolerance", "nan"]
    )
    assert result.exit_code == 1 and "--tolerance must be a fraction" in flat(result.output)


def _entry(document: dict[str, Any]) -> dict[str, Any]:
    return document["operator_cycles"]["0:fc"][0]


def _setup(document: dict[str, Any]) -> dict[str, Any]:
    return document["capture"]["setup"]


Change = Callable[[dict[str, Any]], Any]

#: Shapes the exported schema refuses; hpx's reader must refuse every one.
_REJECTED: dict[str, Change] = {
    "operators": lambda d: d.update(operators={}),
    "capture.prices": lambda d: d["capture"].update(prices={}),
    "model.x": lambda d: d["model"].update(x=1),
    "capture.setup.x": lambda d: _setup(d).update(x=1),
    "an entry's stdev": lambda d: _entry(d).update(stdev=1.0),
    "an entry without facts": lambda d: _entry(d).pop("facts"),
    "a fact named note": lambda d: _entry(d)["facts"].update(note="x"),
    "a fact named fixed:": lambda d: _entry(d)["facts"].update({"fixed:": "x"}),
    "negative cycles": lambda d: _entry(d).update(cycles=-1.0),
    "boolean cycles": lambda d: _entry(d).update(cycles=True),
    "an empty list": lambda d: d["operator_cycles"].update({"0:fc": []}),
    "a pairing digest without fields": lambda d: _setup(d).update(pairing_fields=None),
    "no pairing keys": lambda d: [_setup(d).pop(k) for k in ("pairing_digest", "pairing_fields")],
    "no helia_aot_version": lambda d: _setup(d).pop("helia_aot_version"),
    "no fields": lambda d: _setup(d).pop("fields"),
    "version 1": lambda d: d.update(schema_version=1),
    "version 3": lambda d: d.update(schema_version=3),
    "no version": lambda d: d.pop("schema_version"),
    "an empty sha256": lambda d: d["model"].update(sha256=""),
    "a negative subgraph": lambda d: d["model"].update(subgraph=-3),
    "empty conditions": lambda d: d.update(conditions={}),
    "null weights_memory": lambda d: d["conditions"].update(weights_memory=None),
    "no weights_memory": lambda d: d["conditions"].pop("weights_memory"),
    "no target": lambda d: d["conditions"].pop("target"),
    "an empty target": lambda d: d["conditions"].update(target=""),
    "a string cpu_hz": lambda d: d["conditions"].update(cpu_hz="250"),
    "a boolean cpu_hz": lambda d: d["conditions"].update(cpu_hz=True),
    "a string external_memory_clock": lambda d: d["conditions"].update(external_memory_clock="48"),
    "a null weights_cache": lambda d: d["conditions"].update(weights_cache=None),
    "a numeric application_load": lambda d: d["conditions"].update(application_load=0),
    "no capture": lambda d: d.pop("capture"),
}
#: Shapes both accept.
_ACCEPTED: dict[str, Change] = {
    "the committed file": lambda d: None,
    "minimal conditions": lambda d: d.update(
        conditions={"target": "apollo510_evb", "weights_memory": "dtcm"}
    ),
    "npu_hz": lambda d: d["conditions"].update(npu_hz=96_000_000),
    "an object-valued condition": lambda d: d["conditions"].update(board={"rev": "b"}),
    "a null external_memory_clock": lambda d: d["conditions"].update(external_memory_clock=None),
    "null pairing": lambda d: _setup(d).update(pairing_digest=None, pairing_fields=None),
    "a null helia_aot_version": lambda d: _setup(d).update(helia_aot_version=None),
}
#: What only the reader can refuse, as JSON Schema cannot say it: an
#: integer-valued float in an integer field, a vector listed twice, NaN and
#: infinity in the cycles or in an open condition (Python's JSON parser reads
#: 1e400 as infinity), and a final newline (jsonschema's pattern search lets
#: ``$`` match before it, which the reader's full match does not).
_READER_ONLY: dict[str, Change] = {
    "version 2.0": lambda d: d.update(schema_version=2.0),
    "subgraph 0.0": lambda d: d["model"].update(subgraph=0.0),
    "a float cpu_hz": lambda d: d["conditions"].update(cpu_hz=250_000_000.0),
    "a vector listed twice": lambda d: d["operator_cycles"]["0:fc"].append(_entry(d)),
    "NaN cycles": lambda d: _entry(d).update(cycles=float("nan")),
    "infinite cycles": lambda d: _entry(d).update(cycles=float("inf")),
    "an infinite open condition": lambda d: d["conditions"].update(
        board={"coefficient": float("inf")}
    ),
    "a fact name with a final newline": lambda d: _entry(d)["facts"].update({"fixed:a\n": "x"}),
    "a digest with a final newline": lambda d: _setup(d).update(digest=_setup(d)["digest"] + "\n"),
}


def _reads(path: Path) -> bool:
    try:
        read_calibration(path)
    except CalibrationError:
        return False
    return True


def test_the_exported_schema_rejects_what_hpx_refuses(tmp_path: Path) -> None:
    """Every case goes through both the exported schema and hpx's reader, which must agree."""
    schema = _calibration_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    committed = json.loads(GOLDEN.read_text(encoding="utf-8"))
    disagreements = []
    for expected, cases in (
        ((False, False), _REJECTED),
        ((True, True), _ACCEPTED),
        ((True, False), _READER_ONLY),
    ):
        for name, change in cases.items():
            document = json.loads(json.dumps(committed))
            change(document)
            path = tmp_path / "case.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            seen = (validator.is_valid(json.loads(path.read_text())), _reads(path))
            if seen != expected:
                disagreements.append(f"{name}: schema, reader = {seen}, expected {expected}")
    assert not disagreements, disagreements


@pytest.mark.parametrize(
    "artifact",
    ["summary.json", "run_metadata.json", "result_manifest.json", "hpx_plan.json", "merge.json"],
)
def test_decoder_exhaustion_has_a_domain_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact: str
) -> None:
    from helia_profiler.calibration.table import _plan
    from helia_profiler.errors import ReportError
    from helia_profiler.evaluation.compare import load_run_artifacts
    from tests.calibration_bundles import _republish

    auto, _ = two_bundles(tmp_path)
    output = tmp_path / "out.json"
    output.write_bytes(GOLDEN.read_bytes())
    target = output if artifact == "merge.json" else auto / artifact
    data = json.loads(target.read_text(encoding="utf-8"))
    data["decoder_probe"] = "exhaust-decoder-here"
    target.write_text(json.dumps(data), encoding="utf-8")
    if artifact not in ("merge.json", "result_manifest.json"):
        _republish(auto)
    # Load the surrounding bundle before injecting a failure in the plan decoder.
    run = load_run_artifacts(auto) if artifact == "hpx_plan.json" else None
    before = output.read_bytes()
    original_loads = json.loads

    def exhausted(text: str, *args: Any, **kwargs: Any) -> Any:
        if "exhaust-decoder-here" in text:
            raise RecursionError("decoder exhausted")
        return original_loads(text, *args, **kwargs)

    monkeypatch.setattr(json, "loads", exhausted)
    error = CalibrationError if artifact in ("merge.json", "hpx_plan.json") else ReportError
    with pytest.raises(error, match="JSON nesting exceeds the decoder limit") as caught:
        if artifact == "hpx_plan.json":
            assert run is not None
            _plan(run, str(auto))
        else:
            calibrate([auto], output=output, merge=artifact == "merge.json")
    assert isinstance(caught.value.__cause__, RecursionError)
    assert target.name in str(caught.value)
    assert output.read_bytes() == before

    if artifact != "hpx_plan.json":
        args = ["calibrate", str(auto), "--output", str(output)]
        if artifact == "merge.json":
            args.append("--merge")
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 1
        assert "JSON nesting exceeds the decoder limit" in flat(result.output)
        assert "Traceback" not in result.output
        assert output.read_bytes() == before
