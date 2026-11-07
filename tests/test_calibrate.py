"""``hpx calibrate`` (#648): synthetic bundles, every rule, and heliaOptimus's shape."""

from __future__ import annotations

import csv
import dataclasses
import hashlib
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
    merge_sources,
    read_bundle,
    read_calibration,
)
from helia_profiler.cli.app import app
from helia_profiler.errors import CalibrationError, ReportError
from helia_profiler.results import ResultValidity
from helia_profiler.results.manifest import ResultIssue, load_result_manifest
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
OPS = ("CONV_2D", "FULLY_CONNECTED", "SOFTMAX")

#: Three operators: one knob, two knobs, none.
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

#: What every synthetic bundle records of its capture setup.
DIMENSIONS = {
    "board": None,
    "soc": None,
    "cpu_clock": None,
    "engine": "helia-aot",
    "engine_version": "0.33.0",
    "engine_backend": None,
    "toolchain": "arm-none-eabi-gcc",
    "compiler_version": None,
    "link_family": "gnu",
    "architecture_flags": None,
    "arena_location": None,
    "kernel_library": "cmsis-nn",
    "kernel_version": "7.0.0",
    "kernel_commit": "c" * 40,
    "arena_memory": "DTCM",
}


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
    ops: tuple[str, ...] = OPS,
    plan_ops: list[dict[str, Any]] | None = None,
    conditions: dict[str, Any] | None = CONDITIONS,
    sha256: str = SHA,
    plan_sha256: str | None = None,
    subgraph: int = 0,
    overflow: int | None = None,
    plan_schema: int = 1,
    plan_files: tuple[str, ...] = ("hpx_plan.json",),
    toolchain: str = "arm-none-eabi-gcc",
    engine_version: str = "0.33.0",
    kernel_version: str = "7.0.0",
    kernel_commit: str = "c" * 40,
    arena_region: str = "DTCM",
    layer_cycles_text: str | None = None,
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
        "identity": {
            "model_sha256": sha256,
            "kernels": {"library": "cmsis-nn", "version": kernel_version, "commit": kernel_commit},
        },
        "top_layers": [],
        "memory_plan": {
            "engine": "helia-aot",
            "regions": [
                {
                    "region": arena_region,
                    "consumers": [{"name": "scratch", "size": 1024, "kind": "arena"}],
                },
                {"region": "MRAM", "consumers": [{"name": "code", "size": 1, "kind": "code"}]},
            ],
        },
        "validity": "valid",
        "issues": [],
    }
    if conditions is not None:
        summary["conditions"] = conditions
    (path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    metadata = {
        "schema": "hpx.run-metadata",
        "schema_version": 1,
        "model": {"sha256": sha256},
        "config": {"engine": {"type": "helia-aot"}, "target": {"toolchain": toolchain}},
        "engine": {"type": "helia-aot", "version": engine_version},
        "platform": {"link_family": "gnu"},
    }
    (path / "run_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    with (path / "profile_results.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "op", "cycles", "overflow"])
        writer.writeheader()
        for position, value in cycles.items():
            writer.writerow(
                {
                    "id": position,
                    "op": ops[position % len(ops)],
                    "cycles": layer_cycles_text if layer_cycles_text and position == 1 else value,
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


def _republish(path: Path, **changes: Any) -> None:
    """Rewrite the manifest with *changes*, re-hashing the files it declares."""
    manifest = load_result_manifest(path / "result_manifest.json", verify=False)
    artifacts = tuple(
        dataclasses.replace(
            artifact,
            size_bytes=(path / artifact.path).stat().st_size,
            sha256=hashlib.sha256((path / artifact.path).read_bytes()).hexdigest(),
        )
        for artifact in manifest.artifacts
    )
    dataclasses.replace(manifest, artifacts=artifacts, **changes).write(
        path / "result_manifest.json"
    )


def _mark(path: Path, validity: ResultValidity, *codes: str, severity: str = "error") -> None:
    issues = tuple(
        ResultIssue(code=code, severity=severity, message="The run is not a measurement.")
        for code in codes
    )
    summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    summary["validity"] = validity.value
    summary["issues"] = [dataclasses.asdict(issue) for issue in issues]
    (path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    _republish(path, validity=validity, issues=issues)


def two_bundles(tmp_path: Path) -> tuple[Path, Path]:
    """One bundle per value of the convolution's accumulation; the fc knobs hold still."""
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
    "capture": {"dimensions": DIMENSIONS, "knobs": {}, "held": {}},
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


def test_two_bundles_price_the_knob_they_swept_and_the_knobs_that_held(tmp_path: Path) -> None:
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
    assert not any(output.parent.glob("kws.json*tmp*"))


def test_an_approximate_value_is_still_priced(tmp_path: Path) -> None:
    _, wide = two_bundles(tmp_path)
    (sample,) = [s for s in read_bundle(wide).samples if s.operator == "0:conv"]
    assert sample.knobs == {"accumulation": "int16"} and sample.cycles == 9000.0


def test_one_knob_of_a_two_knob_operator_can_be_swept(tmp_path: Path) -> None:
    held = bundle(tmp_path / "packed", cycles={0: 1.0, 1: 3000.0, 2: 1.0})
    swept = bundle(
        tmp_path / "flat",
        cycles={0: 1.0, 1: 2500.0, 2: 1.0},
        plan_ops=_knob(PLAN_OPERATORS, "0:fc", "weights", "flat"),
    )
    output = tmp_path / "out.json"

    calibrate([held, swept], output=output)

    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["operators"]["0:fc"] == {
        "weights": {"flat": {"cycles": 2500.0}, "packed": {"cycles": 3000.0}}
    }
    assert written["capture"]["knobs"] == {"0:fc": {"accumulation": "int32"}}
    assert written["capture"]["held"] == {}


def test_two_knobs_of_one_operator_may_not_both_change(tmp_path: Path) -> None:
    auto = bundle(tmp_path / "auto", cycles={0: 1.0, 1: 3000.0, 2: 1.0})
    both = bundle(
        tmp_path / "both",
        cycles={0: 1.0, 1: 2000.0, 2: 1.0},
        plan_ops=_knob(
            _knob(PLAN_OPERATORS, "0:fc", "weights", "flat"), "0:fc", "accumulation", "int16"
        ),
    )
    with pytest.raises(
        CalibrationError,
        match=r"Operator 0:fc ran with different values of knobs accumulation, weights",
    ) as caught:
        calibrate([auto, both], output=tmp_path / "out.json")
    assert "Sweep one knob at a time" in str(caught.value)


def test_merge_holds_the_other_knobs_of_an_existing_file_still(tmp_path: Path) -> None:
    held = bundle(tmp_path / "packed", cycles={0: 1.0, 1: 3000.0, 2: 1.0})
    swept = bundle(
        tmp_path / "flat",
        cycles={0: 1.0, 1: 2500.0, 2: 1.0},
        plan_ops=_knob(PLAN_OPERATORS, "0:fc", "weights", "flat"),
    )
    output = tmp_path / "out.json"
    calibrate([held, swept], output=output)
    other = bundle(
        tmp_path / "int16",
        cycles={0: 1.0, 1: 2000.0, 2: 1.0},
        plan_ops=_knob(PLAN_OPERATORS, "0:fc", "accumulation", "int16"),
    )
    with pytest.raises(CalibrationError, match="0:fc ran with different values of knobs"):
        calibrate([other], output=output, merge=True)


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


@pytest.mark.parametrize(
    ("change", "name", "values"),
    [
        ({"toolchain": "armclang"}, "toolchain", ("'armclang'", "'arm-none-eabi-gcc'")),
        ({"engine_version": "0.34.0"}, "engine_version", ("'0.34.0'", "'0.33.0'")),
        ({"kernel_version": "7.1.0"}, "kernel_version", ("'7.1.0'", "'7.0.0'")),
        ({"arena_region": "SRAM"}, "arena_memory", ("'SRAM'", "'DTCM'")),
    ],
)
def test_captures_from_another_setup_are_refused(
    tmp_path: Path, change: dict[str, Any], name: str, values: tuple[str, str]
) -> None:
    auto, _ = two_bundles(tmp_path)
    other = bundle(tmp_path / "other", cycles={0: 1.0, 1: 1.0, 2: 1.0}, **change)
    with pytest.raises(CalibrationError, match=f"has {name} {values[0]};.*has {values[1]}"):
        calibrate([auto, other], output=tmp_path / "out.json")
    # The file remembers its setup, so a later merge is held to it too.
    output = tmp_path / "kws.json"
    calibrate([auto], output=output)
    assert json.loads(output.read_text())["capture"]["dimensions"] == DIMENSIONS
    with pytest.raises(CalibrationError, match=f"has {name} {values[0]};.*kws.json has"):
        calibrate([other], output=output, merge=True)


def _hand_written(path: Path, operators: dict[str, Any]) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model": {"sha256": SHA, "subgraph": 0},
                "conditions": CONDITIONS,
                "operators": operators,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_a_file_that_records_no_setup_takes_the_bundles_setup(tmp_path: Path) -> None:
    auto, wide = two_bundles(tmp_path)
    output = _hand_written(
        tmp_path / "hand.json", {"0:conv": {"accumulation": {"int16": {"cycles": 9000}}}}
    )

    table = calibrate([auto], output=output, merge=True)

    assert table.operators["0:conv"]["accumulation"] == {"int16": 9000.0, "int32": 12000.0}
    assert table.dimensions == DIMENSIONS
    assert json.loads(output.read_text())["capture"]["dimensions"] == DIMENSIONS
    # The bundles must still agree among themselves, file or no file.
    other = bundle(tmp_path / "other", cycles={0: 1.0, 1: 1.0, 2: 1.0}, toolchain="armclang")
    hand = _hand_written(
        tmp_path / "hand2.json", {"0:conv": {"accumulation": {"int16": {"cycles": 9000}}}}
    )
    with pytest.raises(CalibrationError, match="has toolchain 'armclang';.*auto has"):
        calibrate([auto, other], output=hand, merge=True)
    assert "capture" not in json.loads(hand.read_text())


def test_merge_never_replaces_a_price_the_file_holds(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    uneven = _hand_written(
        tmp_path / "uneven.json",
        {
            "0:fc": {
                "accumulation": {"int32": {"cycles": 3000}},
                "weights": {"packed": {"cycles": 5000}},
            }
        },
    )
    with pytest.raises(CalibrationError, match="0:fc prices one vector .* at different cycles"):
        calibrate([auto], output=uneven, merge=True)
    assert json.loads(uneven.read_text())["operators"]["0:fc"]["weights"]["packed"] == {
        "cycles": 5000
    }
    even = _hand_written(
        tmp_path / "even.json",
        {
            "0:fc": {
                "accumulation": {"int32": {"cycles": 5000}},
                "weights": {"packed": {"cycles": 5000}},
            }
        },
    )
    with pytest.raises(
        CalibrationError, match=r"0:fc accumulation=int32 costs 5000 cycles in .*even.json but 3000"
    ):
        calibrate([auto], output=even, merge=True)
    agreeing = bundle(tmp_path / "agreeing", cycles={0: 12000.0, 1: 5050.0, 2: 1.0})
    table = calibrate([agreeing], output=even, merge=True)
    assert table.operators["0:fc"] == {
        "accumulation": {"int32": 5000.0},
        "weights": {"packed": 5000.0},
    }


def test_an_operator_whose_knob_set_differs_is_refused(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    extra = json.loads(json.dumps(PLAN_OPERATORS))
    extra[1]["knobs"]["tiling"] = {"value": "rows"}
    tiled = bundle(tmp_path / "tiled", cycles={0: 1.0, 1: 1.0, 2: 1.0}, plan_ops=extra)
    message = (
        r"Operator 0:fc has knobs \['accumulation', 'tiling', 'weights'\] in .*tiled but "
        r"\['accumulation', 'weights'\] in .*auto"
    )
    with pytest.raises(CalibrationError, match=message):
        calibrate([auto, tiled], output=tmp_path / "out.json")
    with pytest.raises(CalibrationError, match="Operator 0:fc has knobs"):
        calibrate([tiled, auto], output=tmp_path / "out.json")
    partial = _hand_written(
        tmp_path / "partial.json", {"0:fc": {"accumulation": {"int32": {"cycles": 3000}}}}
    )
    with pytest.raises(
        CalibrationError, match=r"has knobs \['accumulation', 'weights'\] in .*auto"
    ):
        calibrate([auto], output=partial, merge=True)


def _with_fixed(ops: list[dict[str, Any]], layout: str) -> list[dict[str, Any]]:
    out = json.loads(json.dumps(ops))
    out[0]["fixed"] = {"weight_layout": {"value": layout, "set_by": "placement"}}
    out[0]["kernel"] = {"entry": f"arm_convolve_{layout}", "provider": None}
    return out


def test_a_fixed_choice_or_kernel_that_moved_is_refused(tmp_path: Path) -> None:
    standard = bundle(
        tmp_path / "standard",
        cycles={0: 12000.0, 1: 3000.0, 2: 400.0},
        plan_ops=_with_fixed(PLAN_OPERATORS, "standard"),
    )
    packed = bundle(
        tmp_path / "packed",
        cycles={0: 7000.0, 1: 3000.0, 2: 400.0},
        plan_ops=_with_fixed(_knob(PLAN_OPERATORS, "0:conv", "accumulation", "int16"), "packed"),
    )
    with pytest.raises(
        CalibrationError,
        match="Operator 0:conv ran with different fixed:weight_layout, kernel:entry in .*packed",
    ):
        calibrate([standard, packed], output=tmp_path / "out.json")
    # Held still, the choices are recorded with the prices and checked on merge.
    output = tmp_path / "kws.json"
    calibrate([standard], output=output)
    assert json.loads(output.read_text())["capture"]["held"] == {
        "0:conv": {"fixed:weight_layout": "standard", "kernel:entry": "arm_convolve_standard"}
    }
    with pytest.raises(CalibrationError, match="different fixed:weight_layout, kernel:entry"):
        calibrate([packed], output=output, merge=True)
    other_commit = bundle(
        tmp_path / "commit", cycles={0: 1.0, 1: 1.0, 2: 1.0}, kernel_commit="d" * 40
    )
    with pytest.raises(CalibrationError, match="has kernel_commit 'dddd"):
        calibrate([standard, other_commit], output=tmp_path / "out.json")


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


def test_a_run_the_profiler_marked_invalid_is_refused(tmp_path: Path) -> None:
    bad = bundle(tmp_path / "bad", cycles={0: 12000.0, 1: 3000.0, 2: 400.0})
    _mark(bad, ResultValidity.INVALID, "firmware.model_mismatch")
    assert load_result_manifest(bad / "result_manifest.json", verify=True).validity.value == (
        "invalid"
    )
    with pytest.raises(
        CalibrationError, match=r"Bundle .*bad is invalid: firmware\.model_mismatch\."
    ):
        read_bundle(bad)

    degraded = bundle(tmp_path / "degraded", cycles={0: 12000.0, 1: 3000.0, 2: 400.0})
    _mark(degraded, ResultValidity.DEGRADED)
    assert read_bundle(degraded).samples


@pytest.mark.parametrize(
    "code", ["profile.clean_window_clock_rate_low", "profile.clean_window_stalled"]
)
def test_a_run_whose_cycle_counter_is_suspect_is_refused(tmp_path: Path, code: str) -> None:
    slow = bundle(tmp_path / "slow", cycles={0: 12000.0, 1: 3000.0, 2: 400.0})
    _mark(slow, ResultValidity.DEGRADED, code, severity="warning")
    with pytest.raises(CalibrationError, match=f"Bundle .*slow is degraded: {code}"):
        read_bundle(slow)


def test_a_run_whose_power_capture_failed_is_priced(tmp_path: Path) -> None:
    dark = bundle(tmp_path / "dark", cycles={0: 12000.0, 1: 3000.0, 2: 400.0})
    _mark(dark, ResultValidity.INVALID, "power.observation_missing")
    assert read_bundle(dark).samples


def test_an_overflowed_layer_is_refused(tmp_path: Path) -> None:
    hot = bundle(tmp_path / "hot", cycles={0: 1.0, 1: 1.0, 2: 1.0}, overflow=1)
    with pytest.raises(CalibrationError, match=r"layer 1 \(FULLY_CONNECTED\) overflowed"):
        read_bundle(hot)


@pytest.mark.parametrize("cycles", [float("nan"), float("inf"), -5.0])
def test_a_cycle_count_that_is_not_one_is_refused(tmp_path: Path, cycles: float) -> None:
    odd = bundle(tmp_path / "odd", cycles={0: 1.0, 1: cycles, 2: 1.0})
    with pytest.raises(CalibrationError, match=r"layer 1 \(FULLY_CONNECTED\) has no finite"):
        read_bundle(odd)


def test_a_cycle_count_too_large_for_a_float_is_refused(tmp_path: Path) -> None:
    huge = "9" * 401
    big = bundle(tmp_path / "big", cycles={0: 1.0, 1: 1.0, 2: 1.0}, layer_cycles_text=huge)
    with pytest.raises(CalibrationError, match=r"layer 1 \(FULLY_CONNECTED\) has no finite"):
        read_bundle(big)
    output = tmp_path / "big.json"
    output.write_text(
        '{"schema_version": 1, "model": {"sha256": "a", "subgraph": 0}, "conditions": {}, '
        '"operators": {"x": {"k": {"v": {"cycles": ' + huge + "}}}}}",
        encoding="utf-8",
    )
    with pytest.raises(CalibrationError, match="no finite, non-negative cycles"):
        read_calibration(output)


def test_a_position_the_plan_and_the_capture_disagree_on_is_refused(tmp_path: Path) -> None:
    short = bundle(tmp_path / "short", cycles={0: 1.0, 1: 1.0})
    with pytest.raises(CalibrationError, match="operator 0:softmax at position 2 has no measured"):
        read_bundle(short)
    long = bundle(tmp_path / "long", cycles={0: 1.0, 1: 1.0, 2: 1.0, 3: 1.0})
    with pytest.raises(CalibrationError, match=r"measured layer 3 \(CONV_2D\) has no operator"):
        read_bundle(long)


def test_an_operator_type_the_plan_and_the_capture_disagree_on_is_refused(tmp_path: Path) -> None:
    swapped = bundle(
        tmp_path / "swapped",
        cycles={0: 1.0, 1: 1.0, 2: 1.0},
        ops=("SOFTMAX:5", "FULLY_CONNECTED:1", "CONV_2D:-1"),
    )
    with pytest.raises(
        CalibrationError, match="plan has conv_2d at position 0 but the capture measured SOFTMAX:5"
    ):
        read_bundle(swapped)
    # A suffixed label and a differently cased key are the same operator type.
    labelled = bundle(
        tmp_path / "labelled",
        cycles={0: 1.0, 1: 1.0, 2: 1.0},
        ops=("CONV_2D:0", "FULLY_CONNECTED:1", "SOFTMAX:-1"),
    )
    assert read_bundle(labelled).samples


def test_a_plan_that_lists_a_position_or_an_operator_twice_is_refused(tmp_path: Path) -> None:
    twice = json.loads(json.dumps(PLAN_OPERATORS))
    twice[1]["index"] = 0
    doubled = bundle(tmp_path / "doubled", cycles={0: 1.0, 1: 1.0, 2: 1.0}, plan_ops=twice)
    with pytest.raises(CalibrationError, match="lists position 0 twice"):
        read_bundle(doubled)
    same = json.loads(json.dumps(PLAN_OPERATORS))
    same[1]["id"] = "0:conv"
    renamed = bundle(tmp_path / "renamed", cycles={0: 1.0, 1: 1.0, 2: 1.0}, plan_ops=same)
    with pytest.raises(CalibrationError, match="lists operator 0:conv twice"):
        read_bundle(renamed)


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
    with pytest.raises(CalibrationError, match="No result bundle"):
        merge_sources([])
    with pytest.raises(CalibrationError, match="No result bundle"):
        calibrate([], output=tmp_path / "out.json")


def test_two_prices_of_one_value_must_agree_within_the_tolerance(tmp_path: Path) -> None:
    auto, wide = two_bundles(tmp_path)
    output = tmp_path / "out.json"
    # The fc prices are 3000 vs 3030: one percent apart.
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


@pytest.mark.parametrize("tolerance", [float("nan"), float("inf"), -0.1, 1.0, 2.0])
def test_a_tolerance_that_is_not_a_fraction_is_refused(tmp_path: Path, tolerance: float) -> None:
    auto, _ = two_bundles(tmp_path)
    with pytest.raises(CalibrationError, match="--tolerance must be a fraction"):
        calibrate([auto], output=tmp_path / "out.json", tolerance=tolerance)


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


def test_merge_refuses_a_file_for_another_model(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    output = tmp_path / "kws.json"
    output.write_text(
        json.dumps(
            CalibrationTable(
                model_sha256="d" * 64,
                subgraph=0,
                conditions=CONDITIONS,
                operators={"0:conv": {"accumulation": {"int32": 1.0}}},
                dimensions={},
                contexts={},
                held={},
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
            "no finite, non-negative cycles",
        ),
        (
            '{"schema_version": 1, "model": {"sha256": "a", "subgraph": 0}, '
            '"conditions": {}, "operators": {"x": {"k": {"v": {"cycles": NaN}}}}}',
            "no finite, non-negative cycles",
        ),
        (
            '{"schema_version": 1, "model": {"sha256": "a", "subgraph": 0}, '
            '"conditions": {}, "operators": {"x": {"k": {"v": {"cycles": -1}}}}}',
            "no finite, non-negative cycles",
        ),
    ],
)
def test_a_malformed_existing_file_is_refused(tmp_path: Path, document: str, message: str) -> None:
    output = tmp_path / "bad.json"
    output.write_text(document, encoding="utf-8")
    with pytest.raises(CalibrationError, match=message):
        read_calibration(output)


def test_a_file_that_cannot_say_which_knob_was_swept_is_refused(tmp_path: Path) -> None:
    output = tmp_path / "wide.json"
    output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model": {"sha256": SHA, "subgraph": 0},
                "conditions": CONDITIONS,
                "operators": {
                    "0:fc": {
                        "accumulation": {"int32": {"cycles": 1}, "int16": {"cycles": 2}},
                        "weights": {"packed": {"cycles": 1}, "flat": {"cycles": 3}},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    auto, _ = two_bundles(tmp_path)
    with pytest.raises(CalibrationError, match="cannot say which knob was swept"):
        calibrate([auto], output=output, merge=True)


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

    lax = runner.invoke(app, ["calibrate", str(auto), "-o", str(output), "--tolerance", "nan"])
    assert lax.exit_code == 1 and "--tolerance must be a fraction" in lax.output

    directory = runner.invoke(app, ["calibrate", str(auto), "-o", str(tmp_path)])
    assert directory.exit_code == 1, directory.output
    assert "Could not write" in directory.output
    assert not any(tmp_path.glob(f"{tmp_path.name}*"))

    help_text = runner.invoke(app, ["calibrate", "--help"]).output
    assert "without knobs" in help_text and "left out" in help_text
    assert "--merge" in help_text and "--tolerance" in help_text
    assert "application_load=none" in help_text
    assert "kws-sram" not in help_text


def test_the_file_follows_the_umask_and_never_replaces_a_link(tmp_path: Path) -> None:
    import os
    import stat

    auto, _ = two_bundles(tmp_path)
    previous = os.umask(0o027)
    try:
        output = tmp_path / "kws.json"
        calibrate([auto], output=output)
        assert stat.S_IMODE(output.stat().st_mode) == 0o640
    finally:
        os.umask(previous)
    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(CalibrationError, match="link.json is a symbolic link"):
        calibrate([auto], output=link)
    assert link.is_symlink() and real.read_text(encoding="utf-8") == "{}"
