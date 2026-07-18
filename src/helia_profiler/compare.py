"""Compare two heliaPROFILER result directories."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .evaluation import (
    ComparabilityAssessment,
    ComparisonProfile,
    ComparisonVerdict,
    assess_comparability,
    evaluate_comparison_profile,
)
from .errors import ReportError
from .result_manifest import ResultManifest, load_result_manifest


@dataclass(frozen=True)
class RunArtifacts:
    """Loaded artifacts from one ``hpx profile`` output directory."""

    path: Path
    summary: dict[str, Any]
    metadata: dict[str, Any]
    layers: list[dict[str, Any]]
    layer_memory: dict[Any, list[dict[str, Any]]] = field(default_factory=dict)
    manifest: ResultManifest | None = None


@dataclass(frozen=True)
class MetricDiff:
    """Run-level metric comparison."""

    name: str
    baseline: Any
    candidate: Any
    delta: float | None = None
    delta_pct: float | None = None
    unit: str = ""


@dataclass(frozen=True)
class ConfigDiffRow:
    """One row of the config/provenance comparison table.

    ``baseline``/``candidate`` hold whatever value was found at the
    corresponding path in ``run_metadata.json`` (string, number, list, or
    ``None``) — inherently dynamic, same rationale as ``MetricDiff``.
    """

    field: str
    baseline: Any
    candidate: Any
    status: str  # "same" | "diff"
    key: str = ""


@dataclass(frozen=True)
class CounterDiff:
    """Baseline/candidate/delta for one dynamic per-layer PMU counter column."""

    baseline: float
    candidate: float
    delta: float
    delta_pct: float | None


@dataclass(frozen=True)
class LayerDiffRow:
    """One row of the index-aligned per-layer comparison."""

    id: Any
    baseline_id: Any
    candidate_id: Any
    baseline_op: str
    candidate_op: str
    op_match: bool
    baseline_cycles: float | None
    candidate_cycles: float | None
    delta_cycles: float | None
    delta_pct: float | None
    speedup: float | None
    baseline_overflow: Any
    candidate_overflow: Any
    baseline_memory: str | None = None
    candidate_memory: str | None = None
    memory_changed: bool | None = None
    memory_diff: str | None = None
    # PMU counter columns are dynamic (whatever counters/presets were
    # enabled for the run) — mirrors the LayerResult.counters escape hatch.
    counters: dict[str, CounterDiff] = field(default_factory=dict)

    def to_flat_dict(self) -> dict[str, Any]:
        """Flatten to the legacy dict shape used for CSV serialization."""

        out: dict[str, Any] = {
            "id": self.id,
            "baseline_id": self.baseline_id,
            "candidate_id": self.candidate_id,
            "baseline_op": self.baseline_op,
            "candidate_op": self.candidate_op,
            "op_match": self.op_match,
            "baseline_cycles": self.baseline_cycles,
            "candidate_cycles": self.candidate_cycles,
            "delta_cycles": self.delta_cycles,
            "delta_pct": self.delta_pct,
            "speedup": self.speedup,
            "baseline_overflow": self.baseline_overflow,
            "candidate_overflow": self.candidate_overflow,
        }
        if self.baseline_memory is not None or self.candidate_memory is not None:
            out["baseline_memory"] = self.baseline_memory
            out["candidate_memory"] = self.candidate_memory
            out["memory_changed"] = self.memory_changed
            out["memory_diff"] = self.memory_diff
        for key, counter in self.counters.items():
            out[f"baseline_{key}"] = counter.baseline
            out[f"candidate_{key}"] = counter.candidate
            out[f"delta_{key}"] = counter.delta
            out[f"delta_pct_{key}"] = counter.delta_pct
        return out


@dataclass(frozen=True)
class CompareResult:
    """Full comparison between two profile runs."""

    baseline: RunArtifacts
    candidate: RunArtifacts
    config_rows: list[ConfigDiffRow]
    metrics: list[MetricDiff]
    layer_rows: list[LayerDiffRow]
    warnings: list[str] = field(default_factory=list)
    comparability: ComparabilityAssessment = field(default_factory=ComparabilityAssessment)
    verdict: ComparisonVerdict | None = None


_CONFIG_FIELDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("model_path", "Model path", ("config", "model", "path")),
    ("model_sha256", "Model SHA256", ("model", "sha256")),
    ("engine", "Engine", ("config", "engine", "type")),
    ("backend", "Backend", ("config", "engine", "backend")),
    ("board", "Board", ("config", "target", "board")),
    ("soc", "SoC", ("platform", "soc")),
    ("toolchain", "Toolchain", ("config", "target", "toolchain")),
    ("transport", "Transport", ("config", "target", "transport")),
    ("cpu_clock", "CPU clock", ("platform", "cpu_clock_name")),
    ("iterations", "Iterations", ("config", "profiling", "iterations")),
    ("warmup", "Warmup", ("config", "profiling", "warmup")),
    ("pmu_counters", "PMU counters", ("config", "profiling", "pmu_counters")),
    ("arena_size", "Arena size", ("config", "model", "arena_size")),
    ("arena_location", "Arena location", ("config", "model", "arena_location")),
    ("weights_location", "Weights location", ("config", "model", "weights_location")),
    ("hpx_version", "hpx version", ("hpx_version",)),
)

_METRIC_FIELDS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("total_cycles", ("total_cycles",), "cycles"),
    ("device_profiled_infer_avg_us", ("latency", "device_profiled_infer_avg_us"), "us"),
    ("device_profiled_infer_total_us", ("latency", "device_profiled_infer_total_us"), "us"),
    ("layers", ("layers",), ""),
    ("binary.text", ("binary", "text"), "bytes"),
    ("binary.data", ("binary", "data"), "bytes"),
    ("binary.bss", ("binary", "bss"), "bytes"),
    ("binary.total", ("binary", "total"), "bytes"),
    ("memory.arena_size", ("memory", "arena_size"), "bytes"),
    ("memory.allocated_arena", ("memory", "allocated_arena"), "bytes"),
    ("memory.model_size", ("memory", "model_size"), "bytes"),
    ("power.avg_current_a", ("power", "avg_current_a"), "A"),
    ("power.avg_power_w", ("power", "avg_power_w"), "W"),
    ("power.peak_current_a", ("power", "peak_current_a"), "A"),
    ("power.energy_j", ("power", "energy_j"), "J"),
    ("power.duration_s", ("power", "duration_s"), "s"),
    ("power.energy_per_inference_j", ("power", "energy_per_inference_j"), "J"),
    ("power.inferences_per_joule", ("power", "inferences_per_joule"), "inferences/J"),
)


def compare_runs(
    baseline_dir: Path,
    candidate_dir: Path,
    *,
    profile: ComparisonProfile | None = None,
) -> CompareResult:
    """Load and compare two ``hpx profile`` result directories."""

    baseline = load_run_artifacts(baseline_dir)
    candidate = load_run_artifacts(candidate_dir)
    comparability = assess_comparability(baseline, candidate)
    if not comparability.run_metrics_comparable:
        reasons = "; ".join(
            issue.message
            for issue in comparability.issues
            if issue.severity.value == "blocking"
        )
        raise ReportError(f"Results are not comparable: {reasons}")

    config_rows = _compare_config(baseline.metadata, candidate.metadata)
    metrics = _compare_metrics(
        baseline.summary,
        candidate.summary,
        include_power=comparability.power_metrics_comparable,
    )
    layer_rows = _compare_layers(baseline, candidate) if comparability.layers_comparable else []
    warnings = _build_warnings(baseline, candidate, metrics, comparability)

    result = CompareResult(
        baseline=baseline,
        candidate=candidate,
        config_rows=config_rows,
        metrics=metrics,
        layer_rows=layer_rows,
        comparability=comparability,
        warnings=warnings,
    )
    if profile is None:
        return result
    return replace(result, verdict=evaluate_comparison_profile(result, profile))


def load_run_artifacts(path: Path) -> RunArtifacts:
    """Load and, when published, verify one profile result bundle."""

    run_dir = path.expanduser().resolve()
    if not run_dir.is_dir():
        raise ReportError(f"Compare input is not a directory: {run_dir}")

    declared: set[str] | None = None
    manifest: ResultManifest | None = None
    manifest_path = run_dir / "result_manifest.json"
    if manifest_path.is_file():
        manifest = load_result_manifest(manifest_path, verify=True)
        declared = {artifact.path for artifact in manifest.artifacts}

    summary_path = _declared_artifact(run_dir, "summary.json", declared)
    metadata_path = _declared_artifact(run_dir, "run_metadata.json", declared)
    summary = _read_json(summary_path)
    metadata = _read_json(metadata_path)

    csv_path = run_dir / "profile_results.csv"
    json_path = run_dir / "profile_results.json"
    csv_available = csv_path.is_file() and (declared is None or "profile_results.csv" in declared)
    json_available = json_path.is_file() and (
        declared is None or "profile_results.json" in declared
    )
    if csv_available:
        layers = _read_layer_csv(csv_path)
    elif json_available:
        layers = _read_layer_json(json_path)
    else:
        raise ReportError(
            f"Missing declared per-layer profile results in {run_dir}",
            hint="Expected profile_results.csv or profile_results.json.",
        )

    layer_memory_path = run_dir / "aot_memory_layers.csv"
    layer_memory_available = layer_memory_path.is_file() and (
        declared is None or "aot_memory_layers.csv" in declared
    )
    layer_memory = _read_layer_memory_csv(layer_memory_path) if layer_memory_available else {}

    return RunArtifacts(
        path=run_dir,
        summary=summary,
        metadata=metadata,
        layers=layers,
        layer_memory=layer_memory,
        manifest=manifest,
    )


def write_compare_artifacts(
    result: CompareResult,
    output_dir: Path,
    *,
    source_dirs: tuple[str, str] | None = None,
    omit_empty_layers: bool = False,
) -> list[Path]:
    """Write ``compare_summary.json`` and ``layer_diff.csv``."""

    out = output_dir.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    paths = [out / "compare_summary.json"]
    if result.layer_rows or not omit_empty_layers:
        paths.append(out / "layer_diff.csv")

    summary = {
        "baseline_dir": source_dirs[0] if source_dirs else str(result.baseline.path),
        "candidate_dir": source_dirs[1] if source_dirs else str(result.candidate.path),
        "warnings": result.warnings,
        "comparability": {
            "run_metrics_comparable": result.comparability.run_metrics_comparable,
            "layers_comparable": result.comparability.layers_comparable,
            "power_metrics_comparable": result.comparability.power_metrics_comparable,
            "issues": [
                {
                    "code": issue.code,
                    "severity": issue.severity.value,
                    "message": issue.message,
                    "context": issue.context,
                }
                for issue in result.comparability.issues
            ],
        },
        "config": [
            {
                "field": row.field,
                "baseline": row.baseline,
                "candidate": row.candidate,
                "status": row.status,
            }
            for row in result.config_rows
        ],
        "metrics": [
            {
                "name": m.name,
                "baseline": m.baseline,
                "candidate": m.candidate,
                "delta": m.delta,
                "delta_pct": m.delta_pct,
                "unit": m.unit,
            }
            for m in result.metrics
        ],
    }
    if result.verdict is not None:
        summary["verdict"] = _verdict_to_dict(result.verdict)
    paths[0].write_text(json.dumps(summary, indent=2, default=str) + "\n")

    if len(paths) > 1:
        flat_layer_rows = [row.to_flat_dict() for row in result.layer_rows]
        fieldnames = _layer_fieldnames(flat_layer_rows)
        with open(paths[1], "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(flat_layer_rows)

    return paths


def _verdict_to_dict(verdict: ComparisonVerdict | None) -> dict[str, Any] | None:
    if verdict is None:
        return None
    return {
        "status": verdict.status.value,
        "profile_name": verdict.profile_name,
        "profile_schema": verdict.profile_schema,
        "profile_schema_version": verdict.profile_schema_version,
        "profile_sha256": verdict.profile_sha256,
        "dimension_mismatches": list(verdict.dimension_mismatches),
        "metrics": [
            {
                "metric": item.metric,
                "status": item.status.value,
                "message": item.message,
                "baseline": item.baseline,
                "candidate": item.candidate,
                "regression": item.regression,
                "allowed_regression": item.allowed_regression,
                "unit": item.unit,
            }
            for item in verdict.metrics
        ],
    }


def render_compare(result: CompareResult, *, top_layers: int = 10) -> str:
    """Render a copyable terminal report."""

    lines: list[str] = []
    lines.append("hpx compare")
    lines.append("")
    lines.append(f"Baseline : {result.baseline.path}")
    lines.append(f"Candidate: {result.candidate.path}")
    lines.append("")

    if result.warnings:
        lines.append("Warnings")
        for warning in result.warnings:
            lines.append(f"  - {warning}")
        lines.append("")

    lines.append("Config")
    config_dicts = [
        {
            "field": row.field,
            "baseline": row.baseline,
            "candidate": row.candidate,
            "status": row.status,
        }
        for row in result.config_rows
    ]
    lines.extend(_format_table(["field", "baseline", "candidate", "status"], config_dicts))
    lines.append("")

    lines.append("Run")
    metric_rows = [
        {
            "metric": m.name,
            "baseline": _format_value(m.baseline, m.unit),
            "candidate": _format_value(m.candidate, m.unit),
            "delta": _format_delta(m),
        }
        for m in result.metrics
    ]
    lines.extend(_format_table(["metric", "baseline", "candidate", "delta"], metric_rows))
    lines.append("")

    top_layers = max(0, top_layers)
    lines.append(f"Layers: top {top_layers} by absolute cycle delta")
    top = sorted(
        result.layer_rows,
        key=lambda row: abs(row.delta_cycles or 0.0),
        reverse=True,
    )[:top_layers]
    layer_rows = [
        {
            "id": row.id,
            "op": row.candidate_op if row.op_match else f"{row.baseline_op} -> {row.candidate_op}",
            "baseline": _format_number(row.baseline_cycles),
            "candidate": _format_number(row.candidate_cycles),
            "delta": _format_layer_delta(row),
            "speedup": _format_speedup(row.speedup),
            "memory": row.memory_diff or "",
        }
        for row in top
    ]
    columns = ["id", "op", "baseline", "candidate", "delta", "speedup"]
    if any(row.get("memory") for row in layer_rows):
        columns.append("memory")
    lines.extend(_format_table(columns, layer_rows))
    return "\n".join(lines)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ReportError(f"Missing required compare artifact: {path}")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ReportError(f"Could not parse JSON artifact: {path}", details=str(exc)) from exc
    if not isinstance(data, dict):
        raise ReportError(f"Expected JSON object in {path}")
    return data


def _read_layer_csv(path: Path) -> list[dict[str, Any]]:
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        return [{k: _coerce_csv_value(v) for k, v in row.items()} for row in reader]


def _read_layer_json(path: Path) -> list[dict[str, Any]]:
    data = _read_json(path)
    layers = data.get("layers")
    if not isinstance(layers, list) or not all(isinstance(layer, dict) for layer in layers):
        raise ReportError(f"Expected a layers array of objects in {path}")
    return layers


def _declared_artifact(run_dir: Path, name: str, declared: set[str] | None) -> Path:
    if declared is not None and name not in declared:
        raise ReportError(f"Result manifest does not declare required artifact: {name}")
    return run_dir / name


def _coerce_csv_value(value: str | None) -> Any:
    if value is None:
        return None
    if value in ("True", "False"):
        return value == "True"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _compare_config(base: dict[str, Any], cand: dict[str, Any]) -> list[ConfigDiffRow]:
    rows: list[ConfigDiffRow] = []
    for key, label, path in _CONFIG_FIELDS:
        b = _get_nested(base, path)
        c = _get_nested(cand, path)
        stable_b = _stable_value(b)
        stable_c = _stable_value(c)
        rows.append(
            ConfigDiffRow(
                field=label,
                baseline=stable_b,
                candidate=stable_c,
                status="same" if stable_b == stable_c else "diff",
                key=key,
            )
        )
    return rows


def _compare_metrics(
    base: dict[str, Any],
    cand: dict[str, Any],
    *,
    include_power: bool = True,
) -> list[MetricDiff]:
    metrics: list[MetricDiff] = []
    for name, path, unit in _METRIC_FIELDS:
        if name.startswith("power.") and not include_power:
            continue
        b = _get_nested(base, path)
        c = _get_nested(cand, path)
        if name.startswith("power.") and b is None and c is None:
            continue
        bf = _to_float(b)
        cf = _to_float(c)
        delta = None
        delta_pct = None
        if bf is not None and cf is not None:
            delta = cf - bf
            if bf != 0:
                delta_pct = delta / bf * 100
        metrics.append(
            MetricDiff(
                name=name, baseline=b, candidate=c, delta=delta, delta_pct=delta_pct, unit=unit
            )
        )
    return metrics


def _compare_layers(base_run: RunArtifacts, cand_run: RunArtifacts) -> list[LayerDiffRow]:
    base = base_run.layers
    cand = cand_run.layers
    if len(base) != len(cand) or [row.get("op") for row in base] != [row.get("op") for row in cand]:
        return []
    rows: list[LayerDiffRow] = []
    max_len = max(len(base), len(cand))
    for idx in range(max_len):
        b = base[idx] if idx < len(base) else {}
        c = cand[idx] if idx < len(cand) else {}
        baseline_cycles = _to_float(b.get("cycles"))
        candidate_cycles = _to_float(c.get("cycles"))
        delta_cycles = None
        delta_pct = None
        speedup = None
        if baseline_cycles is not None and candidate_cycles is not None:
            delta_cycles = candidate_cycles - baseline_cycles
            if baseline_cycles != 0:
                delta_pct = delta_cycles / baseline_cycles * 100
            if candidate_cycles != 0:
                speedup = baseline_cycles / candidate_cycles

        baseline_memory: str | None = None
        candidate_memory: str | None = None
        memory_changed: bool | None = None
        memory_diff: str | None = None
        base_mem = _memory_rows_for_layer(base_run.layer_memory, b, idx)
        cand_mem = _memory_rows_for_layer(cand_run.layer_memory, c, idx)
        base_mem_counts = _layer_memory_counts(base_mem)
        cand_mem_counts = _layer_memory_counts(cand_mem)
        base_mem_summary = _format_memory_summary(base_mem_counts)
        cand_mem_summary = _format_memory_summary(cand_mem_counts)
        if base_mem_summary or cand_mem_summary:
            baseline_memory = base_mem_summary
            candidate_memory = cand_mem_summary
            memory_changed = base_mem_summary != cand_mem_summary
            memory_diff = _format_memory_diff(base_mem_counts, cand_mem_counts)

        counters: dict[str, CounterDiff] = {}
        for key in sorted((set(b) & set(c)) - {"id", "op", "cycles", "overflow"}):
            bf = _to_float(b.get(key))
            cf = _to_float(c.get(key))
            if bf is None or cf is None:
                continue
            counters[key] = CounterDiff(
                baseline=bf,
                candidate=cf,
                delta=cf - bf,
                delta_pct=((cf - bf) / bf * 100) if bf else None,
            )

        rows.append(
            LayerDiffRow(
                id=c.get("id", b.get("id", idx)),
                baseline_id=b.get("id"),
                candidate_id=c.get("id"),
                baseline_op=b.get("op", "<missing>"),
                candidate_op=c.get("op", "<missing>"),
                op_match=b.get("op") == c.get("op"),
                baseline_cycles=baseline_cycles,
                candidate_cycles=candidate_cycles,
                delta_cycles=delta_cycles,
                delta_pct=delta_pct,
                speedup=speedup,
                baseline_overflow=b.get("overflow"),
                candidate_overflow=c.get("overflow"),
                baseline_memory=baseline_memory,
                candidate_memory=candidate_memory,
                memory_changed=memory_changed,
                memory_diff=memory_diff,
                counters=counters,
            )
        )
    return rows


def _build_warnings(
    baseline: RunArtifacts,
    candidate: RunArtifacts,
    metrics: list[MetricDiff],
    comparability: ComparabilityAssessment,
) -> list[str]:
    warnings = [issue.message for issue in comparability.issues]
    if baseline.summary.get("overflow_detected") or candidate.summary.get("overflow_detected"):
        warnings.append(
            "PMU overflow detected in at least one run; counter values may be unreliable."
        )
    missing_metrics = [m.name for m in metrics if m.baseline is None or m.candidate is None]
    if missing_metrics:
        warnings.append(
            "Some run-level metrics are missing in one run: " + ", ".join(missing_metrics)
        )
    if (baseline.layer_memory or candidate.layer_memory) and not (
        baseline.layer_memory and candidate.layer_memory
    ):
        warnings.append(
            "AOT memory placement artifacts are present in only one run; placement diffs may be partial."
        )
    return warnings


def _read_layer_memory_csv(path: Path) -> dict[Any, list[dict[str, Any]]]:
    rows = _read_layer_csv(path)
    by_key: dict[Any, list[dict[str, Any]]] = {}
    for row in rows:
        for key in set((row.get("layer_id"), row.get("layer_idx"))):
            if key is not None:
                by_key.setdefault(key, []).append(row)
    return by_key


def _memory_rows_for_layer(
    layer_memory: dict[Any, list[dict[str, Any]]],
    layer: dict[str, Any],
    idx: int,
) -> list[dict[str, Any]]:
    for key in (layer.get("id"), idx):
        if key in layer_memory:
            return layer_memory[key]
        text_key = str(key)
        if text_key in layer_memory:
            return layer_memory[text_key]
    return []


def _layer_memory_counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Return ``role -> placement -> tensor count`` for one layer."""

    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        kind = _friendly_memory_role(
            str(
                row.get("tensor_kind")
                or row.get("arena_role")
                or row.get("tensor_role")
                or "tensor"
            )
        )
        placement = _format_placement(row)
        counts.setdefault(kind, {})[placement] = counts.setdefault(kind, {}).get(placement, 0) + 1
    return counts


