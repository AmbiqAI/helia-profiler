"""``hpx calibrate`` (#648): synthetic and real plans, every rule, and heliaOptimus's shape."""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from typer.testing import CliRunner

from helia_profiler.calibration import (
    CALIBRATION_SCHEMA_VERSION,
    CAPTURE_DIMENSIONS,
    CalibrationTable,
    Price,
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
    **{dimension.value: None for dimension in CAPTURE_DIMENSIONS},
    "model_sha256": SHA,
    "link_family": "gnu",
    "toolchain": "arm-none-eabi-gcc",
    "engine_version": "0.33.0",
    "kernel_version": "7.0.0",
    "kernel_commit": "c" * 40,
    "arena_memory": "DTCM",
}

PLANS = Path(__file__).parent / "fixtures" / "calibration"


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
    link_family: str = "gnu",
    kernel_version: Any = "7.0.0",
    kernel_commit: str = "c" * 40,
    arena_region: str = "DTCM",
    layer_cycles_text: str | None = None,
    power: dict[str, Any] | None = None,
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
    if power is not None:
        summary["power"] = power
    (path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    metadata = {
        "schema": "hpx.run-metadata",
        "schema_version": 1,
        "model": {"sha256": sha256},
        "config": {"engine": {"type": "helia-aot"}, "target": {"toolchain": toolchain}},
        "engine": {"type": "helia-aot", "version": engine_version},
        "platform": {"link_family": link_family},
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


def _hand_written(path: Path, operators: dict[str, Any], sha256: str = SHA) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model": {"sha256": sha256, "subgraph": 0},
                "conditions": CONDITIONS,
                "operators": operators,
            }
        ),
        encoding="utf-8",
    )
    return path


def _real_bundle(path: Path, plan_name: str, *, offset: float = 0.0, **changes: Any) -> Path:
    """A bundle around one of heliaAOT 0.33's own plans for the kws models."""
    plan = json.loads((PLANS / f"{plan_name}_plan.json").read_text(encoding="utf-8"))
    ops = plan["operators"]
    return bundle(
        path,
        cycles={op["index"]: 1000.0 + op["index"] + offset for op in ops},
        ops=tuple(op["op_type"] for op in ops),
        plan_ops=ops,
        sha256=plan["model"]["sha256"],
        **changes,
    )


def two_bundles(tmp_path: Path) -> tuple[Path, Path]:
    """One bundle per value of the convolution's accumulation; the fc knobs hold still."""
    auto = bundle(tmp_path / "auto", cycles={0: 12000.0, 1: 3000.0, 2: 400.0})
    wide = bundle(
        tmp_path / "wide",
        cycles={0: 9000.0, 1: 3030.0, 2: 410.0},
        plan_ops=_knob(PLAN_OPERATORS, "0:conv", "accumulation", "int16"),
    )
    return auto, wide


