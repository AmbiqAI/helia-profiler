"""Tests for hardware validation report and manifest artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from helia_profiler.validation.report import (
    build_manifest,
    load_validation_report,
    write_validation_reports,
)
from helia_profiler.validation.runner import CaseResult


def _case(output_dir: Path) -> CaseResult:
    return CaseResult(
        case_id="apollo510_evb-kws-rt-ns-arm-none-eabi-gcc-rtt-auto",
        status="pass",
        duration_s=12.5,
        engine="helia-rt",
        model_id="kws",
        board="apollo510_evb",
        power=False,
        toolchain="arm-none-eabi-gcc",
        transport="rtt",
        memory="auto",
        cmsis_nn_provider="ns",
        layers=13,
        total_cycles=123456,
        latency_avg_us=42.5,
        binary_total_bytes=87_000,
        allocated_arena_bytes=24_000,
        output_dir=str(output_dir / "apollo510_evb-kws-rt-ns-arm-none-eabi-gcc-rtt-auto"),
    )


def test_write_validation_reports_includes_manifest_with_relative_paths(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv(
        "HPX_SOURCE_REVISIONS_JSON",
        json.dumps(
            {
                "ns-cmsis-nn": {
                    "requested_kind": "branch",
                    "requested_ref": "feature/faster-kernels",
                    "resolved_commit": "a" * 40,
                }
            }
        ),
    )
    result = _case(tmp_path)
    case_dir = Path(result.output_dir)
    case_dir.mkdir(parents=True)
    (case_dir / "summary.json").write_text(
        json.dumps(
            {
                "schema": "hpx.run-summary",
                "schema_version": 3,
                "binary": {"text": 80_000, "data": 2_000, "bss": 5_000, "total": 87_000},
                "memory": {
                    "arena_size": 32_768,
                    "allocated_arena": 24_000,
                    "model_size": 53_744,
                    "num_tensors": 17,
                },
                "memory_plan": {
                    "engine": "helia-rt",
                    "model_weight_bytes": 53_744,
                    "regions": [
                        {
                            "region": "DTCM",
                            "capacity": 393_216,
                            "used": 32_768,
                            "consumers": [
                                {"name": "tensor_arena", "size": 32_768, "kind": "arena"}
                            ],
                        }
                    ],
                },
                "memory_regions": {
                    "link_family": "gnu",
                    "linker_profile": "default",
                    "regions": [
                        {
                            "region": "DTCM",
                            "window": {"start": 268435456, "length": 393216},
                            "app_window": {"start": 268435456, "length": 393216},
                            "used": 49_432,
                            "reserved": 343_784,
                            "free": 343_784,
                            "load_image": 0,
                            "window_provenance": "hardware-aperture",
                            "app_provenance": "linker-script",
                        }
                    ],
                    "unattributed": [],
                    "unattributed_load_bytes": 0,
                },
            }
        )
    )
    (case_dir / "run_metadata.json").write_text(
        json.dumps(
            {
                "schema": "hpx.run-metadata",
                "schema_version": 1,
                "hpx_version": "0.1.0",
                "toolchain": {
                    "compiler": "gcc",
                    "compiler_version": "12.2.1",
                    "cmake_version": "3.31.6",
                },
                "engine": {"type": "helia-rt", "version": "1.16.0"},
                "firmware": {"system_clock_hz": 96_000_000},
            }
        )
    )

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
    assert manifest["schema_version"] == 6
    assert manifest["validation"]["suite"] == "smoke"
    assert manifest["summary"] == {"total": 1, "pass": 1, "fail": 0, "skip": 0}
    assert manifest["repo"] == {"sha": None, "branch": None, "dirty": None}
    assert manifest["sources"] == {
        "ns-cmsis-nn": {
            "requested_kind": "branch",
            "requested_ref": "feature/faster-kernels",
            "resolved_commit": "a" * 40,
        }
    }

    case = manifest["cases"][0]
    assert case["metrics"]["total_cycles"] == 123456
    assert case["metrics"]["latency_avg_us"] == 42.5
    assert case["metrics"]["binary_total_bytes"] == 87_000
    assert case["metrics"]["allocated_arena_bytes"] == 24_000
    assert case["identity"]["attempt"] == 1
    assert case["identity"]["cmsis_nn_provider"] == "ns"
    assert case["identity"]["requested_memory"] == {"preset": "auto"}
    assert case["identity"]["requested_power"] == {"enabled": False}
    assert case["health_issues"] == []
    assert case["artifacts"]["case_dir"]["path"] == result.case_id
    assert case["artifacts"]["config"]["path"] == f"{result.case_id}/config.yml"
    assert case["artifacts"]["work_dir"]["path"] == f"{result.case_id}/work"
    assert case["artifacts"]["summary"]["path"] == f"{result.case_id}/summary.json"
    assert case["artifacts"]["run_metadata"]["path"] == f"{result.case_id}/run_metadata.json"
    assert case["provenance"]["hpx_version"] == "0.1.0"
    assert case["provenance"]["compiler_version"] == "12.2.1"
    assert case["provenance"]["runtime"] == {
        "toolchain": {
            "compiler": "gcc",
            "compiler_version": "12.2.1",
            "cmake_version": "3.31.6",
        },
        "engine": {"type": "helia-rt", "version": "1.16.0"},
    }
    assert case["provenance"]["system_clock_hz"] == 96_000_000
    assert case["provenance"]["run_metadata_schema_version"] == 1
    assert case["provenance"]["run_summary_schema_version"] == 3
    assert case["resources"]["binary_sections"] == {
        "text": 80_000,
        "data": 2_000,
        "bss": 5_000,
        "total": 87_000,
    }
    assert case["resources"]["runtime_memory"]["num_tensors"] == 17
    assert case["resources"]["memory_plan"]["regions"][0] == {
        "region": "DTCM",
        "capacity": 393_216,
        "used": 32_768,
        "consumers": [{"name": "tensor_arena", "size": 32_768, "kind": "arena"}],
    }
    # Schema v6 (#177 review M4): the measured block passes through
    # verbatim — this assert is what makes deleting the passthrough line a
    # red test instead of a silent contract regression.
    assert case["resources"]["memory_regions"]["regions"][0]["free"] == 343_784
    assert case["resources"]["memory_regions"]["link_family"] == "gnu"
    report = json.loads((tmp_path / "validation_report.json").read_text())
    assert report["cases"][0]["resources"] == case["resources"]
    assert case["artifacts"]["profile_results"]["path"] == f"{result.case_id}/profile_results.csv"

    loaded = load_validation_report(tmp_path / "validation_report.json")
    assert loaded.cases[0].binary_total_bytes == 87_000
    assert loaded.cases[0].health_issues == ()
    assert loaded.summary.passed == 1


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
    assert manifest["cases"][0]["artifacts"]["case_dir"]["path"] == "skipped-case"
    assert "total_cycles" not in manifest["cases"][0]["metrics"]
    assert manifest["cases"][0]["resources"] == {}
    assert manifest["cases"][0]["error"] == "unsupported combination"

    paths = write_validation_reports([result], tmp_path, repo_root=tmp_path / "missing")
    assert paths
    report = json.loads((tmp_path / "validation_report.json").read_text())
    assert report["cases"][0]["resources"] == {}


def test_build_manifest_records_default_branch_source(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(
        "HPX_SOURCE_REVISIONS_JSON",
        json.dumps(
            {
                "ns-cmsis-nn": {
                    "requested_kind": "default_branch",
                    "requested_ref": "main",
                    "resolved_commit": "b" * 40,
                }
            }
        ),
    )

    manifest = build_manifest([], tmp_path, repo_root=tmp_path / "missing")

    assert manifest["sources"]["ns-cmsis-nn"] == {
        "requested_kind": "default_branch",
        "requested_ref": "main",
        "resolved_commit": "b" * 40,
    }


def test_build_manifest_distinguishes_nightly_and_manual_runs(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "AmbiqAI/helia-profiler")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_RUN_ID", "31033041861")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")

    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.setenv("HPX_VALIDATION_RUN_ORIGIN", "nightly")
    nightly = build_manifest([], tmp_path, repo_root=tmp_path / "missing")
    assert nightly["run"] == {
        "origin": "nightly",
        "github": {
            "event_name": "schedule",
            "repository": "AmbiqAI/helia-profiler",
            "run_id": 31033041861,
            "run_attempt": 2,
            "run_url": "https://github.com/AmbiqAI/helia-profiler/actions/runs/31033041861",
        },
    }

    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("HPX_VALIDATION_RUN_ORIGIN", "manual")
    manual = build_manifest([], tmp_path, repo_root=tmp_path / "missing")
    assert manual["run"]["origin"] == "manual"
    assert manual["run"]["github"]["event_name"] == "workflow_dispatch"


def test_powered_case_publishes_dashboard_metrics_and_detailed_artifact(tmp_path: Path):
    case_dir = tmp_path / "apollo510-power"
    detail_dir = case_dir / "detailed"
    detail_dir.mkdir(parents=True)
    (detail_dir / "power_summary.csv").write_text(
        "scope,metric,value\ngpio_gated_clean_window,avg_power_w,0.00706\n"
    )
    power = {
        "avg_current_a": 0.00393,
        "avg_power_w": 0.00706,
        "peak_current_a": 0.00609,
        "energy_j": 0.03514,
        "capture_duration_s": 4.978,
        "measurement_scope": "gpio_gated_clean_window",
        "integrity": "valid",
        "energy_per_inference_j": 0.000147657,
        "inferences_per_joule": 6772.47,
    }
    (case_dir / "summary.json").write_text(json.dumps({"power": power}))

    result = CaseResult(
        case_id="apollo510-power",
        status="pass",
        duration_s=45.0,
        engine="helia-rt",
        model_id="kws",
        board="apollo510_evb",
        power=True,
        toolchain="arm-none-eabi-gcc",
        transport="rtt",
        memory="auto",
        power_serial="H8MS",
        energy_uj=35_140.0,
        avg_current_ma=3.93,
        avg_power_mw=7.06,
        peak_current_ma=6.09,
        power_capture_duration_s=4.978,
        energy_per_inference_uj=147.657,
        inferences_per_joule=6772.47,
        output_dir=str(case_dir),
    )

    write_validation_reports([result], tmp_path, repo_root=tmp_path / "missing")

    manifest_case = json.loads((tmp_path / "validation_manifest.json").read_text())["cases"][0]
    assert manifest_case["provenance"]["power_serial"] == "H8MS"
    assert manifest_case["metrics"]["avg_power_mw"] == 7.06
    assert manifest_case["metrics"]["energy_per_inference_uj"] == 147.657
    assert manifest_case["metrics"]["inferences_per_joule"] == 6772.47
    assert manifest_case["power_metrics"] == power
    assert manifest_case["artifacts"]["power_summary"] == {
        "path": "apollo510-power/detailed/power_summary.csv",
        "available": True,
    }

    report_case = json.loads((tmp_path / "validation_report.json").read_text())["cases"][0]
    assert report_case["power"] is True
    assert report_case["power_metrics"] == power
    assert report_case["avg_power_mw"] == 7.06
