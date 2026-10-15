"""Native profile mapping to the schema-owned portable result view."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .records import ProfileResultRecord, ResultReadError
from .results.manifest import ResultValidity, RunStatus


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _section(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any, *, integer: bool = False) -> int | float | None:
    if value is None:
        return None
    types = (int,) if integer else (int, float)
    if type(value) not in types or value < 0:
        raise ResultReadError("invalid_measurement", "Expected a nonnegative native measurement")
    return value


def _milliseconds(value: int | float | None) -> float | None:
    if value is None:
        return None
    try:
        converted = value / 1000
    except OverflowError as exc:
        raise ResultReadError("invalid_measurement", "Latency cannot be represented in ms") from exc
    if not math.isfinite(converted):
        raise ResultReadError("invalid_measurement", "Latency cannot be represented in ms")
    return converted


def observations(record: ProfileResultRecord) -> dict[str, Any]:
    """Map verified native facts; schema validity alone does not establish verification.

    This first mapping covers ordinary profile timing and memory, not fixture
    captures, rail energy, per-layer attribution or numerical acceptance.
    """
    manifest, summary = record.manifest, record.summary
    metadata = record.metadata or {}
    model, engine, platform = (
        _section(metadata.get(name)) for name in ("model", "engine", "platform")
    )
    refs = {a.name: f"artifact:{a.path}" for a in manifest.artifacts if a.name}
    summary_ref = refs.get("hpx.summary")
    metadata_ref = refs.get("hpx.run-metadata")
    artifacts = [
        {
            "id": f"artifact:{a.path}",
            "role": a.name or a.role or "native-artifact",
            "location": a.path,
            "sha256": a.sha256,
            "size_bytes": a.size_bytes,
        }
        for a in manifest.artifacts
    ]
    artifacts.append(
        {
            "id": "native-manifest",
            "role": "hpx.result-manifest",
            "location": record.manifest_path.name,
            "sha256": record.manifest_sha256,
            "size_bytes": record.manifest_size,
        }
    )
    items: list[dict[str, Any]] = []
    blocked = (
        "native_run_not_complete"
        if manifest.status is not RunStatus.COMPLETE
        else "native_measurements_invalid"
        if manifest.validity is ResultValidity.INVALID
        else None
    )

    def metric(
        name,
        pointer,
        value,
        *,
        unit,
        scope,
        origin,
        statistic,
        count=None,
        transformation=None,
        reason=None,
        clocks=False,
        supporting_pointers=(),
    ):
        unavailable = blocked or reason or ("not_recorded" if value is None else None)
        basis = None
        if summary_ref is not None and value is not None:
            basis = {
                "kind": "derived" if transformation else "reported",
                "sources": [
                    {"artifact": summary_ref, "pointer": source}
                    for source in (pointer, *supporting_pointers)
                ],
                "transformation": transformation,
            }
            if clocks:
                basis["clocks"] = clock_facts
        evidence = [summary_ref] if summary_ref else []
        if basis and basis.get("clocks") and metadata_ref:
            evidence.append(metadata_ref)
        items.append(
            {
                "id": name,
                "metric": name,
                "unit": unit,
                "domain": "cpu" if unit == "ms" else "memory",
                "origin": origin,
                "scope": scope,
                "statistic": statistic,
                "value": None if unavailable else value,
                "unavailable_reason": unavailable,
                "sample_count": None if blocked else count,
                "evidence": evidence,
                "basis": basis,
            }
        )

    firmware = _section(metadata.get("firmware"))
    clock_facts = []
    if metadata_ref is not None:
        for name, role in (("system_clock_hz", "reported"), ("measured_clock_hz", "observed")):
            hz = _number(firmware.get(name), integer=True)
            if hz:
                clock_facts.append(
                    {
                        "role": role,
                        "value_hz": hz,
                        "source": {"artifact": metadata_ref, "pointer": f"/firmware/{name}"},
                    }
                )

    latency = summary.latency if summary else None
    for prefix, name, scope in (
        ("profiled", "latency.profiled_invoke.v1", "profiled_invoke"),
        ("clean", "latency.restore_invoke.v1", "restore_and_invoke"),
    ):
        us = _number(getattr(latency, f"device_{prefix}_infer_avg_us", None))
        count = _number(getattr(latency, f"device_{prefix}_infer_count", None), integer=True)
        if count == 0 and not blocked:
            raise ResultReadError("invalid_measurement", "Native inference count must be positive")
        supporting = [f"/latency/device_{prefix}_infer_count"] if count is not None else []
        if prefix == "clean" and getattr(latency, "clean_workload", None) is not None:
            supporting.append("/latency/clean_workload")
        reason = None
        if (
            prefix == "clean"
            and getattr(latency, "clean_workload", None) != "aot_raw_zero_refill_included_v1"
        ):
            reason = "clean_workload_scope_not_recorded"
        metric(
            name,
            f"/latency/device_{prefix}_infer_avg_us",
            _milliseconds(us) if not blocked else None,
            unit="ms",
            scope=scope,
            origin="measured",
            statistic="mean",
            count=count,
            transformation="native microseconds / 1000",
            reason=reason,
            clocks=True,
            supporting_pointers=supporting,
        )

    for section, field, name, scope in (
        ("memory", "arena_size", "memory.arena_capacity.v1", "arena_capacity"),
        ("memory", "allocated_arena", "memory.arena_allocated.v1", "allocator_report"),
        ("binary", "data", "memory.linked_data.v1", "linked_section"),
        ("binary", "bss", "memory.linked_bss.v1", "linked_section"),
    ):
        value = _number(getattr(getattr(summary, section, None), field, None), integer=True)
        metric(
            name,
            f"/{section}/{field}",
            value,
            unit="B",
            scope=scope,
            origin="reported",
            statistic="size",
        )
    return {
        "schema_version": "1.0-draft",
        "producer": {
            "name": "helia-profiler",
            "version": _text(manifest.hpx_version),
            "revision": None,
            "mapping_version": "hpx-profile-1",
        },
        "native_result": {
            "schema": manifest.schema,
            "identity": record.manifest_sha256,
            "artifacts": artifacts,
        },
        "workload": {
            "name": _text(model.get("name")),
            "model_identity": _text(model.get("sha256")),
            "input_identity": None,
            "state_policy": None,
        },
        "engine": {
            "name": summary.engine if summary else _text(engine.get("type")),
            "version": _text(engine.get("version")),
        },
        "target": {"name": _text(platform.get("board")), "identity": None},
        "outcome": {
            "status": "completed" if manifest.status is RunStatus.COMPLETE else "failed",
            "stage": None,
            "reason": "; ".join(i.message for i in manifest.issues if i.message) or None,
        },
        "observations": items,
        "verification": {
            "verifier": "helia-profiler.records/profile-1",
            "checks": [
                "native-publication",
                "artifact-path-size-sha256",
                "unique-core-artifacts",
                "summary-metadata-identity",
            ],
            "limitations": [
                "Not authentication or numerical qualification.",
                "Native reported measurements; raw terminal reconstruction is not performed.",
                "Clock observations are not external calibration.",
                "Per-layer attribution and rail energy are not mapped.",
            ],
        },
        "extensions": {
            "helia-profiler": {
                "native_validity": manifest.validity.value,
                "native_issues": [i.to_dict() for i in manifest.issues],
                "summary_schema_version": summary.schema_version if summary else None,
            }
        },
    }


def export_result(record: ProfileResultRecord, destination: str | Path) -> Path:
    """Write the common JSON view without modifying any native evidence."""
    path = Path(destination)
    payload = json.dumps(observations(record), indent=2, allow_nan=False) + "\n"
    # Refuse overwriting source evidence or another export implicitly.
    with path.open("x", encoding="utf-8") as stream:
        stream.write(payload)
    return path
