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
from .hostenv.cache_dirs import hpx_cache_root
from .pipeline import PipelineRunner
from .profiler import build_stages, capture_stages
from .results import ResultValidity
from .sweep_evidence import admit_measurement


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
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, record: object) -> None:
    write_text_atomically(path, json.dumps(record, indent=2, allow_nan=False) + "\n")


def _measure(
    config: ProfileConfig,
    plan: Path,
    digest: str,
    root: Path,
    attempt: Path,
    references: tuple[str, ...],
    force: bool,
) -> SweepRow:
    args = config.engine.config.get("aot_args", {})
    args = {**args, "optimization": {**args.get("optimization", {}), "plan": str(plan)}}
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
    if not force and not config.power.enabled and entry.is_file():
        try:
            receipt = read_json(entry.read_bytes(), label="cached measurement")
            saved = (root / receipt["path"]).resolve()
            if saved.is_relative_to(root):
                raw = saved.read_bytes()
                if _sha(raw) == receipt["sha256"]:
                    return _completed(
                        digest,
                        "cached",
                        converted_sha,
                        saved.relative_to(root),
                        read_json(raw, label="measurement"),
                    )
        except (KeyError, OSError, TypeError, ValueError):
            pass
    PipelineRunner(capture_stages()).run_stages(ctx)
    if ctx.run_evaluation is None or ctx.run_evaluation.validity != ResultValidity.VALID:
        raise ValueError("Candidate capture did not produce a usable evaluation")
    measurement = admit_measurement(
        attempt, plan_sha256=digest, reference_outputs=references, cache_regime=None
    )
    measurement["converted_plan_sha256"] = converted_sha
    measurement["pool_bytes"] = (conversion["schedule"]["pool"] or {"bytes": 0})["bytes"]
    saved = attempt / "measurement.json"
    _write(saved, measurement)
    relative = str(saved.relative_to(root))
    if measurement["output_identical"] is not True or measurement["dma_failures"] != 0:
        return SweepRow(digest, "failed", converted_sha, relative, "Output mismatch or DMA failure")
    if not config.power.enabled:
        _write(entry, {"path": relative, "sha256": _sha(saved.read_bytes())})
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
    root = root.resolve()
    session = root / "runs" / uuid4().hex
    rows: list[SweepRow] = []
    lock = hpx_cache_root() / "probe-locks" / (_sha(serial.encode()) + ".lock")
    with file_mutex(lock), file_mutex(root / ".sweep.lock"):
        for digest, (data, source) in zip(hashes, candidates, strict=True):
            attempt = session / digest
            plan = root / "plans" / (digest + ".json")
            write_bytes_atomically(plan, data)
            try:
                row = _measure(
                    config, plan, digest, root, attempt, reference_outputs, digest in remeasure
                )
            except (HpxError, ValueError, OSError) as exc:
                row = SweepRow(digest, "failed", error=f"{source}: {exc}")
            rows.append(row)
            _write(session / "sweep.json", [asdict(r) for r in rows])
    return rows
