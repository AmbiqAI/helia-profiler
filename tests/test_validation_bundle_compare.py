"""Tests for portable validation-bundle loading and comparison."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from helia_profiler.errors import ReportError, ValidationBundleError
from helia_profiler.validation.bundle import load_validation_bundle
from helia_profiler.validation.compare import (
    CaseOutcome,
    compare_validation_bundles,
    write_validation_compare_artifacts,
)
from helia_profiler.validation.report import write_validation_reports
from helia_profiler.validation.runner import CaseResult


def _write_bundle(root: Path, cycles: int, *, attempt: int = 1, repeat_total: int = 1) -> None:
    case_id = "apollo510_evb-kws-rt-arm-none-eabi-gcc-rtt-auto"
    if repeat_total > 1:
        case_id += f"-run{attempt:02d}"
    case_dir = root / case_id
    case_dir.mkdir(parents=True)
    (case_dir / "summary.json").write_text(
        json.dumps(
            {"total_cycles": cycles, "layers": 1, "latency": {"device_profiled_infer_avg_us": 10}}
        )
    )
    (case_dir / "run_metadata.json").write_text(
        json.dumps(
            {
                "model": {"sha256": "abc"},
                "toolchain": {"compiler": "gcc", "compiler_version": "1"},
                "config": {
                    "model": {"model_location": "auto"},
                    "engine": {"type": "helia-rt"},
                    "target": {
                        "board": "apollo510_evb",
                        "toolchain": "arm-none-eabi-gcc",
                        "transport": "rtt",
                    },
                },
            }
        )
    )
    with (case_dir / "profile_results.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["id", "op", "cycles", "overflow"])
        writer.writeheader()
        writer.writerow({"id": 0, "op": "CONV_2D", "cycles": cycles, "overflow": False})
    (case_dir / "config.yml").write_text("model: {}\n")
    write_validation_reports(
        [
            CaseResult(
                case_id=case_id,
                status="pass",
                duration_s=1,
                engine="helia-rt",
                model_id="kws",
                board="apollo510_evb",
                power=False,
                toolchain="arm-none-eabi-gcc",
                transport="rtt",
                memory="auto",
                attempt=attempt,
                repeat_total=repeat_total,
                layers=1,
                total_cycles=cycles,
                output_dir=str(case_dir),
            )
        ],
        root,
        repo_root=root / "not-git",
    )


def test_compare_validation_bundles_writes_portable_artifacts(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    _write_bundle(baseline, 100)
    _write_bundle(candidate, 80)

    result = compare_validation_bundles(baseline, candidate)

    assert result.summary["compared"] == 1
    assert result.cases[0].outcome is CaseOutcome.COMPARED
    total = next(
        metric for metric in result.cases[0].compare_result.metrics if metric.name == "total_cycles"
    )
    assert total.delta == -20

    output = tmp_path / "output"
    paths = write_validation_compare_artifacts(result, output)
    assert {path.name for path in paths} >= {
        "validation_compare.json",
        "validation_compare.md",
        "compare_summary.json",
        "layer_diff.csv",
    }
    document = json.loads((output / "validation_compare.json").read_text())
    refs = document["cases"][0]["artifacts"]
    assert refs["compare_summary.json"].startswith("case_compares/")
    per_case = json.loads((output / refs["compare_summary.json"]).read_text())
    assert per_case["baseline_dir"].startswith("baseline/")
    assert not Path(per_case["baseline_dir"]).is_absolute()


def test_repeat_attempts_match_exactly(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    _write_bundle(baseline, 100, attempt=1, repeat_total=2)
    _write_bundle(candidate, 80, attempt=2, repeat_total=2)

    result = compare_validation_bundles(baseline, candidate)

    assert [case.outcome for case in result.cases] == [
        CaseOutcome.BASELINE_ONLY,
        CaseOutcome.CANDIDATE_ONLY,
    ]


def test_loader_rejects_unsafe_artifact_path(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    manifest = {
        "schema_version": 2,
        "cases": [
            {
                "case_id": "bad",
                "status": "pass",
                "identity": {
                    "model_id": "kws",
                    "engine": "helia-rt",
                    "board": "apollo510_evb",
                    "toolchain": "arm-none-eabi-gcc",
                    "transport": "rtt",
                    "requested_memory": {"preset": "auto"},
                    "requested_power": {"enabled": False},
                    "attempt": 1,
                },
                "health_issues": [],
                "provenance": {},
                "artifacts": {"case_dir": {"path": "../escape", "available": True}},
            }
        ],
    }
    (bundle / "validation_manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(ValidationBundleError, match="Unsafe artifact path"):
        load_validation_bundle(bundle)


def test_loader_accepts_v1_and_infers_terminal_repeat_suffix(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    case_dir = bundle / "board-model-rt-gcc-rtt-auto-run02"
    case_dir.mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "cases": [
            {
                "case_id": case_dir.name,
                "status": "pass",
                "model_id": "model",
                "engine": "helia-rt",
                "board": "board",
                "toolchain": "gcc",
                "transport": "rtt",
                "memory": "auto",
                "power": False,
                "artifacts": {"case_dir": case_dir.name},
            }
        ],
    }
    (bundle / "validation_manifest.json").write_text(json.dumps(manifest))

    loaded = load_validation_bundle(bundle)

    assert loaded.cases[0].identity.attempt == 2
    assert loaded.cases[0].artifact("case_dir").available is True
    assert "schema v1" in loaded.warnings[0]


def test_validation_output_directory_must_be_empty(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    _write_bundle(baseline, 100)
    _write_bundle(candidate, 80)
    output = tmp_path / "output"
    output.mkdir()
    (output / "keep.txt").write_text("keep")

    with pytest.raises(ReportError, match="must be empty"):
        write_validation_compare_artifacts(compare_validation_bundles(baseline, candidate), output)
    assert (output / "keep.txt").read_text() == "keep"
