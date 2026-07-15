"""Tests for regression dashboard dataset export."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from helia_profiler.regression import build_regression_dataset


def _bundle(root: Path, *, generated_at: str, cycles: int) -> Path:
    case_dir = root / "board-model-engine-toolchain-rtt-auto"
    case_dir.mkdir(parents=True)
    (case_dir / "summary.json").write_text(
        json.dumps(
            {
                "layers": 1,
                "total_cycles": cycles,
                "latency": {"device_profiled_infer_avg_us": 42},
                "memory": {"allocated_arena": 1024},
            }
        )
    )
    with (case_dir / "profile_results.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["id", "op", "cycles", "cycles_pct", "ARM_PMU_CPU_CYCLES"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "id": 0,
                "op": "CONV_2D",
                "cycles": cycles,
                "cycles_pct": 100,
                "ARM_PMU_CPU_CYCLES": cycles,
            }
        )
    (case_dir / "run_metadata.json").write_text("{}")
    manifest = {
        "schema_version": 2,
        "generated_at": generated_at,
        "hpx_version": "0.1.0",
        "repo": {"sha": "a" * 40, "branch": "main", "dirty": False},
        "validation": {"suite": "complete"},
        "summary": {"total": 1, "pass": 1, "fail": 0, "skip": 0},
        "cases": [
            {
                "case_id": case_dir.name,
                "status": "pass",
                "duration_s": 3.5,
                "identity": {
                    "model_id": "model",
                    "engine": "helia-rt",
                    "board": "board",
                    "toolchain": "toolchain",
                    "transport": "rtt",
                    "requested_memory": {"preset": "auto"},
                    "requested_power": {"enabled": False},
                    "attempt": 1,
                },
                "health_issues": [],
                "provenance": {"model_sha256": "b" * 64},
                "metrics": {"layers": 1, "total_cycles": cycles},
                "artifacts": {
                    "case_dir": {"path": case_dir.name, "available": True},
                    "summary": {
                        "path": f"{case_dir.name}/summary.json",
                        "available": True,
                    },
                    "run_metadata": {
                        "path": f"{case_dir.name}/run_metadata.json",
                        "available": True,
                    },
                    "profile_results": {
                        "path": f"{case_dir.name}/profile_results.csv",
                        "available": True,
                    },
                },
            }
        ],
    }
    (root / "validation_manifest.json").write_text(json.dumps(manifest))
    return root


def test_build_regression_dataset_exports_runs_and_lazy_layers(tmp_path: Path) -> None:
    first = _bundle(tmp_path / "first", generated_at="2026-07-13T01:02:03Z", cycles=100)
    second = _bundle(tmp_path / "second", generated_at="2026-07-14T01:02:03Z", cycles=110)

    output = tmp_path / "dataset"
    build_regression_dataset([first, second], output)

    catalog = json.loads((output / "catalog.json").read_text())
    assert catalog["schema_version"] == 1
    assert len(catalog["runs"]) == 2
    run = json.loads((output / catalog["runs"][0]["path"]).read_text())
    case = run["cases"][0]
    assert case["metrics"]["total_cycles"] == 100
    assert case["identity"]["model_id"] == "model"
    layers = json.loads((output / case["layer_path"]).read_text())
    assert layers["layers"][0]["op"] == "CONV_2D"
    assert layers["layers"][0]["counters"]["ARM_PMU_CPU_CYCLES"] == 100