def _price(cycles: float, **others: str) -> dict[str, Any]:
    return {"others": others, "facts": {}}


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
    "capture": {
        "dimensions": DIMENSIONS,
        "prices": {
            "0:conv": {"accumulation": {"int32": _price(12000.0), "int16": _price(9000.0)}},
            "0:fc": {
                "accumulation": {"int32": _price(3000.0, weights="packed")},
                "weights": {"packed": _price(3000.0, accumulation="int32")},
            },
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


def test_two_bundles_price_every_knob_value_under_its_vector(tmp_path: Path) -> None:
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
    assert not any(output.parent.glob(".kws.json.*"))


def test_an_approximate_value_is_still_priced(tmp_path: Path) -> None:
    _, wide = two_bundles(tmp_path)
    (sample,) = [s for s in read_bundle(wide).samples if s.operator == "0:conv"]
    assert sample.knobs == {"accumulation": "int16"} and sample.cycles == 9000.0


@pytest.mark.parametrize(
    ("model", "knob", "values", "prices"),
    [
        ("kws_int8", "kernel", ("specialized", "generic"), 20),
        ("kws_fp16", "accumulation", ("precise", "fast"), 8),
    ],
)
def test_a_real_knob_sweep_prices_every_operator_the_knob_moves(
    tmp_path: Path, model: str, knob: str, values: tuple[str, str], prices: int
) -> None:
    """heliaAOT's knobs exist to change the kernel entry and the choices that follow it."""
    first = _real_bundle(tmp_path / values[0], f"{model}_{knob}_{values[0]}")
    second = _real_bundle(tmp_path / values[1], f"{model}_{knob}_{values[1]}", offset=500.0)
    output = tmp_path / f"{model}.json"

    table = calibrate([first, second], output=output)

    assert table.price_count == prices
    written = json.loads(output.read_text(encoding="utf-8"))
    for operator, knobs in written["operators"].items():
        assert set(knobs) == {knob} and set(knobs[knob]) == set(values), operator
    # What the plan reported under each value is recorded with the price, for the reader.
    for value in values:
        plan = json.loads((PLANS / f"{model}_{knob}_{value}_plan.json").read_text())
        for op in plan["operators"]:
            if not op["knobs"]:
                continue
            entry = written["capture"]["prices"][op["id"]][knob][value]
            assert entry["others"] == {}
            assert entry["facts"]["kernel:entry"] == op["kernel"]["entry"]
            for name, choice in op["fixed"].items():
                assert entry["facts"][f"fixed:{name}"] == choice["value"]
    # Merging the same bundles again changes nothing.
    assert calibrate([first, second], output=output, merge=True) == table


def test_a_hand_written_file_merges_with_a_real_plan_and_again(tmp_path: Path) -> None:
    real = _real_bundle(tmp_path / "specialized", "kws_int8_kernel_specialized")
    generic = _real_bundle(tmp_path / "generic", "kws_int8_kernel_generic", offset=500.0)
    plan = json.loads((PLANS / "kws_int8_kernel_specialized_plan.json").read_text())
    conv = plan["operators"][0]["id"]
    hand = _hand_written(
        tmp_path / "hand.json",
        {conv: {"kernel": {"generic": {"cycles": 1500}}}},
        sha256=plan["model"]["sha256"],
    )

    table = calibrate([real], output=hand, merge=True)

    assert table.operators[conv]["kernel"] == {"generic": 1500.0, "specialized": 1000.0}
    assert table.dimensions == {**DIMENSIONS, "model_sha256": plan["model"]["sha256"]}
    written = json.loads(hand.read_text(encoding="utf-8"))
    assert "generic" not in written["capture"]["prices"][conv]["kernel"]
    assert written["capture"]["prices"][conv]["kernel"]["specialized"]["others"] == {}
    # The hand-written price gains its vector when a bundle prices the same key.
    again = calibrate([generic], output=hand, merge=True)
    assert again.prices[(conv, "kernel", "generic")] == Price(
        1500.0, {}, {"kernel:entry": "arm_convolve_s8"}
    )
    assert calibrate([generic, real], output=hand, merge=True) == again


def test_merge_then_merge_again(tmp_path: Path) -> None:
    """The documented workflow: write, --merge, --merge the same bundle."""
    auto = bundle(tmp_path / "auto", cycles={0: 12000.0, 1: 3000.0, 2: 400.0})
    output = tmp_path / "cal.json"
    calibrate([auto], output=output)
    flat = bundle(
        tmp_path / "flat",
        cycles={0: 12000.0, 1: 2500.0, 2: 400.0},
        plan_ops=_knob(PLAN_OPERATORS, "0:fc", "weights", "flat"),
    )

    once = calibrate([flat], output=output, merge=True)
    twice = calibrate([flat], output=output, merge=True)

    assert once == twice
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["operators"]["0:fc"] == {
        "accumulation": {"int32": {"cycles": 3000.0}},
        "weights": {"flat": {"cycles": 2500.0}, "packed": {"cycles": 3000.0}},
    }
    # The file holds one price per value: accumulation=int32 keeps the price
    # measured with weights=packed, and records that vector.
    assert written["capture"]["prices"]["0:fc"]["accumulation"]["int32"]["others"] == {
        "weights": "packed"
    }
    assert written["capture"]["prices"]["0:fc"]["weights"]["flat"]["others"] == {
        "accumulation": "int32"
    }


def test_every_knob_is_priced_under_the_vector_it_ran_with(tmp_path: Path) -> None:
    auto = bundle(tmp_path / "auto", cycles={0: 1.0, 1: 3000.0, 2: 1.0})
    both = bundle(
        tmp_path / "both",
        cycles={0: 1.0, 1: 2000.0, 2: 1.0},
        plan_ops=_knob(
            _knob(PLAN_OPERATORS, "0:fc", "weights", "flat"), "0:fc", "accumulation", "int16"
        ),
    )
    table = calibrate([auto, both], output=tmp_path / "out.json")
    assert table.prices[("0:fc", "accumulation", "int16")] == Price(2000.0, {"weights": "flat"})
    assert table.prices[("0:fc", "weights", "flat")] == Price(2000.0, {"accumulation": "int16"})
    assert table.prices[("0:fc", "weights", "packed")] == Price(3000.0, {"accumulation": "int32"})


def test_facts_of_one_vector_are_recorded_once(tmp_path: Path) -> None:
    def with_entry(entry: str) -> list[dict[str, Any]]:
        ops = json.loads(json.dumps(PLAN_OPERATORS))
        ops[0]["kernel"] = {"entry": entry}
        ops[0]["fixed"] = {"weight_layout": {"value": "packed", "set_by": "heuristic"}}
        return ops

    one = bundle(tmp_path / "one", cycles={0: 12000.0, 1: 1.0, 2: 1.0}, plan_ops=with_entry("a"))
    same = bundle(tmp_path / "same", cycles={0: 12100.0, 1: 1.0, 2: 1.0}, plan_ops=with_entry("a"))
    table = calibrate([one, same], output=tmp_path / "out.json")
    assert table.prices[("0:conv", "accumulation", "int32")].facts == {
        "fixed:weight_layout": "packed",
        "kernel:entry": "a",
    }
    other = bundle(
        tmp_path / "other", cycles={0: 12000.0, 1: 1.0, 2: 1.0}, plan_ops=with_entry("b")
    )
    with pytest.raises(
        CalibrationError,
        match=r"0:conv accumulation=int32 with the other knobs at \{\} reports .*'kernel:entry': 'b'.* "
        r"in .*other but .*'kernel:entry': 'a'.* in .*one",
    ):
        calibrate([one, other], output=tmp_path / "out.json")


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


UNTAGGED = {"unknown": "the lock states no release tag for ns-cmsis-nn"}


@pytest.mark.parametrize(
    ("change", "name", "values"),
    [
        ({"link_family": "armlink"}, "link_family", ("'armlink'", "'gnu'")),
        ({"kernel_version": "7.1.0"}, "kernel_version", ("'7.1.0'", "'7.0.0'")),
        ({"kernel_commit": "d" * 40}, "kernel_commit", ("'dddd", "'cccc")),
    ],
)
def test_captures_from_another_setup_are_refused(
    tmp_path: Path, change: dict[str, Any], name: str, values: tuple[str, str]
) -> None:
    auto, _ = two_bundles(tmp_path)
    other = bundle(tmp_path / "other", cycles={0: 1.0, 1: 1.0, 2: 1.0}, **change)
    with pytest.raises(CalibrationError, match=f"has {name} {values[0]}.*;.*has {values[1]}"):
        calibrate([auto, other], output=tmp_path / "out.json")
    # The file remembers its setup, so a later merge is held to it too.
    output = tmp_path / "kws.json"
    calibrate([auto], output=output)
    assert json.loads(output.read_text())["capture"]["dimensions"] == DIMENSIONS
    with pytest.raises(CalibrationError, match=f"has {name} {values[0]}.*;.*kws.json has"):
        calibrate([other], output=output, merge=True)


def test_the_kernel_commit_tells_untagged_builds_apart(tmp_path: Path) -> None:
    """A SHA-pinned ns-cmsis-nn records no release tag, so only the commit identifies it."""
    before = bundle(
        tmp_path / "before",
        cycles={0: 12000.0, 1: 3000.0, 2: 400.0},
        kernel_version=UNTAGGED,
        kernel_commit="a" * 40,
    )
    after = bundle(
        tmp_path / "after",
        cycles={0: 12000.0, 1: 2500.0, 2: 400.0},
        kernel_version=UNTAGGED,
        kernel_commit="b" * 40,
        plan_ops=_knob(PLAN_OPERATORS, "0:fc", "weights", "flat"),
    )
    with pytest.raises(CalibrationError, match="has kernel_commit 'bbbb"):
        calibrate([before, after], output=tmp_path / "out.json")
    # One side untagged, same commit: the version cannot differ, the build is one.
    tagged = bundle(
        tmp_path / "tagged", cycles={0: 12000.0, 1: 3000.0, 2: 400.0}, kernel_commit="a" * 40
    )
    table = calibrate([before, tagged], output=tmp_path / "out.json")
    assert table.dimensions["kernel_version"] == UNTAGGED


@pytest.mark.parametrize(
    ("change", "name", "values"),
    [
        ({"toolchain": "armclang"}, "toolchain", ("'armclang'", "'arm-none-eabi-gcc'")),
        ({"engine_version": "0.34.0"}, "engine_version", ("'0.34.0'", "'0.33.0'")),
        ({"arena_region": "SRAM"}, "arena_memory", ("'SRAM'", "'DTCM'")),
    ],
)
def test_a_dimension_that_changes_cycles_is_refused_though_compare_only_reports_it(
    tmp_path: Path, change: dict[str, Any], name: str, values: tuple[str, str]
) -> None:
    auto, _ = two_bundles(tmp_path)
    other = bundle(tmp_path / "other", cycles={0: 12000.0, 1: 3000.0, 2: 400.0}, **change)
    with pytest.raises(CalibrationError, match=f"has {name} {values[0]};.*has {values[1]}"):
        calibrate([auto, other], output=tmp_path / "out.json")


def test_a_power_only_difference_still_merges(tmp_path: Path) -> None:
    """A power scope is a blocking dimension for power metrics, which no price is."""
    auto, _ = two_bundles(tmp_path)
    gated = bundle(
        tmp_path / "gated",
        cycles={0: 12000.0, 1: 3000.0, 2: 400.0},
        power={"measurement_scope": "gpio_gated_clean_window", "integrity": "valid"},
    )
    table = calibrate([auto, gated], output=tmp_path / "out.json")
    assert table.price_count == 3
    assert table.dimensions["power_scope"] is None
    whole = bundle(
        tmp_path / "whole",
        cycles={0: 12000.0, 1: 3000.0, 2: 400.0},
        power={"measurement_scope": "whole_capture_window", "integrity": "valid"},
    )
    with pytest.raises(CalibrationError, match="has power_scope 'whole_capture_window'"):
        calibrate([gated, whole], output=tmp_path / "out.json")


def test_a_file_that_records_no_setup_takes_the_bundles_setup(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    output = _hand_written(
        tmp_path / "hand.json", {"0:conv": {"accumulation": {"int16": {"cycles": 9000}}}}
    )

    table = calibrate([auto], output=output, merge=True)

    assert table.operators["0:conv"]["accumulation"] == {"int16": 9000.0, "int32": 12000.0}
    assert table.dimensions == DIMENSIONS
    assert json.loads(output.read_text())["capture"]["dimensions"] == DIMENSIONS
    # The bundles must still agree among themselves, file or no file.
    other = bundle(tmp_path / "other", cycles={0: 1.0, 1: 1.0, 2: 1.0}, link_family="armlink")
    hand = _hand_written(
        tmp_path / "hand2.json", {"0:conv": {"accumulation": {"int16": {"cycles": 9000}}}}
    )
    with pytest.raises(CalibrationError, match="has link_family 'armlink';.*auto has"):
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
    with pytest.raises(
        CalibrationError, match=r"0:fc weights=packed costs 5000 cycles in .*uneven.json but 3000"
    ):
        calibrate([auto], output=uneven, merge=True)
    assert json.loads(uneven.read_text())["operators"]["0:fc"]["weights"]["packed"] == {
        "cycles": 5000
    }
    agreeing = bundle(tmp_path / "agreeing", cycles={0: 12000.0, 1: 3050.0, 2: 1.0})
    even = _hand_written(
        tmp_path / "even.json",
        {
            "0:fc": {
                "accumulation": {"int32": {"cycles": 3000}},
                "weights": {"packed": {"cycles": 3000}},
            }
        },
    )
    table = calibrate([agreeing], output=even, merge=True)
    assert table.prices[("0:fc", "accumulation", "int32")] == Price(3000.0, {"weights": "packed"})
    assert table.prices[("0:fc", "weights", "packed")] == Price(3000.0, {"accumulation": "int32"})


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


def test_validity_follows_the_issue_registry_as_compare_applies_it(tmp_path: Path) -> None:
    # The one error the registry confines to power metrics leaves the cycles usable.
    confined = bundle(tmp_path / "confined", cycles={0: 12000.0, 1: 3000.0, 2: 400.0})
    _mark(confined, ResultValidity.INVALID, "power.window_observer_mismatch")
    assert read_bundle(confined).samples
    # A run-wide power error, an unrecognized code, and an invalid run with no
    # error to explain it are refused, as hpx compare refuses them.
    for name, codes, named in (
        ("dark", ("power.observation_missing",), "power.observation_missing"),
        ("newer", ("power.from_a_newer_hpx",), "power.from_a_newer_hpx"),
        ("silent", (), "no issue explains it"),
    ):
        path = bundle(tmp_path / name, cycles={0: 12000.0, 1: 3000.0, 2: 400.0})
        _mark(path, ResultValidity.INVALID, *codes)
        with pytest.raises(CalibrationError, match=f"Bundle .*{name} is invalid: {named}"):
            read_bundle(path)


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


@pytest.mark.parametrize("fixed", [[], 0, "", "x"])
def test_a_plan_whose_fixed_is_not_an_object_is_refused(tmp_path: Path, fixed: Any) -> None:
    ops = json.loads(json.dumps(PLAN_OPERATORS))
    ops[0]["fixed"] = fixed
    odd = bundle(tmp_path / "odd", cycles={0: 1.0, 1: 1.0, 2: 1.0}, plan_ops=ops)
    with pytest.raises(CalibrationError, match="operator 0:conv fixed is not an object"):
        read_bundle(odd)


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


def test_two_prices_of_one_key_must_agree_within_the_tolerance(tmp_path: Path) -> None:
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
                prices={("0:conv", "accumulation", "int32"): Price(1.0, {})},
                dimensions={},
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
    assert not any(tmp_path.glob(f".{tmp_path.name}.*"))

    help_text = runner.invoke(app, ["calibrate", "--help"]).output
    assert "without knobs" in help_text and "left out" in help_text
    assert "--merge" in help_text and "--tolerance" in help_text
    assert "application_load=none" in help_text
    assert "kws-sram" not in help_text


def test_the_file_follows_the_umask_and_never_replaces_a_link(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    previous = os.umask(0o027)
    try:
        output = tmp_path / "kws.json"
        calibrate([auto], output=output)
        assert stat.S_IMODE(output.stat().st_mode) == 0o640
    finally:
        os.umask(previous)
    private = tmp_path / "private.json"
    private.write_text("{}", encoding="utf-8")
    private.chmod(0o600)
    calibrate([auto], output=private)
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    assert not any(tmp_path.glob(".private.json.*"))
    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(CalibrationError, match="link.json is a symbolic link"):
        calibrate([auto], output=link)
    assert link.is_symlink() and real.read_text(encoding="utf-8") == "{}"


def test_a_failed_write_removes_only_its_own_temporary_file(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    output = tmp_path / "cal.json"
    foreign = tmp_path / f".cal.json.{os.getpid()}.tmp"
    foreign.write_text("another writer's data", encoding="utf-8")
    calibrate([auto], output=output)
    assert foreign.read_text(encoding="utf-8") == "another writer's data"
    assert sorted(p.name for p in tmp_path.glob(".cal.json.*")) == [foreign.name]
