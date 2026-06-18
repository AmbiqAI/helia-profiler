"""Compare two heliaPROFILER result directories."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ReportError


@dataclass(frozen=True)
class RunArtifacts:
    """Loaded artifacts from one ``hpx profile`` output directory."""

    path: Path
    summary: dict[str, Any]
    metadata: dict[str, Any]
    layers: list[dict[str, Any]]
    layer_memory: dict[Any, list[dict[str, Any]]] = field(default_factory=dict)


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
class CompareResult:
    """Full comparison between two profile runs."""

    baseline: RunArtifacts
    candidate: RunArtifacts
    config_rows: list[dict[str, Any]]
    metrics: list[MetricDiff]
    layer_rows: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)


_CONFIG_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Model path", ("config", "model", "path")),
    ("Model SHA256", ("model", "sha256")),
    ("Engine", ("config", "engine", "type")),
    ("Backend", ("config", "engine", "backend")),
    ("Board", ("config", "target", "board")),
    ("SoC", ("platform", "soc")),
    ("Toolchain", ("config", "target", "toolchain")),
    ("Transport", ("config", "target", "transport")),
    ("CPU clock", ("platform", "cpu_clock_name")),
    ("Iterations", ("config", "profiling", "iterations")),
    ("Warmup", ("config", "profiling", "warmup")),
    ("PMU counters", ("config", "profiling", "pmu_counters")),
    ("PMU presets", ("config", "profiling", "pmu_presets")),
    ("Arena size", ("config", "model", "arena_size")),
    ("Model location", ("config", "model", "model_location")),
    ("hpx version", ("hpx_version",)),
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
)


def compare_runs(baseline_dir: Path, candidate_dir: Path) -> CompareResult:
    """Load and compare two ``hpx profile`` result directories."""

    baseline = load_run_artifacts(baseline_dir)
    candidate = load_run_artifacts(candidate_dir)

    config_rows = _compare_config(baseline.metadata, candidate.metadata)
    metrics = _compare_metrics(baseline.summary, candidate.summary)
    layer_rows = _compare_layers(baseline, candidate)
    warnings = _build_warnings(baseline, candidate, config_rows, metrics, layer_rows)

    return CompareResult(
        baseline=baseline,
        candidate=candidate,
        config_rows=config_rows,
        metrics=metrics,
        layer_rows=layer_rows,
        warnings=warnings,
    )


def load_run_artifacts(path: Path) -> RunArtifacts:
    """Load summary, metadata, and per-layer CSV artifacts from *path*."""

    run_dir = path.expanduser().resolve()
    if not run_dir.is_dir():
        raise ReportError(f"Compare input is not a directory: {run_dir}")

    summary = _read_json(run_dir / "summary.json")
    metadata = _read_json(run_dir / "run_metadata.json")
    layers_path = run_dir / "profile_results.csv"
    if not layers_path.is_file():
        raise ReportError(
            f"Missing profile_results.csv in {run_dir}",
            hint="Run `hpx profile` with the default CSV output format before comparing.",
        )
    layers = _read_layer_csv(layers_path)
    layer_memory_path = run_dir / "aot_memory_layers.csv"
    layer_memory = _read_layer_memory_csv(layer_memory_path) if layer_memory_path.is_file() else {}

    return RunArtifacts(
        path=run_dir,
        summary=summary,
        metadata=metadata,
        layers=layers,
        layer_memory=layer_memory,
    )


def write_compare_artifacts(result: CompareResult, output_dir: Path) -> list[Path]:
    """Write ``compare_summary.json`` and ``layer_diff.csv``."""

    out = output_dir.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    paths = [out / "compare_summary.json", out / "layer_diff.csv"]

    summary = {
        "baseline_dir": str(result.baseline.path),
        "candidate_dir": str(result.candidate.path),
        "warnings": result.warnings,
        "config": result.config_rows,
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
    paths[0].write_text(json.dumps(summary, indent=2, default=str) + "\n")

    fieldnames = _layer_fieldnames(result.layer_rows)
    with open(paths[1], "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(result.layer_rows)

    return paths


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
    lines.extend(_format_table(["field", "baseline", "candidate", "status"], result.config_rows))
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
        key=lambda row: abs(_to_float(row.get("delta_cycles")) or 0.0),
        reverse=True,
    )[:top_layers]
    layer_rows = [
        {
            "id": row["id"],
            "op": row["candidate_op"] if row["op_match"] else f"{row['baseline_op']} -> {row['candidate_op']}",
            "baseline": _format_number(row.get("baseline_cycles")),
            "candidate": _format_number(row.get("candidate_cycles")),
            "delta": _format_layer_delta(row),
            "speedup": _format_speedup(row.get("speedup")),
            "memory": row.get("memory_diff", ""),
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


def _compare_config(base: dict[str, Any], cand: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label, path in _CONFIG_FIELDS:
        b = _get_nested(base, path)
        c = _get_nested(cand, path)
        rows.append(
            {
                "field": label,
                "baseline": _stable_value(b),
                "candidate": _stable_value(c),
                "status": "same" if _stable_value(b) == _stable_value(c) else "diff",
            }
        )
    return rows


def _compare_metrics(base: dict[str, Any], cand: dict[str, Any]) -> list[MetricDiff]:
    metrics: list[MetricDiff] = []
    for name, path, unit in _METRIC_FIELDS:
        b = _get_nested(base, path)
        c = _get_nested(cand, path)
        bf = _to_float(b)
        cf = _to_float(c)
        delta = None
        delta_pct = None
        if bf is not None and cf is not None:
            delta = cf - bf
            if bf != 0:
                delta_pct = delta / bf * 100
        metrics.append(MetricDiff(name=name, baseline=b, candidate=c, delta=delta, delta_pct=delta_pct, unit=unit))
    return metrics


def _compare_layers(base_run: RunArtifacts, cand_run: RunArtifacts) -> list[dict[str, Any]]:
    base = base_run.layers
    cand = cand_run.layers
    rows: list[dict[str, Any]] = []
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

        row: dict[str, Any] = {
            "id": c.get("id", b.get("id", idx)),
            "baseline_id": b.get("id"),
            "candidate_id": c.get("id"),
            "baseline_op": b.get("op", "<missing>"),
            "candidate_op": c.get("op", "<missing>"),
            "op_match": b.get("op") == c.get("op"),
            "baseline_cycles": baseline_cycles,
            "candidate_cycles": candidate_cycles,
            "delta_cycles": delta_cycles,
            "delta_pct": delta_pct,
            "speedup": speedup,
            "baseline_overflow": b.get("overflow"),
            "candidate_overflow": c.get("overflow"),
        }
        base_mem = _memory_rows_for_layer(base_run.layer_memory, b, idx)
        cand_mem = _memory_rows_for_layer(cand_run.layer_memory, c, idx)
        base_mem_counts = _layer_memory_counts(base_mem)
        cand_mem_counts = _layer_memory_counts(cand_mem)
        base_mem_summary = _format_memory_summary(base_mem_counts)
        cand_mem_summary = _format_memory_summary(cand_mem_counts)
        if base_mem_summary or cand_mem_summary:
            row["baseline_memory"] = base_mem_summary
            row["candidate_memory"] = cand_mem_summary
            row["memory_changed"] = base_mem_summary != cand_mem_summary
            row["memory_diff"] = _format_memory_diff(base_mem_counts, cand_mem_counts)

        for key in sorted((set(b) & set(c)) - {"id", "op", "cycles", "overflow"}):
            bf = _to_float(b.get(key))
            cf = _to_float(c.get(key))
            if bf is None or cf is None:
                continue
            row[f"baseline_{key}"] = bf
            row[f"candidate_{key}"] = cf
            row[f"delta_{key}"] = cf - bf
            row[f"delta_pct_{key}"] = ((cf - bf) / bf * 100) if bf else None

        rows.append(row)
    return rows


def _build_warnings(
    baseline: RunArtifacts,
    candidate: RunArtifacts,
    config_rows: list[dict[str, Any]],
    metrics: list[MetricDiff],
    layer_rows: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    important = {"Model SHA256", "Board", "SoC", "Engine", "Iterations", "Warmup", "Arena size"}
    changed = [row["field"] for row in config_rows if row["status"] == "diff" and row["field"] in important]
    if changed:
        warnings.append("Important provenance differs: " + ", ".join(changed))
    if baseline.summary.get("overflow_detected") or candidate.summary.get("overflow_detected"):
        warnings.append("PMU overflow detected in at least one run; counter values may be unreliable.")
    if len(baseline.layers) != len(candidate.layers):
        warnings.append(f"Layer counts differ: baseline={len(baseline.layers)}, candidate={len(candidate.layers)}")
    mismatches = sum(1 for row in layer_rows if not row.get("op_match"))
    if mismatches:
        warnings.append(f"{mismatches} layer op name(s) differ; index-aligned layer diffs may be approximate.")
    missing_metrics = [m.name for m in metrics if m.baseline is None or m.candidate is None]
    if missing_metrics:
        warnings.append("Some run-level metrics are missing in one run: " + ", ".join(missing_metrics))
    if (baseline.layer_memory or candidate.layer_memory) and not (baseline.layer_memory and candidate.layer_memory):
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
            str(row.get("tensor_kind") or row.get("arena_role") or row.get("tensor_role") or "tensor")
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


def _format_layer_delta(row: dict[str, Any]) -> str:
    delta = _to_float(row.get("delta_cycles"))
    if delta is None:
        return "n/a"
    pct = row.get("delta_pct")
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
