"""Tests for hardware validation report and manifest artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from helia_profiler.validation.report import build_manifest, write_validation_reports
from helia_profiler.validation.runner import CaseResult


def _case(output_dir: Path) -> CaseResult:
    return CaseResult(
        case_id="apollo510_evb-kws-rt-arm-none-eabi-gcc-rtt-auto",
        status="pass",
        duration_s=12.5,
        engine="helia-rt",
        model_id="kws",
        board="apollo510_evb",
        power=False,
        toolchain="arm-none-eabi-gcc",
        transport="rtt",
        memory="auto",
        layers=13,
        total_cycles=123456,
        output_dir=str(output_dir / "apollo510_evb-kws-rt-arm-none-eabi-gcc-rtt-auto"),
    )


def test_write_validation_reports_includes_manifest_with_relative_paths(tmp_path: Path):
    result = _case(tmp_path)

    paths = write_validation_reports(
        [result],
        tmp_path,
        validation_options={
            "suite": "smoke",
            "boards": "apollo510_evb",
            "power": "off",
            "timeout_s": 900.0,
        },
        repo_root=tmp_path / "not-a-git-repo",
    )

    assert {p.name for p in paths} == {
        "validation_report.json",
        "validation_report.md",
        "validation_manifest.json",
    }
    manifest = json.loads((tmp_path / "validation_manifest.json").read_text())
    assert manifest["schema_version"] == 1
    assert manifest["validation"]["suite"] == "smoke"
    assert manifest["summary"] == {"total": 1, "pass": 1, "fail": 0, "skip": 0}
    assert manifest["repo"] == {"sha": None, "branch": None, "dirty": None}

    case = manifest["cases"][0]
    assert case["metrics"]["total_cycles"] == 123456
    assert case["artifacts"]["case_dir"] == result.case_id
    assert case["artifacts"]["config"] == f"{result.case_id}/config.yml"
    assert case["artifacts"]["work_dir"] == f"{result.case_id}/work"
    assert case["artifacts"]["summary"] == f"{result.case_id}/summary.json"
    assert case["artifacts"]["run_metadata"] == f"{result.case_id}/run_metadata.json"
    assert case["artifacts"]["profile_results"] == f"{result.case_id}/profile_results.csv"


def test_build_manifest_omits_none_metrics_and_tolerates_missing_git(tmp_path: Path):
    result = CaseResult(
        case_id="skipped-case",
        status="skip",
        duration_s=0.0,
        engine="helia-rt",
        model_id="kws",
        board="apollo510_evb",
        power=False,
        toolchain="arm-none-eabi-gcc",
        transport="rtt",
        memory="auto",
        error="unsupported combination",
    )

    manifest = build_manifest([result], tmp_path, repo_root=tmp_path / "missing")

    assert manifest["repo"]["sha"] is None
    assert manifest["repo"]["dirty"] is None
    assert manifest["summary"]["skip"] == 1
    assert manifest["cases"][0]["artifacts"]["case_dir"] == "skipped-case"
    assert "total_cycles" not in manifest["cases"][0]["metrics"]
    assert manifest["cases"][0]["error"] == "unsupported combination"
