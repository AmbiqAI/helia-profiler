"""Offline, reconstructed operator-cost views of retained profile bundles."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ._model_explorer_overlay import GradientItem, build_overlay
from .records import _document, _object, load_result
from .results.manifest import ResultValidity, RunStatus

TIME_BASIS = "cumulative aggregated operator cost; not recorded wall-clock time"
PMU_BASIS = (
    "aggregated per-layer totals, not sampled time series; acquisition passes are not "
    "simultaneous; final graph zero closes the display, not another measurement"
)


@dataclass(frozen=True)
class VisualLayer:
    event_id: int | str
    name: str
    cycles: float
    counters: dict[str, float]


@dataclass(frozen=True)
class VisualRun:
    label: str
    root: Path
    manifest_sha256: str
    model_sha256: str
    engine: str
    clock_hz: float
    aggregation: str
    layers: tuple[VisualLayer, ...]
    totals: dict[str, Any]
    conditions: dict[str, Any]


def _number(value: Any, field: str, *, positive: bool = False) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"Missing or nonnumeric {field}")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"Unrepresentable {field}") from exc
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError(f"Invalid {field}")
    return result


def read_visual_run(location: Path, label: str) -> VisualRun:
    """Read only complete, valid JSON profiles; never acquire or reanalyse a model."""
    record = load_result(location)
    if (
        record.manifest.status is not RunStatus.COMPLETE
        or record.manifest.validity is not ResultValidity.VALID
    ):
        raise ValueError("Visualization requires a complete, valid native result")
    artifacts = [
        a
        for a in record.manifest.artifacts
        if a.name == "hpx.profile-layers" and a.media_type == "application/json"
    ]
    if len(artifacts) != 1:
        raise ValueError("Expected exactly one manifest-bound JSON layer table")
    data = _document(record.location, artifacts[0])
    if (
        data.get("schema") != "hpx.profile-results"
        or type(data.get("schema_version")) is not int
        or data["schema_version"] != 1
    ):
        raise ValueError("Unsupported layer table schema")
    # Standalone metadata adds post-capture facts after the JSON table.
    enrichment = {"firmware", "target_lifecycle", "power_terminal", "on_device_power"}
    embedded = data.get("metadata", {})
    if not isinstance(embedded, dict) or {
        k: v for k, v in embedded.items() if k not in enrichment
    } != {k: v for k, v in (record.metadata or {}).items() if k not in enrichment}:
        raise ValueError("Layer table metadata differs from verified run metadata")
    for key in enrichment & embedded.keys():
        if embedded[key] != (record.metadata or {}).get(key):
            raise ValueError(f"Layer {key} differs from run metadata")
    if (
        data.get("overflow_detected") is not False
        or record.summary is None
        or record.summary.overflow_detected
    ):
        raise ValueError("Overflow or missing native summary")
    metadata = record.metadata or {}
    clock = _number(data.get("summary", {}).get("system_clock_hz"), "CPU clock", positive=True)
    if clock != metadata.get("firmware", {}).get("system_clock_hz"):
        raise ValueError("Layer clock differs from run metadata")
    config = metadata.get("config", {})
    aggregation = config.get("profiling", {}).get("aggregation")
    if aggregation not in {"median", "mean", "trimmed"}:
        raise ValueError("Layer aggregation is not recorded")
    rows = data.get("layers")
    if not isinstance(rows, list) or not rows or len(rows) != record.summary.layers:
        raise ValueError("Layer count differs from native summary or is empty")
    layers: list[VisualLayer] = []
    ids: set[int | str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Layer must be an object")
        event, name = row.get("id"), row.get("op")
        if (
            type(event) not in (int, str)
            or event == ""
            or event in ids
            or not isinstance(name, str)
            or not name
        ):
            raise ValueError("Invalid or duplicate layer identity")
        ids.add(event)
        if row.get("overflow") is not False:
            raise ValueError("Layer overflow or missing overflow status")
        cycles = _number(row.get("cycles"), "layer cycles")
        if cycles != _number(row.get("ARM_PMU_CPU_CYCLES"), "CPU cycle counter"):
            raise ValueError("Layer cycles disagree with CPU cycle counter")
        counters = {
            key: _number(value, key)
            for key, value in row.items()
            if key.startswith("ARM_PMU_") and value is not None
        }
        layers.append(VisualLayer(event, name, cycles, counters))
    model = metadata.get("model", {}).get("sha256")
    if not isinstance(model, str) or len(model) != 64:
        raise ValueError("Model identity is not recorded")
    latency = record.summary.latency
    totals = {
        "operator_cycles": math.fsum(layer.cycles for layer in layers),
        "profiled_mean_us": latency.device_profiled_infer_avg_us if latency else None,
        "profiled_count": latency.device_profiled_infer_count if latency else None,
        "clean_mean_us": latency.device_clean_infer_avg_us if latency else None,
        "clean_count": latency.device_clean_infer_count if latency else None,
        "clean_scope": latency.clean_workload if latency else None,
    }
    return VisualRun(
        label,
        record.location,
        record.manifest_sha256,
        model,
        record.summary.engine,
        clock,
        aggregation,
        tuple(layers),
        totals,
        {
            "platform": metadata.get("platform"),
            "model": config.get("model"),
            "profiling": config.get("profiling"),
            "engine": metadata.get("engine"),
            "firmware": metadata.get("firmware"),
        },
    )


def _mapping(
    runs: Sequence[VisualRun], path: Path | None
) -> tuple[dict[str, dict[int | str, dict]], dict]:
    if path is None:
        return {}, {}
    raw = path.read_bytes()
    data = _object(raw)
    if (
        data.get("schema") != "hpx.visualization-mapping"
        or type(data.get("schema_version")) is not int
        or data["schema_version"] != 1
    ):
        raise ValueError("Unsupported visualization mapping")
    if not data.get("basis") or any(run.model_sha256 != data.get("model_sha256") for run in runs):
        raise ValueError("Mapping needs an attribution basis and the exact shared model")
    by_run = data.get("runs", {})
    mapped: dict[str, dict[int | str, dict]] = {}
    for run in runs:
        known = {layer.event_id: layer.name for layer in run.layers}
        rows = by_run.get(run.manifest_sha256, [])
        subjects: set[tuple[str, str]] = set()
        selected: dict[int | str, dict] = {}
        for row in rows:
            event = row.get("event_id")
            if (
                type(event) not in (int, str)
                or event not in known
                or event in selected
                or row.get("event_name") != known[event]
            ):
                raise ValueError("Mapping has an unknown, duplicate or mismatched event")
            graph, node = row.get("graph_id"), row.get("node_id")
            if not isinstance(graph, str) or not graph or not isinstance(node, str) or not node:
                raise ValueError("Mapping requires explicit graph and node IDs")
            subject = (graph, node)
            if subject in subjects:
                raise ValueError("Many events map to one node; no implicit aggregation")
            subjects.add(subject)
            selected[event] = row
        mapped[run.manifest_sha256] = selected
    return mapped, {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "basis": data["basis"],
        "status": "caller-supplied attribution, bound to native identities",
        "document": data,
    }


def _counter_tracks(run: VisualRun, pid: int, spans: list[tuple[int, int]]) -> list[dict]:
    events: list[dict] = []
    metrics = sorted({metric for layer in run.layers for metric in layer.counters})
    for tid, metric in enumerate(metrics, 3):
        name = f"{metric.removeprefix('ARM_PMU_')} (per-layer)"
        if all(metric in layer.counters for layer in run.layers) and all(
            end > start for start, end in spans
        ):
            for layer, (start, _) in zip(run.layers, spans, strict=True):
                events.append(
                    {
                        "ph": "C",
                        "pid": pid,
                        "tid": 0,
                        "name": name,
                        "ts": start / 1000,
                        "args": {"per-layer total": layer.counters[metric]},
                    }
                )
            events.append(
                {
                    "ph": "C",
                    "pid": pid,
                    "tid": 0,
                    "name": name,
                    "ts": spans[-1][1] / 1000,
                    "args": {"per-layer total": 0},
                }
            )
            continue
        # A graph would hold a missing value forward or erase a zero-width operator.
        events.append(
            {"ph": "M", "pid": pid, "tid": tid, "name": "thread_name", "args": {"name": name}}
        )
        for layer, (start, end) in zip(run.layers, spans, strict=True):
            if metric not in layer.counters:
                continue
            value = layer.counters[metric]
            events.extend(
                [
                    {
                        "ph": "B",
                        "pid": pid,
                        "tid": tid,
                        "ts": start / 1000,
                        "name": f"{layer.name}: {value:g}",
                        "args": {
                            "value": value,
                            "metric": metric,
                            "event_id": layer.event_id,
                            "pmu_basis": PMU_BASIS,
                        },
                    },
                    {"ph": "E", "pid": pid, "tid": tid, "ts": end / 1000},
                ]
            )
    return events


def _trace(runs: Sequence[VisualRun], mapping: dict) -> dict:
    events: list[dict] = []
    for pid, run in enumerate(runs, 1):
        events.extend(
            [
                {
                    "ph": "M",
                    "pid": pid,
                    "tid": 0,
                    "name": "process_name",
                    "args": {"name": run.label},
                },
                {
                    "ph": "M",
                    "pid": pid,
                    "tid": 1,
                    "name": "thread_name",
                    "args": {"name": f"{run.aggregation} operator costs — reconstructed"},
                },
                {
                    "ph": "M",
                    "pid": pid,
                    "tid": 2,
                    "name": "thread_name",
                    "args": {"name": "separate profiled invocation mean"},
                },
            ]
        )
        cursor_cycles = 0.0
        cursor_ns = 0
        spans: list[tuple[int, int]] = []
        for layer in run.layers:
            cursor_cycles += layer.cycles
            end_ns_float = cursor_cycles / run.clock_hz * 1e9
            if not math.isfinite(end_ns_float):
                raise ValueError("Unrepresentable cumulative duration")
            end_ns = round(end_ns_float)
            annotation = mapping.get(run.manifest_sha256, {}).get(layer.event_id, {})
            args = {
                "event_id": layer.event_id,
                "cycles": layer.cycles,
                "cpu_hz": run.clock_hz,
                "aggregation": run.aggregation,
                "time_basis": TIME_BASIS,
                "manifest_sha256": run.manifest_sha256,
                "counters": layer.counters,
                "pmu_basis": PMU_BASIS,
                "attribution": annotation,
            }
            events.append(
                {
                    "ph": "B",
                    "pid": pid,
                    "tid": 1,
                    "ts": cursor_ns / 1000,
                    "cat": "reconstructed operator cost",
                    "name": layer.name,
                    "args": args,
                }
            )
            events.append({"ph": "E", "pid": pid, "tid": 1, "ts": end_ns / 1000})
            spans.append((cursor_ns, end_ns))
            cursor_ns = end_ns
        events.extend(_counter_tracks(run, pid, spans))
        if run.totals["profiled_mean_us"] is not None:
            duration = _number(run.totals["profiled_mean_us"], "profiled mean")
            events.append(
                {
                    "ph": "X",
                    "pid": pid,
                    "tid": 2,
                    "ts": 0,
                    "dur": duration,
                    "cat": "whole invocation summary",
                    "name": "Profiled invocation mean (separate statistic)",
                    "args": run.totals,
                }
            )
    return {
        "traceEvents": events,
        "displayTimeUnit": "ms",
        "otherData": {
            "time_basis": TIME_BASIS,
            "pmu_basis": PMU_BASIS,
            "alignment": "each run starts at zero; no simultaneity, idle, overlap or power alignment inferred",
        },
    }


def _overlay(rows: Sequence[tuple[str, str, float]], metric: str, *, delta: bool = False) -> dict:
    graphs: dict[str, dict[str, float]] = {}
    for graph, node, value in rows:
        graphs.setdefault(graph, {})[node] = value
    output: dict = {}
    for graph, values in graphs.items():
        # Explicit colors keep zero neutral even when the delta range is asymmetric.
        overlay = build_overlay(
            values,
            metric_name=metric,
            graph_id=graph,
            gradient=[
                GradientItem(0, "#2563eb"),
                GradientItem(0.5, "#ffffff"),
                GradientItem(1, "#dc2626"),
            ]
            if delta
            else None,
        )
        if delta:
            for node, value in values.items():
                overlay.graphsData[graph].results[node].bgColor = (
                    "#dc2626" if value > 0 else "#2563eb" if value < 0 else "#eeeeee"
                )
        output.update(json.loads(overlay.to_json()))
    return output


def export_visualization(
    runs: Sequence[VisualRun], output: Path, *, mapping_path: Path | None = None
) -> Path:
    """Write a new offline view directory; external mappings enable graph overlays/deltas."""
    if not 1 <= len(runs) <= 2 or len({run.label for run in runs}) != len(runs):
        raise ValueError("Supply one or two uniquely labeled runs")
    output = output.resolve()
    if any(output == run.root or output.is_relative_to(run.root) for run in runs):
        raise ValueError("Visualization output must be outside native bundles")
    mapping, provenance = _mapping(runs, mapping_path)
    documents: dict[str, Any] = {"comparison.perfetto.json": _trace(runs, mapping)}
    summary: dict = {
        "time_basis": TIME_BASIS,
        "mapping": provenance,
        "runs": [],
        "comparison": "descriptive only; no equivalence or speedup qualification",
    }
    compared: list[dict] = []
    by_subject: list[dict] = []
    for index, run in enumerate(runs, 1):
        documents[f"run-{index}.perfetto.json"] = _trace([run], mapping)
        mapped = mapping.get(run.manifest_sha256, {})
        subjects = {
            (item["graph_id"], item["node_id"]): layer
            for layer in run.layers
            if (item := mapped.get(layer.event_id)) is not None
        }
        by_subject.append(subjects)
        if subjects:
            documents[f"run-{index}.latency.overlay.json"] = _overlay(
                [(g, n, layer.cycles / run.clock_hz * 1000) for (g, n), layer in subjects.items()],
                f"{run.label}: {run.aggregation} ms",
            )
            for metric_index, metric in enumerate(
                sorted({key for layer in subjects.values() for key in layer.counters}), 1
            ):
                documents[f"run-{index}.counter-{metric_index}.overlay.json"] = _overlay(
                    [
                        (g, n, layer.counters[metric])
                        for (g, n), layer in subjects.items()
                        if metric in layer.counters
                    ],
                    f"{run.label}: {metric}",
                )
        summary["runs"].append(
            {
                "label": run.label,
                "engine": run.engine,
                "manifest_sha256": run.manifest_sha256,
                "model_sha256": run.model_sha256,
                "clock_hz": run.clock_hz,
                "aggregation": run.aggregation,
                "totals": run.totals,
                "conditions": run.conditions,
                "layers": len(run.layers),
                "mapped": len(subjects),
                "unmapped_event_ids": [
                    layer.event_id for layer in run.layers if layer.event_id not in mapped
                ],
            }
        )
    if len(runs) == 2 and mapping:
        for graph, node in sorted(by_subject[0].keys() | by_subject[1].keys()):
            left, right = (subjects.get((graph, node)) for subjects in by_subject)
            a = left.cycles / runs[0].clock_hz * 1000 if left else None
            b = right.cycles / runs[1].clock_hz * 1000 if right else None
            delta = b - a if a is not None and b is not None else None
            compared.append(
                {
                    "graph_id": graph,
                    "node_id": node,
                    "baseline_event": left.name if left else None,
                    "candidate_event": right.name if right else None,
                    "baseline_ms": a,
                    "candidate_ms": b,
                    "delta_ms": delta,
                    "delta_pct": delta / a * 100 if delta is not None and a else None,
                }
            )
        for field in ("delta_ms", "delta_pct"):
            values = [
                (row["graph_id"], row["node_id"], row[field])
                for row in compared
                if row[field] is not None
            ]
            if values:
                documents[f"{field}.overlay.json"] = _overlay(
                    values, f"candidate − baseline: {field}", delta=True
                )
    summary["layers"] = compared
    documents["comparison.json"] = summary
    encoded = {
        name: json.dumps(value, indent=2, allow_nan=False) + "\n"
        for name, value in documents.items()
    }
    output.mkdir(parents=True, exist_ok=False)
    for name, content in encoded.items():
        (output / name).write_text(content, encoding="utf-8")
    if provenance:
        (output / "mapping.json").write_text(
            json.dumps(provenance["document"], indent=2) + "\n", encoding="utf-8"
        )
    if compared:
        with (output / "comparison.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(compared[0]))
            writer.writeheader()
            writer.writerows(compared)
    return output