def _format_memory_summary(counts: dict[str, dict[str, int]]) -> str:
    if not counts:
        return ""
    parts: list[str] = []
    for kind, placements in sorted(counts.items()):
        placement_text = " + ".join(
            _format_counted_placement(count, placement)
            for placement, count in sorted(placements.items())
        )
        parts.append(f"{kind}: {placement_text}")
    return "; ".join(parts)


def _format_memory_diff(
    base: dict[str, dict[str, int]],
    cand: dict[str, dict[str, int]],
) -> str:
    if not base and not cand:
        return ""
    if base == cand:
        return f"unchanged: {_format_memory_summary(base)}"
    parts: list[str] = []
    for kind in sorted(set(base) | set(cand)):
        base_places = base.get(kind, {})
        cand_places = cand.get(kind, {})
        if base_places == cand_places:
            continue
        before = _format_placement_group(base_places) if base_places else "none"
        after = _format_placement_group(cand_places) if cand_places else "none"
        parts.append(f"{kind}: {before} -> {after}")
    return "; ".join(parts)


def _format_placement(row: dict[str, Any]) -> str:
    memory = str(row.get("memory") or "?").upper()
    source = row.get("source_memory")
    source_s = str(source).upper() if source else memory
    return f"staged {source_s} to {memory}" if source_s != memory else f"in {memory}"


