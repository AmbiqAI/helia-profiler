"""``hpx calibrate`` (#648): two synthetic bundles, every rule, and heliaOptimus's shape."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from typer.testing import CliRunner

from helia_profiler.calibration import (
    CALIBRATION_SCHEMA_VERSION,
    CalibrationTable,
    calibrate,
    read_bundle,
    read_calibration,
)
from helia_profiler.cli.app import app
from helia_profiler.errors import CalibrationError, ReportError
from helia_profiler.results.run_summary import RUN_SUMMARY_SCHEMA, RUN_SUMMARY_SCHEMA_VERSION
from tests.result_bundles import publish_manifest

SHA = "a" * 64
CONDITIONS = {
    "target": "apollo510_evb",
    "cpu_hz": 250_000_000,
    "weights_memory": "dtcm",
    "weights_cache": "on",
    "application_load": "none",
}

#: Three operators, two with knobs; position 2 (the softmax) has none.
PLAN_OPERATORS = [
    {
        "id": "0:conv",
        "index": 0,
        "op_type": "conv_2d",
        "knobs": {"accumulation": {"value": "int32"}},
    },
    {
        "id": "0:fc",
        "index": 1,
        "op_type": "fully_connected",
        "knobs": {"accumulation": {"value": "int32"}, "weights": {"value": "packed"}},
    },
    {"id": "0:softmax", "index": 2, "op_type": "softmax", "knobs": {}},
]


def _knob(ops: list[dict[str, Any]], operator: str, knob: str, value: str) -> list[dict[str, Any]]:
    out = json.loads(json.dumps(ops))
    for op in out:
        if op["id"] == operator:
            op["knobs"][knob] = {"value": value, "approximate": value == "int16"}
    return out


def bundle(
    path: Path,
    *,
    cycles: dict[int, float],
    ops: tuple[str, ...] = ("CONV_2D", "FULLY_CONNECTED", "SOFTMAX"),
    plan_ops: list[dict[str, Any]] | None = None,
    conditions: dict[str, Any] | None = CONDITIONS,
    sha256: str = SHA,
    plan_sha256: str | None = None,
    subgraph: int = 0,
    overflow: int | None = None,
    plan_schema: int = 1,
    plan_files: tuple[str, ...] = ("hpx_plan.json",),
) -> Path:
    """Write a result bundle as ``hpx profile --engine helia-aot`` would."""
    path.mkdir(parents=True)
    summary: dict[str, Any] = {
        "schema": RUN_SUMMARY_SCHEMA,
        "schema_version": RUN_SUMMARY_SCHEMA_VERSION,
        "engine": "helia-aot",
        "layers": len(cycles),
        "total_cycles": sum(cycles.values()),
        "overflow_detected": overflow is not None,
        "identity": {"model_sha256": sha256},
        "top_layers": [],
        "validity": "valid",
        "issues": [],
    }
    if conditions is not None:
        summary["conditions"] = conditions
    (path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (path / "run_metadata.json").write_text(
        json.dumps(
            {"schema": "hpx.run-metadata", "schema_version": 1, "model": {"sha256": sha256}}
        ),
        encoding="utf-8",
    )
    with (path / "profile_results.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "op", "cycles", "overflow"])
        writer.writeheader()
        for position, value in cycles.items():
            writer.writerow(
                {
                    "id": position,
                    "op": ops[position % len(ops)],
                    "cycles": value,
                    "overflow": position == overflow,
                }
            )
    plan = {
        "schema_version": plan_schema,
        "model": {"file": "kws.tflite", "sha256": plan_sha256 or sha256, "subgraph": subgraph},
        "operators": PLAN_OPERATORS if plan_ops is None else plan_ops,
    }
    for name in plan_files:
        (path / name).write_text(json.dumps(plan), encoding="utf-8")
    publish_manifest(path)
    return path


def two_bundles(tmp_path: Path) -> tuple[Path, Path]:
    auto = bundle(tmp_path / "auto", cycles={0: 12000.0, 1: 3000.0, 2: 400.0})
    wide = bundle(
        tmp_path / "wide",
        cycles={0: 9000.0, 1: 3030.0, 2: 410.0},
        plan_ops=_knob(PLAN_OPERATORS, "0:conv", "accumulation", "int16"),
    )
    return auto, wide


EXPECTED = {
    "schema_version": 1,
    "model": {"sha256": SHA, "subgraph": 0},
    "conditions": CONDITIONS,
    "operators": {
        "0:conv": {"accumulation": {"int16": {"cycles": 9000.0}, "int32": {"cycles": 12000.0}}},
        "0:fc": {
            "accumulation": {"int32": {"cycles": 3000.0}},
            "weights": {"packed": {"cycles": 3000.0}},
        },
    },
}


# -- the heliaOptimus shape, as helia_optimus.cost.calibration reads it --


class _Conditions(BaseModel):
    model_config = ConfigDict(extra="allow")
    target: str
    cpu_hz: int | None = None
    weights_memory: str = "dtcm"
    weights_cache: str = "on"


class _ValueCost(BaseModel):
    model_config = ConfigDict(extra="ignore")
    cycles: float


class _CalibrationModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sha256: str
    subgraph: int = 0


class _CalibrationFile(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: int
    model: _CalibrationModel
    conditions: _Conditions
    operators: dict[str, dict[str, dict[str, _ValueCost]]]


def test_two_bundles_price_every_knob_value_they_exercised(tmp_path: Path) -> None:
    auto, wide = two_bundles(tmp_path)
    output = tmp_path / "kws.json"

    table = calibrate([auto, wide], output=output)

    written = json.loads(output.read_text(encoding="utf-8"))
    assert written == EXPECTED
    assert "0:softmax" not in written["operators"]
    assert table.price_count == 4
    assert read_calibration(output) == table
    file = _CalibrationFile.model_validate(written)
    assert file.schema_version == CALIBRATION_SCHEMA_VERSION
    assert file.conditions == _Conditions.model_validate(CONDITIONS)
    assert file.operators["0:conv"]["accumulation"]["int16"].cycles == 9000.0


def test_an_approximate_value_is_still_priced(tmp_path: Path) -> None:
    _, wide = two_bundles(tmp_path)
    source = read_bundle(wide)
    priced = {(p.operator, p.knob, p.value): p.cycles for p in source.prices}
    assert priced[("0:conv", "accumulation", "int16")] == 9000.0


def test_bundles_of_another_model_are_refused(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    other = bundle(tmp_path / "other", cycles={0: 1.0, 1: 1.0, 2: 1.0}, sha256="b" * 64)
    with pytest.raises(CalibrationError, match="is for model bbbbbbbbbbbb subgraph 0"):
        calibrate([auto, other], output=tmp_path / "out.json")
    sub = bundle(tmp_path / "sub", cycles={0: 1.0, 1: 1.0, 2: 1.0}, subgraph=1)
    with pytest.raises(CalibrationError, match="subgraph 1"):
        calibrate([auto, sub], output=tmp_path / "out.json")


def test_a_plan_for_another_model_is_refused(tmp_path: Path) -> None:
    stale = bundle(tmp_path / "stale", cycles={0: 1.0, 1: 1.0, 2: 1.0}, plan_sha256="c" * 64)
    with pytest.raises(CalibrationError, match="compiled from model cccccccccccc"):
        read_bundle(stale)


def test_bundles_under_other_conditions_are_refused(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    mram = bundle(
        tmp_path / "mram",
        cycles={0: 1.0, 1: 1.0, 2: 1.0},
        conditions={**CONDITIONS, "weights_memory": "mram"},
    )
    with pytest.raises(CalibrationError, match="measured under conditions .*'mram'"):
        calibrate([auto, mram], output=tmp_path / "out.json")
    # Every recorded field takes part, as heliaOptimus compares them.
    loaded = bundle(
        tmp_path / "loaded",
        cycles={0: 1.0, 1: 1.0, 2: 1.0},
        conditions={**CONDITIONS, "application_load": "ble"},
    )
    with pytest.raises(CalibrationError, match="measured under conditions"):
        calibrate([auto, loaded], output=tmp_path / "out.json")


def test_a_bundle_without_conditions_or_weights_memory_is_refused(tmp_path: Path) -> None:
    old = bundle(tmp_path / "old", cycles={0: 1.0, 1: 1.0, 2: 1.0}, conditions=None)
    with pytest.raises(CalibrationError, match="records no conditions"):
        read_bundle(old)
    unplaced = bundle(
        tmp_path / "unplaced",
        cycles={0: 1.0, 1: 1.0, 2: 1.0},
        conditions={**CONDITIONS, "weights_memory": None},
    )
    with pytest.raises(CalibrationError, match="records no weights memory"):
        read_bundle(unplaced)


def test_an_overflowed_layer_is_refused(tmp_path: Path) -> None:
    hot = bundle(tmp_path / "hot", cycles={0: 1.0, 1: 1.0, 2: 1.0}, overflow=1)
    with pytest.raises(CalibrationError, match=r"layer 1 \(FULLY_CONNECTED\) overflowed"):
        read_bundle(hot)


def test_a_position_the_plan_and_the_capture_disagree_on_is_refused(tmp_path: Path) -> None:
    short = bundle(tmp_path / "short", cycles={0: 1.0, 1: 1.0})
    with pytest.raises(CalibrationError, match="operator 0:softmax at position 2 has no measured"):
        read_bundle(short)
    long = bundle(tmp_path / "long", cycles={0: 1.0, 1: 1.0, 2: 1.0, 3: 1.0})
    with pytest.raises(CalibrationError, match=r"measured layer 3 \(CONV_2D\) has no operator"):
        read_bundle(long)


def test_a_bundle_without_a_plan_is_refused(tmp_path: Path) -> None:
    bare = bundle(tmp_path / "bare", cycles={0: 1.0, 1: 1.0, 2: 1.0}, plan_files=())
    with pytest.raises(CalibrationError, match="pins no heliaAOT plan"):
        read_bundle(bare)
    two = bundle(
        tmp_path / "two",
        cycles={0: 1.0, 1: 1.0, 2: 1.0},
        plan_files=("a_plan.json", "b_plan.json"),
    )
    with pytest.raises(CalibrationError, match="pins 2 heliaAOT plans"):
        read_bundle(two)
    future = bundle(tmp_path / "future", cycles={0: 1.0, 1: 1.0, 2: 1.0}, plan_schema=2)
    with pytest.raises(CalibrationError, match="plan schema version 2"):
        read_bundle(future)


def test_an_unverified_bundle_is_refused(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    (auto / "summary.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ReportError):
        read_bundle(auto)


def test_bundles_without_any_knob_are_refused(tmp_path: Path) -> None:
    plain = bundle(
        tmp_path / "plain",
        cycles={0: 1.0, 1: 1.0, 2: 1.0},
        plan_ops=[{**op, "knobs": {}} for op in PLAN_OPERATORS],
    )
    with pytest.raises(CalibrationError, match="nothing to price"):
        calibrate([plain], output=tmp_path / "out.json")


def test_two_prices_of_one_value_must_agree_within_the_tolerance(tmp_path: Path) -> None:
    auto, wide = two_bundles(tmp_path)
    output = tmp_path / "out.json"
    # The fc accumulation price is 3000 vs 3030: one percent apart.
    calibrate([auto, wide], output=output, tolerance=0.02)
    assert json.loads(output.read_text())["operators"]["0:fc"]["accumulation"]["int32"] == {
        "cycles": 3000.0
    }
    with pytest.raises(
        CalibrationError,
        match=r"0:fc accumulation=int32 costs 3000 cycles in .*auto but 3030 in .*wide "
        r"\(1\.0% apart, tolerance 0\.5%\)",
    ):
        calibrate([auto, wide], output=output, tolerance=0.005)


def test_merge_holds_the_existing_file_to_the_same_rules(tmp_path: Path) -> None:
    auto, wide = two_bundles(tmp_path)
    output = tmp_path / "kws.json"
    calibrate([auto], output=output)
    assert "int16" not in json.loads(output.read_text())["operators"]["0:conv"]["accumulation"]

    calibrate([wide], output=output, merge=True)
    assert json.loads(output.read_text()) == EXPECTED

    disagreeing = bundle(tmp_path / "disagreeing", cycles={0: 12000.0, 1: 4000.0, 2: 400.0})
    with pytest.raises(CalibrationError, match=r"costs 3000 cycles in .*kws\.json but 4000"):
        calibrate([disagreeing], output=output, merge=True)
    assert json.loads(output.read_text()) == EXPECTED

    with pytest.raises(CalibrationError, match="Nothing to merge into"):
        calibrate([auto], output=tmp_path / "missing.json", merge=True)


def test_merge_refuses_a_file_for_another_model_or_conditions(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    output = tmp_path / "kws.json"
    output.write_text(
        json.dumps(
            CalibrationTable(
                model_sha256="d" * 64,
                subgraph=0,
                conditions=CONDITIONS,
                operators={"0:conv": {"accumulation": {"int32": 1.0}}},
            ).to_dict()
        )
    )
    with pytest.raises(CalibrationError, match="kws.json is for model dddddddddddd"):
        calibrate([auto], output=output, merge=True)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("[]", "not a JSON object"),
        ('{"schema_version": 2}', "schema version 2"),
        ('{"schema_version": 1, "model": {"sha256": "a"}}', "malformed"),
        (
            '{"schema_version": 1, "model": {"sha256": "a", "subgraph": 0}, '
            '"conditions": {}, "operators": {"x": {"k": {"v": {}}}}}',
            "no numeric cycles",
        ),
    ],
)
def test_a_malformed_existing_file_is_refused(tmp_path: Path, document: str, message: str) -> None:
    output = tmp_path / "bad.json"
    output.write_text(document, encoding="utf-8")
    with pytest.raises(CalibrationError, match=message):
        read_calibration(output)


def test_the_command_writes_the_file_and_reports_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    auto, wide = two_bundles(tmp_path)
    output = tmp_path / "kws.json"

    result = runner.invoke(app, ["calibrate", str(auto), str(wide), "--output", str(output)])

    assert result.exit_code == 0, result.output
    assert "2 operators, 4 prices" in result.output
    assert json.loads(output.read_text(encoding="utf-8")) == EXPECTED

    strict = runner.invoke(
        app, ["calibrate", str(auto), str(wide), "-o", str(output), "--tolerance", "0.005"]
    )
    assert strict.exit_code == 1
    assert "costs 3000 cycles" in strict.output and "tolerance 0.5%" in strict.output

    help_text = runner.invoke(app, ["calibrate", "--help"]).output
    assert "without knobs" in help_text and "left out" in help_text
    assert "--merge" in help_text and "--tolerance" in help_text
