"""Admit one completed heliaAOT capture as a heliaOptimus schema-1 measurement."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .calibration import read_bundle
from .calibration.table import _conditions, _model_sha256, _plan, _require_usable_cycles
from .errors import HpxError
from .evaluation.compare import load_run_artifacts
from .results.manifest import load_result_manifest
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


def _timed_run(lines: list[str], reference_outputs: tuple[str, ...]) -> _TimedRun:
    match = _EVIDENCE.fullmatch(_field(lines, WireKey.AOT_RUN_EVIDENCE.wire))
    if not match or not 0 < int(match[1]) < 2**32:
        raise ValueError("Missing or invalid timed inference cycles or transfer evidence")
    if int(match[2]):
        raise ValueError(f"DMA provider reported error {match[2]}")
    outputs = []
    for line in lines:
        if line.startswith("HPX_OUTPUT_") and "_FNV1A64=" in line:
            digest = _OUTPUT.fullmatch(line)
            if not digest or int(digest[1]) != len(outputs):
                raise ValueError("Missing, malformed or duplicate timed output digest")
            outputs.append(digest[2])
    if tuple(outputs) != reference_outputs:
        raise ValueError("Timed output digests differ from the reference or are incomplete")
    return _TimedRun(int(match[1]), match[3])


def _timing(
    raw: str, reference_outputs: tuple[str, ...], cache: object
) -> tuple[float, str | None]:
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
            runs.append(_timed_run(lines[start:end], reference_outputs))
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
            runs.append(_timed_run(section[pos:end], reference_outputs))
    if not runs or len(runs) != int(_field(lines, WireKey.PROFILED_INFER_COUNT.wire)):
        raise ValueError("Instrumented inference count disagrees with evidence")
    regimes = {run.preparation for run in runs}
    if cache == "on" and regimes not in ({"warm"}, {"cold"}):
        raise ValueError("Cache preparation unknown or inconsistent across timed runs")
    return sum(run.cycles for run in runs) / len(runs), regimes.pop() if cache == "on" else None


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
        manifest = load_result_manifest(bundle / "result_manifest.json")
        capture = next((a for a in manifest.artifacts if a.path == "firmware_output.txt"), None)
        if capture is None:
            raise ValueError("No manifest-declared instrumented firmware output")
        raw = (bundle / capture.path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != capture.sha256:
            raise ValueError("Firmware output does not match the manifest")
        if manifest.bundle_type == "profile-model-only":
            run = load_run_artifacts(bundle)
            name = str(run.path)
            _require_usable_cycles(run, name)
            conditions = dict(_conditions(run, name))
            model = _plan(run, name).get("model")
            if not isinstance(model, dict) or model.get("sha256") != _model_sha256(run, name):
                raise ValueError("Model-only capture differs from its converted plan")
            per_operator = {}
        else:
            source = read_bundle(bundle)
            conditions = dict(source.conditions)
            per_operator = {sample.operator: sample.cycles for sample in source.samples}
        cycles, regime = _timing(raw.decode(), reference_outputs, conditions["weights_cache"])
    except (HpxError, OSError) as exc:
        raise ValueError(str(exc)) from exc
    if cache_regime is not None and cache_regime != regime:
        raise ValueError(
            f"Observed cache regime {regime!r} differs from requested {cache_regime!r}"
        )
    if regime is not None:
        conditions["cache_regime"] = regime
    return {
        "schema_version": 1,
        "plan_sha256": plan_sha256,
        "conditions": conditions,
        "cycles": cycles,
        "energy_uj": None,
        "output_identical": True,
        "dma_failures": 0,
        "per_operator": per_operator,
    }