def _format_placement_group(placements: dict[str, int]) -> str:
    return " + ".join(
        _format_counted_placement(count, placement)
        for placement, count in sorted(placements.items())
    )


def _format_counted_placement(count: int, placement: str) -> str:
    noun = "buffer" if count == 1 else "buffers"
    return f"{count} {noun} {placement}"


def _friendly_memory_role(kind: str) -> str:
    labels = {
        "constant": "constants",
        "scratch": "scratch",
        "persistent": "persistent",
        "input": "inputs",
        "output": "outputs",
        "local": "local buffers",
    }
    return labels.get(kind.lower(), kind.lower())


def _get_nested(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    cur: Any = data
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _stable_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return value


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_value(value: Any, unit: str) -> str:
    if value is None:
        return "n/a"
    number = _to_float(value)
    if number is not None:
        return f"{_format_number(number)} {unit}".rstrip()
    return str(value)


def _format_number(value: Any) -> str:
    number = _to_float(value)
    if number is None:
        return "n/a"
    if abs(number) >= 1000:
        return f"{number:,.0f}"
    if number == int(number):
        return str(int(number))
    return f"{number:.2f}"


def _format_delta(metric: MetricDiff) -> str:
    if metric.delta is None:
        return "n/a"
    pct = f" ({metric.delta_pct:+.1f}%)" if metric.delta_pct is not None else ""
    return f"{_format_number(metric.delta)} {metric.unit}{pct}".strip()


def _format_layer_delta(row: LayerDiffRow) -> str:
    delta = row.delta_cycles
    if delta is None:
        return "n/a"
    pct = row.delta_pct
    pct_s = f" ({pct:+.1f}%)" if isinstance(pct, (int, float)) else ""
    return f"{_format_number(delta)}{pct_s}"


def _format_speedup(value: Any) -> str:
    number = _to_float(value)
    if number is None:
        return "n/a"
    return f"{number:.2f}x"


def _format_table(columns: list[str], rows: list[dict[str, Any]]) -> list[str]:
    widths = {col: len(col) for col in columns}
    str_rows: list[dict[str, str]] = []
    for row in rows:
        rendered = {col: str(row.get(col, "")) for col in columns}
        str_rows.append(rendered)
        for col, value in rendered.items():
            widths[col] = max(widths[col], len(value))

    header = "  " + "  ".join(col.ljust(widths[col]) for col in columns)
    sep = "  " + "  ".join("-" * widths[col] for col in columns)
    out = [header, sep]
    for row in str_rows:
        out.append("  " + "  ".join(row[col].ljust(widths[col]) for col in columns))
    return out


def _layer_fieldnames(rows: list[dict[str, Any]]) -> list[str]:
    preferred = [
        "id",
        "baseline_id",
        "candidate_id",
        "baseline_op",
        "candidate_op",
        "op_match",
        "baseline_cycles",
        "candidate_cycles",
        "delta_cycles",
        "delta_pct",
        "speedup",
        "baseline_overflow",
        "candidate_overflow",
        "baseline_memory",
        "candidate_memory",
        "memory_changed",
        "memory_diff",
    ]
    keys = set().union(*(row.keys() for row in rows)) if rows else set()
    return [key for key in preferred if key in keys] + sorted(keys - set(preferred))
