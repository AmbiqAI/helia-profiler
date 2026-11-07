"""Result bundles written as ``hpx profile --engine helia-aot`` writes them, for the calibrate tests.

Needs no heliaAOT: the tests that read these bundles skip without the aot
extra, the ones that only need a bundle on disk run everywhere.

``python -m tests.calibration_bundles <out>`` writes the calibration file of
:func:`golden_bundles` to *out*, which needs the aot extra; the committed
``tests/fixtures/calibration/hpx_calibration.v2.json`` is written this way.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import json
import sys
import tempfile
from collections.abc import Iterator
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest

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
#: The calibration file hpx's writer produces for :func:`golden_bundles`.
GOLDEN = PLANS / "hpx_calibration.v2.json"


def _aot_args(
    knob_values: dict[str, str] | None, memory: dict[str, Any] | None = None
) -> dict[str, Any]:
    """heliaAOT arguments in the shape its ConvertArgs accepts, knob values included.

    Validated by ConvertArgs wherever the aot extra is installed; a test
    environment without it still gets the same shape.
    """
    args: dict[str, Any] = {
        "optimization": {"goal": "balanced", **(knob_values or {})},
        "operators": [{"type": "conv_2d", "optimization": dict(knob_values or {})}],
    }
    if memory is not None:
        args["memory"] = dict(memory)
    try:
        from helia_aot.cli.defines import ConvertArgs
    except ImportError:
        return args
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
    aot_memory: dict[str, Any] | None = None,
    jlink_serial: str = "1160002204",
    flags: str = "-mcpu=cortex-m55",
    kernel_commit: str | None = "c" * 40,
    kernel_content: str = "k" * 64,
    power: dict[str, Any] | None = None,
    power_config: dict[str, Any] | None = None,
    with_toolchain: bool = True,
    effective_config: str = "e" * 64,
    effective_config_pairing: str | None = "9" * 64,
    weights_location: str | None = "tcm",
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
                "weights_location": weights_location,
            },
            "engine": {
                "type": "helia-aot",
                "backend": backend,
                "config_path": None,
                "config": {"aot_args": _aot_args(knob_values, aot_memory)},
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
    if effective_config_pairing is not None:
        metadata["engine"]["effective_config_pairing_sha256"] = effective_config_pairing
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


def _hand_written(path: Path, operator_cycles: dict[str, Any], sha256: str = SHA) -> Path:
    """A schema 2 table as a person writes it for heliaOptimus: no ``capture`` block."""
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "model": {"sha256": sha256, "subgraph": 0},
                "conditions": CONDITIONS,
                "operator_cycles": operator_cycles,
            }
        ),
        encoding="utf-8",
    )
    return path


def two_bundles(tmp_path: Path) -> tuple[Path, Path]:
    """One bundle per value of the convolution's accumulation; the fc knobs hold still."""
    auto = bundle(tmp_path / "auto", cycles={0: 12000.0, 1: 3000.0, 2: 400.0})
    wide = bundle(
        tmp_path / "wide",
        cycles={0: 9000.0, 1: 3030.0, 2: 405.0},
        plan_ops=_knob(PLAN_OPERATORS, "0:conv", "accumulation", "fast"),
        knob_values={"accumulation": "fast"},
    )
    return auto, wide


#: The heliaAOT memory arguments of every capture of a pair: hpx accepts
#: PSRAM weights only in external-arena mode, and the setting is held.
PAIR_AOT_MEMORY: dict[str, Any] = {"allocate_arenas": False}


