"""``hpx calibrate`` (#648): synthetic and real plans, every rule, and heliaOptimus's shape."""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from typer.testing import CliRunner

from helia_profiler.calibration import (
    CALIBRATION_SCHEMA_VERSION,
    SETUP_EXCLUDED,
    CalibrationTable,
    Price,
    Setup,
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
        "knobs": {"accumulation": {"value": "precise"}},
    },
    {
        "id": "0:fc",
        "index": 1,
        "op_type": "fully_connected",
        "knobs": {"accumulation": {"value": "precise"}, "kernel": {"value": "specialized"}},
    },
    {"id": "0:softmax", "index": 2, "op_type": "softmax", "knobs": {}},
]

PLANS = Path(__file__).parent / "fixtures" / "calibration"


def _aot_args(knob_values: dict[str, str] | None) -> dict[str, Any]:
    """heliaAOT arguments in the shape its ConvertArgs accepts, knob values included."""
    from helia_aot.cli.defines import ConvertArgs

    args: dict[str, Any] = {
        "optimization": {"goal": "balanced", **(knob_values or {})},
        "operators": [{"type": "conv_2d", "optimization": dict(knob_values or {})}],
    }
    ConvertArgs(**args)
    return args


def _knob(ops: list[dict[str, Any]], operator: str, knob: str, value: str) -> list[dict[str, Any]]:
    out = json.loads(json.dumps(ops))
    for op in out:
        if op["id"] == operator:
            op["knobs"][knob] = {"value": value, "approximate": value == "fast"}
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
    compiler_version: str = "13.2.1",
    engine_version: str = "0.33.0",
    backend: str | None = None,
    link_family: str = "gnu",
    arena_location: str | None = None,
    knob_values: dict[str, str] | None = None,
    jlink_serial: str = "1160002204",
    flags: str = "-mcpu=cortex-m55",
    kernel_commit: str | None = "c" * 40,
    kernel_content: str = "k" * 64,
    power: dict[str, Any] | None = None,
    power_config: dict[str, Any] | None = None,
    with_toolchain: bool = True,
    effective_config: str = "e" * 64,
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
        "identity": {"model_sha256": sha256},
        "top_layers": [],
        "validity": "valid",
        "issues": [],
    }
    if conditions is not None:
        summary["conditions"] = conditions
    if power is not None:
        summary["power"] = power
    (path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    metadata: dict[str, Any] = {
        "schema": "hpx.run-metadata",
        "schema_version": 1,
        "model": {"sha256": sha256},
        "config": {
            "model": {
                "path": str(path / "kws.tflite"),
                "arena_size": 262144,
                "arena_location": arena_location,
                "weights_location": "tcm",
            },
            "engine": {
                "type": "helia-aot",
                "backend": backend,
                "config_path": None,
                "config": {"aot_args": _aot_args(knob_values)},
            },
            "target": {
                "board": "apollo510_evb",
                "toolchain": toolchain,
                "jlink_serial": jlink_serial,
                "transport": "rtt",
                "clock": {"cpu": "hp"},
            },
            "profiling": {"iterations": 100, "pmu_counters": ["cpu:default"]},
            "power": power_config or {"enabled": False, "mode": "external"},
            "output": {"dir": str(path), "format": "csv"},
            "timeouts": {"build_s": 600},
            "build": {"nsx_modules": {}, "offline": False},
            "work_dir": None,
        },
        "engine": {
            "type": "helia-aot",
            "version": engine_version,
            "backend": backend,
            "effective_config_sha256": effective_config,
            "effective_config_keys": ["memory", "module", "optimization", "platform"],
        },
        "platform": {"link_family": link_family},
        "build_images": [{"role": "profile", "architecture_flags": {flags: 12}}],
        "dependencies": {
            "modules": [
                {
                    "name": "nsx-cmsis-nn",
                    "peeled_commit": kernel_commit,
                    "content_hash": {"algorithm": "sha256", "value": kernel_content},
                }
            ]
        },
    }
    if with_toolchain:
        metadata["toolchain"] = {"compiler": toolchain, "compiler_version": compiler_version}
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
    """A bundle around one of heliaAOT 0.33's own plans for the kws models.

    The effective configuration is the real digest of a real config that
    sets the plan's knob value, so a sweep's bundles carry real, equal digests.
    """
    plan = json.loads((PLANS / f"{plan_name}_plan.json").read_text(encoding="utf-8"))
    ops = plan["operators"]
    (knob, value), *_ = {
        name: choice["value"] for op in ops for name, choice in op["knobs"].items()
    }.items()
    config = _aot_config(path.parent, plan_name, f"optimization: {{{knob}: {value}}}\n")
    return bundle(
        path,
        cycles={op["index"]: 1000.0 + op["index"] + offset for op in ops},
        ops=tuple(op["op_type"] for op in ops),
        plan_ops=ops,
        sha256=plan["model"]["sha256"],
        effective_config=_digest(config)[0],
        knob_values={knob: value},
        **changes,
    )


def two_bundles(tmp_path: Path) -> tuple[Path, Path]:
    """One bundle per value of the convolution's accumulation; the fc knobs hold still."""
    auto = bundle(tmp_path / "auto", cycles={0: 12000.0, 1: 3000.0, 2: 400.0})
    wide = bundle(
        tmp_path / "wide",
        cycles={0: 9000.0, 1: 3030.0, 2: 410.0},
        plan_ops=_knob(PLAN_OPERATORS, "0:conv", "accumulation", "fast"),
        knob_values={"accumulation": "fast"},
    )
    return auto, wide


def _price(cycles: float, **others: str) -> dict[str, Any]:
    return {"others": others, "facts": {}}


EXPECTED_OPERATORS = {
    "0:conv": {"accumulation": {"fast": {"cycles": 9000.0}, "precise": {"cycles": 12000.0}}},
    "0:fc": {
        "accumulation": {"precise": {"cycles": 3000.0}},
        "kernel": {"specialized": {"cycles": 3000.0}},
    },
}
EXPECTED_PRICES = {
    "0:conv": {"accumulation": {"precise": _price(12000.0), "fast": _price(9000.0)}},
    "0:fc": {
        "accumulation": {"precise": _price(3000.0, kernel="specialized")},
        "kernel": {"specialized": _price(3000.0, accumulation="precise")},
    },
}


def _check_expected(written: dict[str, Any]) -> None:
    assert written["schema_version"] == 1
    assert written["model"] == {"sha256": SHA, "subgraph": 0}
    assert written["conditions"] == CONDITIONS
    assert written["operators"] == EXPECTED_OPERATORS
    assert written["capture"]["prices"] == EXPECTED_PRICES
    assert set(written["capture"]) == {"setup", "prices"}


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
    _check_expected(written)
    assert "0:softmax" not in written["operators"]
    assert table.price_count == 4
    assert read_calibration(output) == table
    file = _CalibrationFile.model_validate(written)
    assert file.schema_version == CALIBRATION_SCHEMA_VERSION
    assert file.conditions == _Conditions.model_validate(CONDITIONS)
    assert file.operators["0:conv"]["accumulation"]["fast"].cycles == 9000.0
    assert not any(output.parent.glob(".kws.json.*"))


def test_the_setup_covers_the_held_settings_and_nothing_swept_or_run_only(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    output = tmp_path / "kws.json"
    calibrate([auto], output=output)

    setup = json.loads(output.read_text(encoding="utf-8"))["capture"]["setup"]

    assert re.fullmatch(r"[0-9a-f]{64}", setup["digest"])
    fields = set(setup["fields"])
    held = {
        "config.target.toolchain",
        "config.target.board",
        "config.target.clock.cpu",
        "config.model.arena_location",
        "config.model.arena_size",
        "config.model.weights_location",
        "config.engine.backend",
        "config.engine.config.aot_args.optimization.goal",
        "config.profiling.iterations",
        "config.build.nsx_modules",
        "toolchain.compiler_version",
        "engine.version",
        "engine.effective_config_sha256",
        "engine.effective_config_keys.0",
        "build.architecture_flags",
        "kernel.build",
    }
    assert held <= fields, held - fields
    swept_or_run_only = {
        "config.engine.config.aot_args.optimization.accumulation",
        "config.engine.config.aot_args.optimization.kernel",
        "config.engine.config.aot_args.operators.0.optimization.accumulation",
        "config.engine.config.aot_args.operators.0.type",
        "config.engine.config.aot_args.operators",
        "config.power.enabled",
        "toolchain.cmake_version",
        "config.output.dir",
        "config.timeouts.build_s",
        "config.target.jlink_serial",
        "config.model.path",
        "config.work_dir",
        "config.build.offline",
    }
    assert not swept_or_run_only & fields
    assert not any(name.startswith("config.engine.config.aot_args.operators") for name in fields)
    assert "config.power" in SETUP_EXCLUDED
    source = read_bundle(auto)
    assert source.setup.digest == setup["digest"]
    assert source.setup.values is not None
    assert source.setup.values["kernel"]["build"] == "commit:" + "c" * 40
    assert source.setup.values["build"]["architecture_flags"] == "-mcpu=cortex-m55"


def test_an_approximate_value_is_still_priced(tmp_path: Path) -> None:
    _, wide = two_bundles(tmp_path)
    (sample,) = [s for s in read_bundle(wide).samples if s.operator == "0:conv"]
    assert sample.knobs == {"accumulation": "fast"} and sample.cycles == 9000.0


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
    assert re.fullmatch(r"[0-9a-f]{64}", table.setup.digest)
    assert "engine.effective_config_sha256" in table.setup.fields
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


def test_merge_then_merge_again(tmp_path: Path) -> None:
    """The documented workflow: write, --merge, --merge the same bundle."""
    auto, wide = two_bundles(tmp_path)
    output = tmp_path / "cal.json"
    calibrate([auto], output=output)

    once = calibrate([wide], output=output, merge=True)
    twice = calibrate([wide], output=output, merge=True)

    assert once == twice
    _check_expected(json.loads(output.read_text(encoding="utf-8")))


def test_a_second_vector_for_one_value_is_refused_naming_both(tmp_path: Path) -> None:
    auto = bundle(tmp_path / "auto", cycles={0: 12000.0, 1: 3000.0, 2: 400.0})
    flat = bundle(
        tmp_path / "flat",
        cycles={0: 12000.0, 1: 2500.0, 2: 400.0},
        plan_ops=_knob(PLAN_OPERATORS, "0:fc", "kernel", "generic"),
    )
    with pytest.raises(
        CalibrationError,
        match=r"0:fc accumulation=precise is priced with the other knobs at \{'kernel': 'specialized'\} "
        r"in .*auto but at \{'kernel': 'generic'\} in .*flat",
    ):
        calibrate([auto, flat], output=tmp_path / "out.json")
    output = tmp_path / "cal.json"
    calibrate([auto], output=output)
    with pytest.raises(CalibrationError, match=r"in .*cal.json but at \{'kernel': 'generic'\}"):
        calibrate([flat], output=output, merge=True)


def test_facts_of_one_vector_are_recorded_once(tmp_path: Path) -> None:
    def with_entry(entry: str) -> list[dict[str, Any]]:
        ops = json.loads(json.dumps(PLAN_OPERATORS))
        ops[0]["kernel"] = {"entry": entry}
        ops[0]["fixed"] = {"weight_layout": {"value": "packed", "set_by": "heuristic"}}
        return ops

    one = bundle(tmp_path / "one", cycles={0: 12000.0, 1: 1.0, 2: 1.0}, plan_ops=with_entry("a"))
    same = bundle(tmp_path / "same", cycles={0: 12100.0, 1: 1.0, 2: 1.0}, plan_ops=with_entry("a"))
    table = calibrate([one, same], output=tmp_path / "out.json")
    assert table.prices[("0:conv", "accumulation", "precise")].facts == {
        "fixed:weight_layout": "packed",
        "kernel:entry": "a",
    }
    other = bundle(
        tmp_path / "other", cycles={0: 12000.0, 1: 1.0, 2: 1.0}, plan_ops=with_entry("b")
    )
    with pytest.raises(
        CalibrationError,
        match=r"0:conv accumulation=precise with the other knobs at \{\} reports .*'kernel:entry': 'b'.* "
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


@pytest.mark.parametrize(
    ("change", "field", "values"),
    [
        (
            {"toolchain": "armclang"},
            "config.target.toolchain",
            ("'armclang'", "'arm-none-eabi-gcc'"),
        ),
        ({"compiler_version": "14.1.0"}, "toolchain.compiler_version", ("'14.1.0'", "'13.2.1'")),
        ({"backend": "ethos_u"}, "config.engine.backend", ("'ethos_u'", "None")),
        ({"arena_location": "sram"}, "config.model.arena_location", ("'sram'", "None")),
        (
            {"flags": "-mcpu=cortex-m55+nomve"},
            "build.architecture_flags",
            ("'-mcpu=cortex-m55\\+nomve'", "'-mcpu=cortex-m55'"),
        ),
        ({"kernel_commit": "d" * 40}, "kernel.build", ("'commit:dddd", "'commit:cccc")),
        (
            {"effective_config": "f" * 64},
            "engine.effective_config_sha256",
            ("'ffff", "'eeee"),
        ),
    ],
)
def test_a_held_setting_that_differs_is_refused_naming_it(
    tmp_path: Path, change: dict[str, Any], field: str, values: tuple[str, str]
) -> None:
    auto, _ = two_bundles(tmp_path)
    other = bundle(tmp_path / "other", cycles={0: 12000.0, 1: 3000.0, 2: 400.0}, **change)
    message = (
        f"different setups: .*{re.escape(field)} is .*{values[0]}.* in .*other"
        f".*{values[1]}.* in .*auto|different setups: .*{re.escape(field)} is "
        f".*{values[1]}.* in .*auto.*{values[0]}.* in .*other"
    )
    with pytest.raises(CalibrationError, match=message):
        calibrate([auto, other], output=tmp_path / "out.json")
    with pytest.raises(CalibrationError, match=message):
        calibrate([other, auto], output=tmp_path / "out.json")
    # The file remembers its setup, so a later merge is held to it too.
    output = tmp_path / "kws.json"
    calibrate([auto], output=output)
    with pytest.raises(CalibrationError, match="different setups"):
        calibrate([other], output=output, merge=True)


def test_local_kernel_trees_are_told_apart_by_their_content(tmp_path: Path) -> None:
    """A local or vendored ns-cmsis-nn records no commit, only a content hash."""
    one = bundle(
        tmp_path / "one",
        cycles={0: 12000.0, 1: 3000.0, 2: 400.0},
        kernel_commit=None,
        kernel_content="1" * 64,
    )
    other = bundle(
        tmp_path / "other",
        cycles={0: 5000.0, 1: 3000.0, 2: 400.0},
        kernel_commit=None,
        kernel_content="2" * 64,
        plan_ops=_knob(PLAN_OPERATORS, "0:conv", "accumulation", "fast"),
    )
    with pytest.raises(CalibrationError, match="kernel.build is 'content:2222"):
        calibrate([one, other], output=tmp_path / "out.json")
    same = bundle(
        tmp_path / "same",
        cycles={0: 5000.0, 1: 3000.0, 2: 400.0},
        kernel_commit=None,
        kernel_content="1" * 64,
        plan_ops=_knob(PLAN_OPERATORS, "0:conv", "accumulation", "fast"),
    )
    assert calibrate([one, same], output=tmp_path / "out.json").price_count == 4


def test_a_setting_absent_on_one_side_is_a_mismatch(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    bare = bundle(tmp_path / "bare", cycles={0: 12000.0, 1: 3000.0, 2: 400.0}, with_toolchain=False)
    with pytest.raises(
        CalibrationError,
        match="toolchain.compiler_version is absent in .*bare but '13.2.1' in .*auto",
    ):
        calibrate([auto, bare], output=tmp_path / "out.json")
    with pytest.raises(CalibrationError, match="different setups"):
        calibrate([bare, auto], output=tmp_path / "out.json")


def test_swept_and_run_only_settings_do_not_block_a_merge(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    other = bundle(
        tmp_path / "other",
        cycles={0: 9000.0, 1: 3000.0, 2: 400.0},
        plan_ops=_knob(PLAN_OPERATORS, "0:conv", "accumulation", "fast"),
        knob_values={"accumulation": "fast", "kernel": "generic"},
        jlink_serial="1160009999",
        power_config={"enabled": True, "mode": "external", "serial": "004204"},
    )
    assert calibrate([auto, other], output=tmp_path / "out.json").price_count == 4


def test_a_helia_aot_bundle_without_the_effective_configuration_is_refused(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    metadata = json.loads((auto / "run_metadata.json").read_text(encoding="utf-8"))
    del metadata["engine"]["effective_config_sha256"]
    (auto / "run_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    _republish(auto)
    with pytest.raises(CalibrationError, match="records no engine.effective_config_sha256"):
        read_bundle(auto)


def _with_engine_config(path: Path, aot_args: dict[str, Any]) -> Path:
    metadata = json.loads((path / "run_metadata.json").read_text(encoding="utf-8"))
    metadata["config"]["engine"]["config"]["aot_args"] = aot_args
    (path / "run_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    _republish(path)
    return path


def test_the_digest_is_of_the_nested_settings_not_a_flattened_view(tmp_path: Path) -> None:
    dotted = _with_engine_config(
        bundle(tmp_path / "dotted", cycles={0: 1.0, 1: 1.0, 2: 1.0}),
        {"memory": {"planner_options": {"a.b": 1}}},
    )
    nested = _with_engine_config(
        bundle(tmp_path / "nested", cycles={0: 1.0, 1: 1.0, 2: 1.0}),
        {"memory": {"planner_options": {"a": {"b": 1}}}},
    )
    assert read_bundle(dotted).setup.digest != read_bundle(nested).setup.digest
    # No leaf differs, so the message names the section.
    with pytest.raises(CalibrationError, match="different setups: the config section differs"):
        calibrate([dotted, nested], output=tmp_path / "out.json")


def test_a_leaf_mismatch_names_the_leaf_and_absent_means_absent(tmp_path: Path) -> None:
    flag = _with_engine_config(
        bundle(tmp_path / "flag", cycles={0: 1.0, 1: 1.0, 2: 1.0}),
        {"memory": {"planner_options": {"fast": True}}},
    )
    number = _with_engine_config(
        bundle(tmp_path / "number", cycles={0: 1.0, 1: 1.0, 2: 1.0}),
        {"memory": {"planner_options": {"fast": 1}}},
    )
    with pytest.raises(
        CalibrationError,
        match="config.engine.config.aot_args.memory.planner_options.fast is 1 in .*number but True",
    ):
        calibrate([flag, number], output=tmp_path / "out.json")
    null = _with_engine_config(
        bundle(tmp_path / "null", cycles={0: 1.0, 1: 1.0, 2: 1.0}),
        {"memory": {"planner_options": {"fast": None}}},
    )
    absent = _with_engine_config(
        bundle(tmp_path / "absent", cycles={0: 1.0, 1: 1.0, 2: 1.0}),
        {"memory": {"planner_options": {}}},
    )
    with pytest.raises(
        CalibrationError,
        match="different setups: (.*; )?[a-z_.]*planner_options.fast is absent in .*absent but None in .*null",
    ) as caught:
        calibrate([null, absent], output=tmp_path / "out.json")
    assert "planner_options is" not in str(caught.value)


def test_power_measurement_state_never_takes_part(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    gated = bundle(
        tmp_path / "gated",
        cycles={0: 9000.0, 1: 3000.0, 2: 400.0},
        plan_ops=_knob(PLAN_OPERATORS, "0:conv", "accumulation", "fast"),
        knob_values={"accumulation": "fast"},
        power={
            "measurement_scope": "gpio_gated_clean_window",
            "integrity": "valid",
            "firmware_code_fingerprint": "1" * 64,
        },
        power_config={"enabled": True, "mode": "external"},
    )
    assert calibrate([auto, gated], output=tmp_path / "out.json").price_count == 4
    # Two power-measured runs whose rendered firmware differs with the knob.
    other_print = bundle(
        tmp_path / "print",
        cycles={0: 12000.0, 1: 3000.0, 2: 400.0},
        power={
            "measurement_scope": "whole_capture_window",
            "integrity": "degraded",
            "firmware_code_fingerprint": "2" * 64,
        },
        power_config={"enabled": True, "mode": "external"},
    )
    assert calibrate([gated, other_print], output=tmp_path / "out.json").price_count == 4


def test_the_setup_check_does_not_depend_on_bundle_order(tmp_path: Path) -> None:
    auto, wide = two_bundles(tmp_path)
    odd = bundle(
        tmp_path / "odd",
        cycles={0: 1.0, 1: 1.0, 2: 1.0},
        toolchain="armclang",
        plan_ops=_knob(PLAN_OPERATORS, "0:fc", "kernel", "generic"),
    )
    for order in ([auto, wide, odd], [odd, auto, wide], [auto, odd, wide]):
        with pytest.raises(CalibrationError, match="different setups: config.target.toolchain"):
            calibrate(order, output=tmp_path / "out.json")


def test_a_file_hpx_did_not_write_cannot_be_merged_into(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    hand = _hand_written(
        tmp_path / "hand.json", {"0:conv": {"accumulation": {"fast": {"cycles": 9000}}}}
    )
    with pytest.raises(CalibrationError, match="records no setup under capture.setup") as refused:
        calibrate([auto], output=hand, merge=True)
    assert "heliaOptimus reads a hand-written table directly" in str(refused.value)
    assert json.loads(hand.read_text())["operators"] == {
        "0:conv": {"accumulation": {"fast": {"cycles": 9000}}}
    }


def test_a_damaged_file_cannot_be_merged_into(tmp_path: Path) -> None:
    auto, wide = two_bundles(tmp_path)
    output = tmp_path / "cal.json"
    calibrate([auto], output=output)
    whole = json.loads(output.read_text(encoding="utf-8"))

    damaged = tmp_path / "no_setup.json"
    data = json.loads(json.dumps(whole))
    del data["capture"]["setup"]
    damaged.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CalibrationError, match="records no setup under capture.setup"):
        calibrate([wide], output=damaged, merge=True)

    edited = tmp_path / "edited.json"
    data = json.loads(json.dumps(whole))
    data["capture"]["setup"]["digest"] = "not a digest"
    edited.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CalibrationError, match="records no setup under capture.setup"):
        calibrate([wide], output=edited, merge=True)

    malformed = tmp_path / "malformed.json"
    data = json.loads(json.dumps(whole))
    data["capture"]["prices"]["0:fc"]["accumulation"]["precise"]["others"] = ["weights"]
    malformed.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(
        CalibrationError, match="0:fc accumulation=precise has no well-formed record"
    ):
        calibrate([wide], output=malformed, merge=True)

    unrecorded = tmp_path / "unrecorded.json"
    data = json.loads(json.dumps(whole))
    del data["capture"]["prices"]["0:conv"]
    unrecorded.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CalibrationError, match="0:conv accumulation=precise has no well-formed"):
        calibrate([wide], output=unrecorded, merge=True)


def test_two_files_are_held_to_the_tolerance(tmp_path: Path) -> None:
    low = bundle(tmp_path / "low", cycles={0: 1000.0, 1: 1.0, 2: 1.0})
    high = bundle(tmp_path / "high", cycles={0: 9000.0, 1: 1.0, 2: 1.0})
    first = calibrate([low], output=tmp_path / "f1.json").as_source("f1")
    second = calibrate([high], output=tmp_path / "f2.json").as_source("f2")
    with pytest.raises(CalibrationError, match="costs 1000 cycles in f1 but 9000 in f2"):
        merge_sources([first, second])


def test_merge_never_replaces_a_price_the_file_holds(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    output = tmp_path / "kws.json"
    calibrate([auto], output=output)
    disagreeing = bundle(tmp_path / "disagreeing", cycles={0: 12000.0, 1: 4000.0, 2: 400.0})
    with pytest.raises(CalibrationError, match=r"costs 3000 cycles in .*kws\.json but 4000"):
        calibrate([disagreeing], output=output, merge=True)
    assert json.loads(output.read_text())["operators"]["0:fc"]["kernel"]["specialized"] == {
        "cycles": 3000.0
    }
    agreeing = bundle(tmp_path / "agreeing", cycles={0: 12000.0, 1: 3050.0, 2: 400.0})
    table = calibrate([agreeing], output=output, merge=True)
    assert table.prices[("0:fc", "accumulation", "precise")] == Price(
        3000.0, {"kernel": "specialized"}
    )


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
        '"capture": {"setup": {"digest": "' + "0" * 64 + '", "fields": []}}, '
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
    assert json.loads(output.read_text())["operators"]["0:fc"]["accumulation"]["precise"] == {
        "cycles": 3000.0
    }
    with pytest.raises(
        CalibrationError,
        match=r"0:fc accumulation=precise costs 3000 cycles in .*auto but 3030 in .*wide "
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
    assert "fast" not in json.loads(output.read_text())["operators"]["0:conv"]["accumulation"]

    calibrate([wide], output=output, merge=True)
    _check_expected(json.loads(output.read_text()))

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
                prices={("0:conv", "accumulation", "precise"): Price(1.0, {})},
                setup=Setup.of({"config.target.board": "apollo510_evb"}),
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
            "records no setup",
        ),
        (
            '{"schema_version": 1, "model": {"sha256": "a", "subgraph": 0}, "conditions": {}, '
            '"capture": {"setup": {"digest": "' + "0" * 64 + '", "fields": []}}, '
            '"operators": {"x": {"k": {"v": {}}}}}',
            "no finite, non-negative cycles",
        ),
        (
            '{"schema_version": 1, "model": {"sha256": "a", "subgraph": 0}, "conditions": {}, '
            '"capture": {"setup": {"digest": "' + "0" * 64 + '", "fields": []}}, '
            '"operators": {"x": {"k": {"v": {"cycles": NaN}}}}}',
            "no finite, non-negative cycles",
        ),
        (
            '{"schema_version": 1, "model": {"sha256": "a", "subgraph": 0}, "conditions": {}, '
            '"capture": {"setup": {"digest": "' + "0" * 64 + '", "fields": []}}, '
            '"operators": {"x": {"k": {"v": {"cycles": -1}}}}}',
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
    _check_expected(json.loads(output.read_text(encoding="utf-8")))

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
    assert "capture.setup" in help_text
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


def _aot_config(tmp_path: Path, name: str, text: str):
    """A profiler config whose engine.config_path holds *text*, which ConvertArgs accepts."""
    import yaml
    from helia_aot.cli.defines import ConvertArgs

    from helia_profiler.config import load_config

    ConvertArgs(**yaml.safe_load(text))
    config_path = tmp_path / f"{name}.yml"
    config_path.write_text(text, encoding="utf-8")
    model = tmp_path / "kws.tflite"
    model.write_bytes(b"\x00")
    return load_config(
        None,
        {
            "model": {"path": str(model), "weights_location": "mram"},
            "engine": {"type": "helia-aot", "config_path": str(config_path)},
            "target": {"board": "apollo510_evb"},
            "work_dir": str(tmp_path / "work"),
        },
    )


def _digest(config) -> tuple[str, tuple[str, ...]]:
    from helia_profiler.engines.helia_aot.compile import (
        effective_aot_args,
        effective_config_digest,
    )

    return effective_config_digest(
        effective_aot_args(config),
        module_name="hpx_model",
        prefix="hpx",
        aot_platform="apollo510_evb",
    )


def test_the_compiler_configuration_a_file_supplies_is_part_of_the_setup(tmp_path: Path) -> None:
    """Two runs differing only in a placement rule from engine.config_path do not merge."""
    sram = _aot_config(
        tmp_path,
        "sram",
        "memory:\n  tensors:\n    - type: constant\n      id: '0:w'\n      attributes: {memory: sram}\n",
    )
    dtcm = _aot_config(
        tmp_path,
        "dtcm",
        "memory:\n  tensors:\n    - type: constant\n      id: '0:w'\n      attributes: {memory: dtcm}\n",
    )
    sram_digest, keys = _digest(sram)
    assert sram_digest != _digest(dtcm)[0]
    assert keys == (
        "documentation",
        "force",
        "memory",
        "model",
        "module",
        "optimization",
        "platform",
        "result_views",
        "test",
        "transforms",
    )
    # The knob values are the swept inputs: a config that differs only there digests the same.
    generic = _aot_config(tmp_path, "generic", "optimization: {kernel: generic}\n")
    specialized = _aot_config(tmp_path, "specialized", "optimization: {kernel: specialized}\n")
    assert _digest(generic) == _digest(specialized)
    assert _digest(generic)[0] != sram_digest
    # A rule that only overrides a knob leaves nothing behind either.
    ruled = _aot_config(
        tmp_path,
        "ruled",
        "operators:\n  - type: conv_2d\n    optimization: {kernel: specialized}\n",
    )
    assert _digest(ruled) == _digest(generic)
    # The goal and approximation switch are held, the run's own paths are not.
    fast = _aot_config(tmp_path, "fast", "optimization: {goal: latency}\n")
    assert _digest(fast) != _digest(generic)
    logged = _aot_config(tmp_path, "logged", "log_file: run.log\nverbose: 2\n")
    assert _digest(logged) == _digest(generic)
    # And the bundles those runs write refuse one another on that field.
    one = bundle(
        tmp_path / "one", cycles={0: 12000.0, 1: 3000.0, 2: 400.0}, effective_config=sram_digest
    )
    other = bundle(
        tmp_path / "other",
        cycles={0: 9000.0, 1: 3000.0, 2: 400.0},
        plan_ops=_knob(PLAN_OPERATORS, "0:conv", "accumulation", "fast"),
        effective_config=_digest(dtcm)[0],
    )
    with pytest.raises(CalibrationError, match="engine.effective_config_sha256 is"):
        calibrate([one, other], output=tmp_path / "out.json")
    same = bundle(
        tmp_path / "same",
        cycles={0: 9000.0, 1: 3000.0, 2: 400.0},
        plan_ops=_knob(PLAN_OPERATORS, "0:conv", "accumulation", "fast"),
        effective_config=sram_digest,
    )
    assert calibrate([one, same], output=tmp_path / "out.json").price_count == 4


def test_the_run_metadata_records_the_effective_configuration() -> None:
    from helia_profiler.report.metadata import _metadata_to_dict
    from helia_profiler.results import EngineInfo, RunMetadata

    recorded = _metadata_to_dict(
        RunMetadata(
            engine=EngineInfo(
                type="helia-aot",
                version="0.33.0",
                effective_config_sha256="e" * 64,
                effective_config_keys=("memory", "module"),
            )
        )
    )
    assert recorded["engine"] == {
        "type": "helia-aot",
        "version": "0.33.0",
        "effective_config_sha256": "e" * 64,
        "effective_config_keys": ("memory", "module"),
    }
    assert json.loads(json.dumps(recorded))["engine"]["effective_config_keys"] == [
        "memory",
        "module",
    ]


def test_captures_compiled_by_different_heliaaot_releases_do_not_merge(tmp_path: Path) -> None:
    auto, _ = two_bundles(tmp_path)
    newer = bundle(
        tmp_path / "newer",
        cycles={0: 9000.0, 1: 3000.0, 2: 400.0},
        plan_ops=_knob(PLAN_OPERATORS, "0:conv", "accumulation", "fast"),
        knob_values={"accumulation": "fast"},
        engine_version="0.34.0",
    )
    with pytest.raises(
        CalibrationError,
        match="newer was compiled with heliaAOT 0.34.0 and .*auto with heliaAOT 0.33.0",
    ):
        calibrate([auto, newer], output=tmp_path / "out.json")
    output = tmp_path / "kws.json"
    calibrate([auto], output=output)
    assert json.loads(output.read_text())["capture"]["setup"]["helia_aot_version"] == "0.33.0"
    with pytest.raises(CalibrationError, match="compiled with heliaAOT 0.34.0 and .*kws.json"):
        calibrate([newer], output=output, merge=True)


def test_a_non_finite_value_in_an_excluded_section_does_not_matter(tmp_path: Path) -> None:
    auto, wide = two_bundles(tmp_path)
    for path in (auto, wide):
        metadata = json.loads((path / "run_metadata.json").read_text(encoding="utf-8"))
        metadata["config"]["power"]["max_current_a"] = float("inf")
        metadata["config"]["power"]["io_voltage"] = float("nan")
        (path / "run_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        _republish(path)
    assert "Infinity" in (auto / "run_metadata.json").read_text(encoding="utf-8")
    assert calibrate([auto, wide], output=tmp_path / "out.json").price_count == 4
    result = CliRunner().invoke(
        app, ["calibrate", str(auto), str(wide), "--output", str(tmp_path / "cli.json")]
    )
    assert result.exit_code == 0, result.output


def test_a_non_finite_value_in_a_held_field_is_refused_naming_it(tmp_path: Path) -> None:
    held = _with_engine_config(
        bundle(tmp_path / "held", cycles={0: 1.0, 1: 1.0, 2: 1.0}),
        {"transforms": [{"name": "fuse_activation", "options": {"eps": float("inf")}}]},
    )
    with pytest.raises(
        CalibrationError, match="heliaAOT argument transforms.0.options.eps is not a finite number"
    ):
        read_bundle(held)
    metadata = json.loads((held / "run_metadata.json").read_text(encoding="utf-8"))
    metadata["config"]["engine"]["config"]["aot_args"] = {}
    metadata["config"]["profiling"]["window_target_ms"] = float("nan")
    (held / "run_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    _republish(held)
    with pytest.raises(
        CalibrationError,
        match="held setting config.profiling.window_target_ms is not a finite number",
    ):
        read_bundle(held)
    # The CLI reports it as an error, never as a traceback.
    result = CliRunner().invoke(app, ["calibrate", str(held), "--output", str(tmp_path / "o.json")])
    assert result.exit_code == 1 and "window_target_ms" in result.output


def test_heliaaot_rejecting_the_arguments_surfaces_its_own_error(tmp_path: Path) -> None:
    from helia_profiler.engines.helia_aot.compile import effective_config_digest
    from helia_profiler.errors import EngineError

    with pytest.raises(
        EngineError, match=r"(?s)Failed to build heliaAOT ConvertArgs: .*tolerance"
    ) as caught:
        effective_config_digest(
            {"test": {"tolerance": -1}}, module_name="m", prefix="p", aot_platform="apollo510_evb"
        )
    assert caught.value.hint == "Check engine.config_path and engine.config.aot_args."
    bad = _with_engine_config(
        bundle(tmp_path / "bad", cycles={0: 1.0, 1: 1.0, 2: 1.0}), {"optimization": {"knobs": {}}}
    )
    with pytest.raises(
        CalibrationError, match=r"(?s)Failed to build heliaAOT ConvertArgs: .*knobs"
    ):
        read_bundle(bad)


def test_the_dumped_form_makes_spelled_out_defaults_equal(tmp_path: Path) -> None:
    from helia_profiler.engines.helia_aot.compile import effective_config_digest

    def digest(args: dict[str, Any]) -> str:
        return effective_config_digest(args, module_name="m", prefix="p", aot_platform="x")[0]

    plain = digest({})
    assert digest({"optimization": {"goal": "balanced", "kernel": "generic"}}) == plain
    assert digest({"optimization": {}}) == plain
    assert digest({"operators": [{"type": "conv_2d", "optimization": None}]}) == plain
    assert digest(
        {
            "operators": [
                {"type": "conv_2d", "attributes": {}, "optimization": {"kernel": "generic"}}
            ]
        }
    ) == digest({"operators": [{"type": "conv_2d", "optimization": {"kernel": "specialized"}}]})
    assert digest({"optimization": {"goal": "latency"}}) != plain


def test_a_rule_setting_operator_attributes_is_part_of_the_setup(tmp_path: Path) -> None:
    ruled = _aot_config(
        tmp_path,
        "ruled",
        "operators:\n  - type: conv_2d\n    attributes: {memory: sram}\n"
        "    optimization: {kernel: generic}\n",
    )
    swept = _aot_config(
        tmp_path,
        "swept",
        "operators:\n  - type: conv_2d\n    attributes: {memory: sram}\n"
        "    optimization: {kernel: specialized}\n",
    )
    bare = _aot_config(tmp_path, "bare", "optimization: {kernel: generic}\n")
    assert _digest(ruled) == _digest(swept)
    assert _digest(ruled)[0] != _digest(bare)[0]
    one = bundle(
        tmp_path / "one",
        cycles={0: 12000.0, 1: 3000.0, 2: 400.0},
        effective_config=_digest(ruled)[0],
    )
    other = bundle(
        tmp_path / "other",
        cycles={0: 9000.0, 1: 3000.0, 2: 400.0},
        plan_ops=_knob(PLAN_OPERATORS, "0:conv", "accumulation", "fast"),
        knob_values={"accumulation": "fast"},
        effective_config=_digest(bare)[0],
    )
    with pytest.raises(CalibrationError, match="engine.effective_config_sha256 is"):
        calibrate([one, other], output=tmp_path / "out.json")


def test_a_host_without_heliaaot_gets_the_install_hint(tmp_path: Path, monkeypatch) -> None:
    import sys

    from helia_profiler.errors import EngineMissingError

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
