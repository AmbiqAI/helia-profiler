"""Build a browser-friendly historical dataset from validation bundles."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..errors import ReportError
from ..validation.bundle import (
    ValidationBundle,
    ValidationBundleCase,
    load_validation_bundle,
    resolve_artifact,
)

DATASET_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RegressionLayer:
    index: int
    op: str
    cycles: float | None
    cycles_pct: float | None
    overflow: bool | None
    macs: float | None
    ops: float | None
    counters: dict[str, float]


@dataclass(frozen=True)
class RegressionCase:
    case_id: str
    identity_key: str
    identity: dict[str, Any]
    status: str
    duration_s: float | None
    health_issues: tuple[str, ...]
    provenance: dict[str, Any]
    metrics: dict[str, float | int | None]
    layer_path: str | None


@dataclass(frozen=True)
class RegressionRun:
    run_id: str
    generated_at: str | None
    suite: str | None
    hpx_version: str | None
    repo: dict[str, Any]
    summary: dict[str, int]
    cases: tuple[RegressionCase, ...]


def build_regression_dataset(bundle_dirs: list[Path], output_dir: Path) -> tuple[Path, ...]:
    """Normalize completed validation bundles into a static website dataset."""

    if not bundle_dirs:
        raise ReportError("At least one validation bundle is required")

    output = output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    runs_dir = output / "runs"
    layers_dir = output / "layers"
    runs_dir.mkdir(exist_ok=True)
    layers_dir.mkdir(exist_ok=True)

    runs: list[RegressionRun] = []
    written: list[Path] = []
    seen_ids: set[str] = set()
    for bundle_dir in bundle_dirs:
        bundle = load_validation_bundle(bundle_dir)
        manifest = json.loads((bundle.root / "validation_manifest.json").read_text())
        run_id = _run_id(bundle)
        if run_id in seen_ids:
            raise ReportError(f"Duplicate regression run ID: {run_id}")
        seen_ids.add(run_id)
        run, layer_paths = _build_run(bundle, manifest, run_id, output)
        runs.append(run)
        written.extend(layer_paths)
        run_path = runs_dir / f"{run_id}.json"
        _write_json(run_path, _run_document(run))
        written.append(run_path)

    runs.sort(key=lambda run: run.generated_at or "")
    catalog_path = output / "catalog.json"
    catalog = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "runs": [_catalog_entry(run) for run in runs],
    }
    _write_json(catalog_path, catalog)
    return (catalog_path, *written)


def _build_run(
    bundle: ValidationBundle,
    manifest: dict[str, Any],
    run_id: str,
    output: Path,
) -> tuple[RegressionRun, list[Path]]:
    raw_by_id = {
        case.get("case_id"): case
        for case in manifest.get("cases", [])
        if isinstance(case, dict) and isinstance(case.get("case_id"), str)
    }
    cases: list[RegressionCase] = []
    written: list[Path] = []
    for case in bundle.cases:
        raw_case = raw_by_id.get(case.case_id, {})
        summary = _read_json_artifact(bundle, case, "summary") or {}
        layers = _read_layers(bundle, case)
        layer_path: str | None = None
        if layers:
            slug = _safe_slug(case.case_id)
            path = output / "layers" / run_id / f"{slug}.json"
            _write_json(
                path,
                {
                    "schema_version": DATASET_SCHEMA_VERSION,
                    "run_id": run_id,
                    "case_id": case.case_id,
                    "identity": case.identity.to_dict(),
                    "layers": [asdict(layer) for layer in layers],
                },
            )
            layer_path = path.relative_to(output).as_posix()
            written.append(path)
        identity = case.identity.to_dict()
        cases.append(
            RegressionCase(
                case_id=case.case_id,
                identity_key=json.dumps(identity, sort_keys=True, separators=(",", ":")),
                identity=identity,
                status=case.status,
                duration_s=_number(raw_case.get("duration_s")),
                health_issues=case.health_issues,
                provenance=dict(case.provenance),
                metrics=_summary_metrics(summary, raw_case),
                layer_path=layer_path,
            )
        )

    validation = manifest.get("validation") if isinstance(manifest.get("validation"), dict) else {}
    summary = manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {}
    return (
        RegressionRun(
            run_id=run_id,
            generated_at=bundle.metadata.generated_at,
            suite=validation.get("suite") if isinstance(validation.get("suite"), str) else None,
            hpx_version=bundle.metadata.hpx_version,
            repo={
                "sha": bundle.metadata.repo_sha,
                "branch": bundle.metadata.repo_branch,
                "dirty": bundle.metadata.repo_dirty,
            },
            summary={
                "total": int(summary.get("total", len(cases))),
                "pass": int(summary.get("pass", 0)),
                "fail": int(summary.get("fail", 0)),
                "skip": int(summary.get("skip", 0)),
            },
            cases=tuple(cases),
        ),
        written,
    )


def _read_json_artifact(
    bundle: ValidationBundle, case: ValidationBundleCase, name: str
) -> dict[str, Any] | None:
    artifact = case.artifact(name)
    if artifact is None or not artifact.available:
        return None
    path = resolve_artifact(bundle, artifact)
    if not path.is_file():
        return None
    value = json.loads(path.read_text())
    return value if isinstance(value, dict) else None


def _read_layers(bundle: ValidationBundle, case: ValidationBundleCase) -> tuple[RegressionLayer, ...]:
    artifact = case.artifact("profile_results")
    if artifact is None or not artifact.available:
        return ()
    path = resolve_artifact(bundle, artifact)
    if not path.is_file():
        return ()
    layers: list[RegressionLayer] = []
    with path.open(newline="") as handle:
        for position, row in enumerate(csv.DictReader(handle)):
            known = {
                "id",
                "op",
                "cycles",
                "cycles_pct",
                "overflow",
                "macs",
                "ops",
                "cycles_per_mac",
                "cycles_per_op",
            }
            counters = {
                key: value
                for key, raw in row.items()
                if key not in known and (value := _number(raw)) is not None
            }
            layers.append(
                RegressionLayer(
                    index=_integer(row.get("id"), default=position),
                    op=str(row.get("op") or "UNKNOWN"),
                    cycles=_number(row.get("cycles")),
                    cycles_pct=_number(row.get("cycles_pct")),
                    overflow=_boolean(row.get("overflow")),
                    macs=_number(row.get("macs")),
                    ops=_number(row.get("ops")),
                    counters=counters,
                )
            )
    return tuple(layers)


def _summary_metrics(
    summary: dict[str, Any], raw_case: dict[str, Any]
) -> dict[str, float | int | None]:
    latency = summary.get("latency") if isinstance(summary.get("latency"), dict) else {}
    memory = summary.get("memory") if isinstance(summary.get("memory"), dict) else {}
    binary = summary.get("binary_size") if isinstance(summary.get("binary_size"), dict) else {}
    manifest_metrics = (
        raw_case.get("metrics") if isinstance(raw_case.get("metrics"), dict) else {}
    )
    return {
        "total_cycles": _number(summary.get("total_cycles"))
        or _number(manifest_metrics.get("total_cycles")),
        "layers": _integer(summary.get("layers"), default=0),
        "profiled_infer_avg_us": _number(latency.get("device_profiled_infer_avg_us")),
        "clean_infer_avg_cycles": _number(latency.get("device_clean_infer_avg_cycles")),
        "clean_infer_avg_us": _number(latency.get("device_clean_infer_avg_us")),
        "arena_allocated_bytes": _number(memory.get("allocated_arena")),
        "model_size_bytes": _number(memory.get("model_size")),
        "binary_text_bytes": _number(binary.get("text")),
        "binary_data_bytes": _number(binary.get("data")),
        "binary_bss_bytes": _number(binary.get("bss")),
        "binary_total_bytes": _number(binary.get("total")),
    }


def _run_id(bundle: ValidationBundle) -> str:
    timestamp = re.sub(r"[^0-9TZ]", "", bundle.metadata.generated_at or "unknown")[:16]
    sha = (bundle.metadata.repo_sha or "no-sha")[:8]
    digest = hashlib.sha256(str(bundle.root).encode()).hexdigest()[:6] if timestamp == "unknown" else ""
    return "-".join(part for part in (timestamp, sha, digest) if part)


def _catalog_entry(run: RegressionRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "generated_at": run.generated_at,
        "suite": run.suite,
        "hpx_version": run.hpx_version,
        "repo": run.repo,
        "summary": run.summary,
        "path": f"runs/{run.run_id}.json",
    }


def _run_document(run: RegressionRun) -> dict[str, Any]:
    return {
        "schema_version": DATASET_SCHEMA_VERSION,
        "run_id": run.run_id,
        "generated_at": run.generated_at,
        "suite": run.suite,
        "hpx_version": run.hpx_version,
        "repo": run.repo,
        "summary": run.summary,
        "cases": [asdict(case) for case in run.cases],
    }


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-.")
    return slug[:160] or "case"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any, *, default: int) -> int:
    number = _number(value)
    return int(number) if number is not None else default


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
    return None