def pair(tmp_path: Path, slow_memory: str) -> tuple[Path, Path]:
    """A capture with the weights in *slow_memory* (``mram`` or ``psram``) and one in TCM.

    Everything else is held, as one operator session records it: both run
    with :data:`PAIR_AOT_MEMORY`, the compiler's configuration differs only
    in the constants' placement rule hpx derives from ``weights_location``,
    so its pairing digest is shared, and the PSRAM clock is recorded only by
    the run that used PSRAM.
    """
    slow_conditions: dict[str, Any] = {**CONDITIONS, "weights_memory": slow_memory}
    if slow_memory == "psram":
        slow_conditions["external_memory_clock"] = 48
    slow = bundle(
        tmp_path / f"{slow_memory}-pair" / slow_memory,
        cycles={0: 15000.0, 1: 3600.0, 2: 400.0},
        conditions=slow_conditions,
        weights_location=slow_memory,
        aot_memory=PAIR_AOT_MEMORY,
        effective_config=hashlib.sha256(f"constants in {slow_memory}".encode()).hexdigest(),
    )
    fast = bundle(
        tmp_path / f"{slow_memory}-pair" / "tcm",
        cycles={0: 12000.0, 1: 3000.0, 2: 400.0},
        weights_location="tcm",
        aot_memory=PAIR_AOT_MEMORY,
        effective_config=hashlib.sha256(b"constants staged in dtcm").hexdigest(),
    )
    return slow, fast


def _golden_plan(changes: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """:data:`PLAN_OPERATORS` with *changes* applied to the knobs, each operator's
    kernel entry named after its vector, and the fc's fixed weight layout."""
    ops = json.loads(json.dumps(PLAN_OPERATORS))
    for op in ops:
        for knob, value in changes.get(op["id"], {}).items():
            op["knobs"][knob] = {"value": value, "approximate": value == "fast"}
        vector = sorted((knob, choice["value"]) for knob, choice in op["knobs"].items())
        op["kernel"] = {"entry": "_".join([op["op_type"], *(f"{k}_{v}" for k, v in vector)])}
        if op["id"] == "0:fc":
            op["fixed"] = {"weight_layout": {"value": "packed", "set_by": "heuristic"}}
    return ops


def golden_bundles(path: Path) -> tuple[Path, Path, Path, Path]:
    """Four bundles of :data:`PLAN_OPERATORS`: a base and one knob moved in each other.

    ``0:fc``'s two knobs are swept one at a time from the base's
    ``(precise, specialized)``; together they write 3 operators and 6 samples.
    """
    base = {0: 12000.0, 1: 3000.0, 2: 400.0}
    sweeps = (
        ("base", {}, {}, {}),
        ("conv_fast", {"0:conv": {"accumulation": "fast"}}, {0: 9000.0}, {"accumulation": "fast"}),
        ("fc_generic", {"0:fc": {"kernel": "generic"}}, {1: 3600.0}, {"kernel": "generic"}),
        ("fc_fast", {"0:fc": {"accumulation": "fast"}}, {1: 2400.0}, {"accumulation": "fast"}),
    )
    first, second, third, fourth = (
        bundle(
            path / name,
            cycles={**base, **cycles},
            plan_ops=_golden_plan(changes),
            knob_values=knob_values or None,
        )
        for name, changes, cycles, knob_values in sweeps
    )
    return first, second, third, fourth


def _calibration_schema() -> dict[str, Any]:
    text = files("helia_profiler").joinpath("data/calibration.schema.v2.json").read_text("utf-8")
    return json.loads(text)


@pytest.fixture
def schema_checked_writes(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Validate every file ``calibrate`` writes against the exported schema."""
    from jsonschema import Draft202012Validator

    from helia_profiler.calibration import table

    validator = Draft202012Validator(_calibration_schema())
    original = table.write_calibration

    def checked(written: Any, path: Path) -> Path:
        result = original(written, path)
        validator.validate(json.loads(path.read_text(encoding="utf-8")))
        return result

    monkeypatch.setattr(table, "write_calibration", checked)
    yield


def write_golden(output: Path) -> Path:
    """Calibrate :func:`golden_bundles` into *output* with hpx's own writer."""
    from helia_profiler.calibration import calibrate

    with tempfile.TemporaryDirectory() as directory:
        calibrate(golden_bundles(Path(directory)), output=output)
    return output


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m tests.calibration_bundles <out>")
    write_golden(Path(sys.argv[1]))
