"""Admit one completed heliaAOT capture as a heliaOptimus schema-1 measurement."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .calibration.table import (
    _conditions,
    _is_subgraph,
    _model_sha256,
    _plan,
    _read_bundle,
    _require_usable_cycles,
)
from .errors import HpxError
from .evaluation.compare import load_run_artifacts
from .results import RunStatus
from .wire import (
    HPX_END_SENTINEL,
    HPX_ERROR_PREFIX,
    HPX_ITER_SENTINEL_RE,
    HPX_PRESET_SENTINEL_RE,
    HPX_START_SENTINEL,
    WireKey,
)

_DIGEST = re.compile(r"[0-9a-f]{16}")
_OUTPUT = re.compile(r"HPX_OUTPUT_(\d+)_FNV1A64=([0-9a-f]{16})")
_EVIDENCE = re.compile(r"(\d+),(\d+),(cold|warm|unknown)")


def _field(lines: Sequence[str], name: str) -> str:
    values = [line.removeprefix(name + "=") for line in lines if line.startswith(name + "=")]
    if len(values) != 1:
        raise ValueError(f"Expected one {name} field, observed {len(values)}")
    return values[0]


@dataclass(frozen=True)
class _TimedRun:
    cycles: int
    preparation: str
    output_identical: bool


@dataclass(frozen=True)
class _Timing:
    cycles: float
    regime: str | None
    output_identical: bool


def _timed_run(lines: list[str], reference_outputs: tuple[str, ...], sample: str) -> _TimedRun:
    match = _EVIDENCE.fullmatch(_field(lines, WireKey.AOT_RUN_EVIDENCE.wire))
    if not match or not 0 < int(match[1]) < 2**32:
        raise ValueError("Missing or invalid timed inference cycles or transfer evidence")
    if int(match[2]):
        raise ValueError(
            f"{sample}: DMA provider last-error status {match[2]}; transfer failure count unavailable"
        )
    outputs = []
    for line in lines:
        if line.startswith("HPX_OUTPUT_") and "_FNV1A64=" in line:
            digest = _OUTPUT.fullmatch(line)
            if not digest or int(digest[1]) != len(outputs):
                raise ValueError("Missing, malformed or duplicate timed output digest")
            outputs.append(digest[2])
    if len(outputs) != len(reference_outputs):
        raise ValueError(f"{sample}: incomplete timed output digests")
    return _TimedRun(int(match[1]), match[3], tuple(outputs) == reference_outputs)


def _timing(raw: str, reference_outputs: tuple[str, ...], cache: object) -> _Timing:
    lines = [line.strip() for line in raw.splitlines()]
    if lines.count(HPX_START_SENTINEL) != 1 or lines.count(HPX_END_SENTINEL) != 1:
        raise ValueError("Expected a single completed capture")
    lines = lines[lines.index(HPX_START_SENTINEL) + 1 : lines.index(HPX_END_SENTINEL)]
    if _field(lines, WireKey.ENGINE.wire) != "helia_aot" or any(
        s.startswith(HPX_ERROR_PREFIX) for s in lines
    ):
        raise ValueError("Expected a successful heliaAOT capture")
    if _field(lines, WireKey.DCACHE.wire) != cache or cache not in ("on", "off"):
        raise ValueError("Observed data cache policy disagrees with conditions")
    if int(_field(lines, WireKey.NUM_OUTPUTS.wire)) != len(reference_outputs):
        raise ValueError("Supply one reference digest for every output")
    preset_text = _field(lines, WireKey.PRESETS.wire)
    presets = preset_text.split(",") if preset_text else []
    iterations = int(_field(lines, WireKey.ITERATIONS.wire))
    if len(presets) != int(_field(lines, WireKey.NUM_PRESETS.wire)) or iterations < 1:
        raise ValueError("Invalid announced pass or iteration counts")
    runs: list[_TimedRun] = []
    starts = [i for i, line in enumerate(lines) if HPX_PRESET_SENTINEL_RE.fullmatch(line)]
    if len(starts) != len(presets):
        raise ValueError("Incomplete timed passes")
    if not presets:
        if _field(lines, WireKey.PER_LAYER_PROFILING.wire) != "0":
            raise ValueError("No timed passes in an instrumented capture")
        boundaries = [
            i
            for i, line in enumerate(lines)
            if line.startswith(WireKey.AOT_RUN_EVIDENCE.wire + "=")
        ]
        if len(boundaries) != iterations:
            raise ValueError("Incomplete model-only timed samples")
        for start, end in zip(boundaries, [*boundaries[1:], len(lines)], strict=True):
            runs.append(
                _timed_run(lines[start:end], reference_outputs, f"Model-only sample {len(runs)}")
            )
    for idx, start in enumerate(starts):
        if lines[start] != f"--- HPX_PRESET {presets[idx]} ---":
            raise ValueError("Timed pass order disagrees with announced presets")
        section = lines[start : starts[idx + 1] if idx + 1 < len(starts) else len(lines)]
        frames = [i for i, line in enumerate(section) if HPX_ITER_SENTINEL_RE.fullmatch(line)]
        if len(frames) != iterations:
            raise ValueError(f"Incomplete timed iterations in {presets[idx]}")
        for iteration, pos in enumerate(frames):
            if section[pos] != f"--- HPX_ITER {iteration} ---":
                raise ValueError("Missing or duplicate timed iteration")
            end = frames[iteration + 1] if iteration + 1 < len(frames) else len(section)
            runs.append(
                _timed_run(
                    section[pos:end], reference_outputs, f"{presets[idx]} sample {iteration}"
                )
            )
    if not runs or len(runs) != int(_field(lines, WireKey.PROFILED_INFER_COUNT.wire)):
        raise ValueError("Instrumented inference count disagrees with evidence")
    regimes = {run.preparation for run in runs}
    if cache == "on" and regimes not in ({"warm"}, {"cold"}):
        raise ValueError("Cache preparation unknown or inconsistent across timed runs")
    return _Timing(
        sum(run.cycles for run in runs) / len(runs),
        regimes.pop() if cache == "on" else None,
        all(run.output_identical for run in runs),
    )


def _converted_weights_memory(plan: dict[str, Any]) -> str:
    """Resolve the one weights memory represented by the compiled constant storage roots."""
    tensors = plan.get("tensors")
    if not isinstance(tensors, list) or not tensors:
        raise ValueError("Converted plan has no constant placement evidence for weights_memory")
    memories = set()
    for tensor in tensors:
        if not isinstance(tensor, dict) or tensor.get("residency") not in (
            "direct",
            "stream",
            "pin",
        ):
            raise ValueError("Converted plan has an unknown constant residency")
        # A pinned constant is read from its destination after model initialization.
        field = "destination_memory" if tensor["residency"] == "pin" else "memory"
        memory = tensor.get(field)
        if not isinstance(memory, str) or memory.lower() not in {
            "dtcm",
            "itcm",
            "sram",
            "mram",
            "psram",
            "nor",
        }:
            raise ValueError(f"Converted plan has an unknown constant {field}: {memory!r}")
        memories.add(memory.lower())
    if len(memories) != 1:
        raise ValueError(
            f"Converted plan has mixed weights memories: {', '.join(sorted(memories))}"
        )
    return memories.pop()


def admit_measurement(
    bundle: Path,
    *,
    plan_sha256: str,
    reference_outputs: tuple[str, ...],
    cache_regime: str | None = None,
) -> dict[str, object]:
    """Validate the instrumented runs and join their cycles to the original plan identity."""
    if not re.fullmatch(r"[0-9a-f]{64}", plan_sha256):
        raise ValueError("Expected SHA-256 of the original plan file")
    if not reference_outputs or not all(_DIGEST.fullmatch(value) for value in reference_outputs):
        raise ValueError("Expected a lowercase 16-digit FNV-1a digest for every output")
    try:
        run = load_run_artifacts(bundle)
        manifest = run.manifest
        if manifest is None or manifest.status != RunStatus.COMPLETE:
            raise ValueError("Sweep measurement requires a completed capture")
        name = str(run.path)
        plan = _plan(run, name)
        recorded = run.summary.get("conditions")
        memory = None
        if isinstance(recorded, dict) and recorded.get("weights_memory") is None:
            memory = _converted_weights_memory(plan)
        if manifest.bundle_type == "profile-model-only":
            _require_usable_cycles(run, name)
            conditions = dict(_conditions(run, name, converted_memory=memory))
            model = plan.get("model")
            if (
                not isinstance(model, dict)
                or model.get("sha256") != _model_sha256(run, name)
                or not _is_subgraph(model.get("subgraph"))
            ):
                raise ValueError(
                    "Model-only capture differs from its converted plan or lacks a subgraph"
                )
            per_operator = {}
        else:
            source = _read_bundle(run, plan, converted_memory=memory)
            conditions = dict(source.conditions)
            per_operator = {sample.operator: sample.cycles for sample in source.samples}
        capture = next((a for a in manifest.artifacts if a.path == "firmware_output.txt"), None)
        if capture is None:
            raise ValueError("No manifest-declared instrumented firmware output")
        raw = (bundle / capture.path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != capture.sha256:
            raise ValueError("Firmware output does not match the manifest")
        timing = _timing(raw.decode(), reference_outputs, conditions["weights_cache"])
    except (HpxError, OSError) as exc:
        raise ValueError(str(exc)) from exc
    if cache_regime is not None and cache_regime != timing.regime:
        raise ValueError(
            f"Observed cache regime {timing.regime!r} differs from requested {cache_regime!r}"
        )
    if timing.regime is not None:
        conditions["cache_regime"] = timing.regime
    return {
        "schema_version": 1,
        "plan_sha256": plan_sha256,
        "conditions": conditions,
        "cycles": timing.cycles,
        "energy_uj": None,
        "output_identical": timing.output_identical,
        "dma_failures": 0,
        "per_operator": per_operator,
    }
