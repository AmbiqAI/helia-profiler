"""Measure ordered heliaAOT plan candidates on a single selected probe."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from neuralspotx.file_lock import file_mutex

from ._files import read_json, write_bytes_atomically, write_text_atomically
from ._version import __version__
from .config import ProfileConfig
from .engines import EngineType
from .errors import ConfigError, HpxError
from .firmware.workload import AOT_CLEAN_WORKLOAD
from .pipeline import PipelineRunner
from .profiler import build_stages, capture_stages
from .results import ResultValidity
from .results.run_summary import load_run_summary
from .sweep_evidence import admit_measurement
from .target.probe.reservation import reserve_probe


@dataclass(frozen=True)
class SweepRow:
    plan_sha256: str
    status: str
    converted_plan_sha256: str | None = None
    measurement: str | None = None
    error: str | None = None
    cycles: float | None = None
    pool_bytes: int | None = None
    output_identical: bool | None = None
    dma_failures: int | None = None
    energy_uj: float | None = None
    attempt: str | None = None
    clean_window_energy_uj: float | None = None


def _completed(
    digest: str, status: str, converted: str, relative: Path, data: dict[str, Any]
) -> SweepRow:
    return SweepRow(
        digest,
        status,
        converted,
        str(relative),
        cycles=data["cycles"],
        pool_bytes=data["pool_bytes"],
        output_identical=data["output_identical"],
        dma_failures=data["dma_failures"],
        energy_uj=data["energy_uj"],
        clean_window_energy_uj=data.get("clean_window_energy_uj"),
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, record: object) -> None:
    write_text_atomically(path, json.dumps(record, indent=2, allow_nan=False) + "\n")


def _read_cache(entry: Path, root: Path, key: str, digest: str, converted: str) -> SweepRow | None:
    try:
        receipt = read_json(entry.read_bytes(), label="cached measurement")
        saved = (root / receipt["path"]).resolve()
        if receipt["key"] != key or not saved.is_relative_to(root):
            return None
        raw = saved.read_bytes()
        if _sha(raw) != receipt["sha256"]:
            return None
        data = read_json(raw, label="measurement")
        if (data["plan_sha256"], data["converted_plan_sha256"], data["cache_key"]) != (
            digest,
            converted,
            key,
        ):
            return None
        if data["output_identical"] is not True or data["dma_failures"] != 0:
            return None
        return _completed(digest, "cached", converted, saved.relative_to(root), data)
    except (KeyError, OSError, TypeError, ValueError):
        return None


def _measure(
    config: ProfileConfig,
    plan: Path,
    digest: str,
    root: Path,
    attempt: Path,
    references: tuple[str, ...],
    force: bool,
) -> SweepRow:
    args = config.engine.config.get("aot_args") or {}
    if not isinstance(args, dict):
        raise ConfigError("engine.config.aot_args must be a mapping")
    optimization = args.get("optimization") or {}
    if not isinstance(optimization, dict):
        raise ConfigError("heliaAOT optimization settings must be a mapping")
    args = {**args, "optimization": {**optimization, "plan": str(plan)}}
    candidate = replace(
        config,
        engine=replace(config.engine, config={**config.engine.config, "aot_args": args}),
        profiling=replace(config.profiling, sweep_evidence=True),
        work_dir=root / "build" / digest,
        output=replace(config.output, dir=attempt),
    )
    ctx = PipelineRunner(build_stages()).run(candidate)
    if ctx.engine_artifacts is None or ctx.profile_run is None:
        raise ValueError("Candidate build produced no profile firmware")
    (converted,) = [p for p in ctx.engine_artifacts.report_files if p.name.endswith("_plan.json")]
    converted_bytes = converted.read_bytes()
    converted_sha = _sha(converted_bytes)
    conversion = read_json(converted_bytes, label="converted plan")
    model_hash = conversion["model"]["sha256"]
    if model_hash != _sha(config.model.path.read_bytes()):
        raise ValueError("Converted plan and requested model differ")
    image = _sha(ctx.profile_run.firmware.binary_path.read_bytes())
    key = _sha(
        json.dumps(
            [
                __version__,
                model_hash,
                digest,
                converted_sha,
                image,
                ctx.resolved_jlink_serial,
                references,
                config.target.board,
                config.target.psram.clock_hz,
                config.profiling.dcache,
            ]
        ).encode()
    )
    entry = root / "cache" / (key + ".json")
    if force:
        entry.unlink(missing_ok=True)
    elif not config.power.enabled and entry.is_file():
        cached = _read_cache(entry, root, key, digest, converted_sha)
        if cached is not None:
            return cached
    try:
        PipelineRunner(capture_stages()).run_stages(ctx)
        if ctx.run_evaluation is None or ctx.run_evaluation.validity != ResultValidity.VALID:
            raise ValueError("Candidate capture did not produce a usable evaluation")
        measurement = admit_measurement(
            attempt, plan_sha256=digest, reference_outputs=references, cache_regime=None
        )
    except (HpxError, ValueError, OSError) as exc:
        return SweepRow(
            digest, "failed", converted_sha, error=str(exc), attempt=str(attempt.relative_to(root))
        )
    if config.power.enabled:
        power = load_run_summary(attempt / "summary.json").power
        if power is not None and power.clean_workload == AOT_CLEAN_WORKLOAD:
            measurement["clean_window_energy_uj"] = power.energy_per_inference_uj
    measurement["cache_key"] = key
    measurement["converted_plan_sha256"] = converted_sha
    measurement["pool_bytes"] = (conversion["schedule"]["pool"] or {"bytes": 0})["bytes"]
    saved = attempt / "measurement.json"
    _write(saved, measurement)
    relative = str(saved.relative_to(root))
    if measurement["output_identical"] is not True or measurement["dma_failures"] != 0:
        return replace(
            _completed(digest, "failed", converted_sha, saved.relative_to(root), measurement),
            error="Output mismatch or DMA failure",
            attempt=str(attempt.relative_to(root)),
        )
    if not config.power.enabled:
        _write(entry, {"key": key, "path": relative, "sha256": _sha(saved.read_bytes())})
    return _completed(digest, "measured", converted_sha, saved.relative_to(root), measurement)


def sweep(
    config: ProfileConfig,
    plans: list[Path],
    root: Path,
    *,
    reference_outputs: tuple[str, ...],
    remeasure: tuple[str, ...] = (),
) -> list[SweepRow]:
    """Build each candidate and reuse measurements of matching firmware and references."""
    serial = config.target.jlink_serial
    if config.engine.type != EngineType.HELIA_AOT or not serial:
        raise ConfigError("Sweep requires helia-aot and an explicit target.jlink_serial")
    if (
        not plans
        or not reference_outputs
        or not all(re.fullmatch("[0-9a-f]{16}", d) for d in reference_outputs)
    ):
        raise ConfigError(
            "Supply candidate plans and one lowercase FNV1a64 digest per reference output"
        )
    candidates = [(path.read_bytes(), path) for path in plans]
    hashes = [_sha(data) for data, _ in candidates]
    if len(set(hashes)) != len(hashes) or set(remeasure) - set(hashes):
        raise ConfigError(
            "Candidate identities must be unique and contain every requested remeasurement"
        )
    model_sha = _sha(config.model.path.read_bytes())
    invalid: dict[str, str] = {}
    for digest, (data, _) in zip(hashes, candidates, strict=True):
        try:
            content = read_json(data, label="candidate plan")
            if not isinstance(content, dict) or not isinstance(content.get("model"), dict):
                raise ValueError("Missing plan model identity")
            if content["model"].get("sha256") != model_sha:
                raise ValueError("Candidate plan targets a different model")
        except ValueError as exc:
            invalid[digest] = str(exc)
    root = root.resolve()
    session = root / "runs" / uuid4().hex
    rows: list[SweepRow] = []
    with reserve_probe(serial), file_mutex(root / ".sweep.lock"):
        for digest, (data, source) in zip(hashes, candidates, strict=True):
            attempt = session / digest
            plan = root / "plans" / (digest + ".json")
            write_bytes_atomically(plan, data)
            try:
                if digest in invalid:
                    raise ValueError(invalid[digest])
                row = _measure(
                    config, plan, digest, root, attempt, reference_outputs, digest in remeasure
                )
            except (HpxError, ValueError, OSError) as exc:
                row = SweepRow(
                    digest,
                    "failed",
                    error=f"{source}: {exc}",
                    attempt=str(attempt.relative_to(root)),
                )
            rows.append(row)
            _write(session / "sweep.json", [asdict(r) for r in rows])
    return rows
