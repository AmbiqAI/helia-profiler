"""Run the transfer probe and publish one calibration under its measured setup."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from .._files import sha256_file, write_text_atomically
from ..calibration.setup import _setup
from ..calibration.table import _require_usable_cycles
from ..config import ProfileConfig
from ..engines import EngineType
from ..engines.base import HeliaAotArtifacts
from ..errors import ConfigError
from ..evaluation.compare import load_run_artifacts
from ..pipeline import PipelineContext, PipelineRunner
from ..platform.catalog import _builtin_catalog
from ..profiler import build_stages, capture_stages
from ..results import ResultValidity
from .matrix import REPETITIONS, SIZES, matrix, schedule_cells
from .record import read_rows


class _RetainProvider:
    """Copy the provider and its plan while the build workspace is still locked."""

    name = "retain_transfer_provider"

    def __init__(self, directory: Path) -> None:
        self.plan = directory / "plan.json"
        self.source = directory / "provider.c"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        assert ctx.profile_run is not None
        app = ctx.profile_run.firmware.app_dir
        companion = ctx.config.profiling.transfer_provider_plan is not None
        artifacts = ctx.prepared_artifacts
        assert isinstance(artifacts, HeliaAotArtifacts)
        base = app / "probe_provider" if companion else app / "modules" / artifacts.aot_module_name
        prefix = "costprobe" if companion else artifacts.aot_prefix
        self.plan.parent.mkdir(parents=True)
        shutil.copyfile(base / f"{prefix}_plan.json", self.plan)
        shutil.copyfile(base / "src" / f"{prefix}_nsx_transfer.c", self.source)


def measure_transfers(
    config: ProfileConfig, output: Path, *, provider_plan: Path | None = None
) -> Path:
    """Measure PSRAM copies on one selected probe and atomically publish schema v2."""
    serial = config.target.jlink_serial
    if (
        not serial
        or config.engine.type != EngineType.HELIA_AOT
        or config.target.transport != "rtt"
        or config.power.enabled
        or config.profiling.dcache != "on"
        or config.target.board not in {"apollo510_evb", "apollo510b_evb"}
    ):
        raise ConfigError(
            "Transfer costs require an explicit Apollo510/510B probe, helia-aot, RTT, D-cache on and power capture disabled"
        )
    bundle = output.parent / (output.name + ".runs") / uuid4().hex
    cfg = replace(
        config,
        profiling=replace(
            config.profiling,
            transfer_costs=True,
            transfer_provider_plan=provider_plan or config.profiling.transfer_provider_plan,
        ),
        output=replace(config.output, dir=bundle),
    )
    evidence = _RetainProvider(bundle.with_suffix(".provider"))
    ctx = PipelineRunner([*build_stages(), evidence, *capture_stages()]).run(cfg)
    geometry = schedule_cells(json.loads(evidence.plan.read_bytes()))
    if ctx.run_evaluation is None or ctx.run_evaluation.validity != ResultValidity.VALID:
        raise ValueError(f"Transfer probe capture did not pass evaluation: {bundle}")
    run = load_run_artifacts(bundle)
    _require_usable_cycles(run, str(bundle))
    setup = _setup(run, str(bundle))
    if setup.pairing_digest is None:
        raise ValueError("Transfer capture lacks a calibration pairing digest")
    curves = matrix()
    rows, probe = read_rows(ctx.captured_pmu.raw_output, curves + geometry)
    held = ctx.run_metadata.conditions
    if (
        held is None
        or held.cpu_hz != probe["cpu_hz"]
        or held.external_memory_clock is None
        or held.external_memory_clock * 1_000_000 != probe["psram_hz"]
    ):
        raise ValueError("Probe clock settings disagree with capture conditions")
    document = {
        "schema_version": 2,
        "sizes": list(SIZES),
        "repetitions": REPETITIONS,
        "conditions": {
            "target": held.target,
            "cpu_hz": held.cpu_hz,
            "external_memory_clock": held.external_memory_clock,
            "application_load": held.application_load,
        },
        "capture": {
            "setup": setup.to_dict(),
            "identity": {
                "board": cfg.target.board,
                "probe": serial,
                "captured_at": ctx.run_metadata.timestamp,
                "hpx_version": ctx.run_metadata.hpx_version,
                "run_id": ctx.run_metadata.run_id,
                "flash_sha256": next(
                    image.flash_sha256
                    for image in ctx.run_metadata.build_images
                    if image.role == "profile"
                ),
            },
            "method": {
                "timer": "DWT_CYCCNT",
                "provider_source_sha256": sha256_file(evidence.source),
                "provider": "heliaAOT nsx-psram",
                "dcache_capacity_kib": _builtin_catalog()
                .resolve(cfg.target.board)
                .core_options.dcache_kb,
                "foreground_code": "default firmware text; runtime address in foreground_address",
                "memory_diagnostic_data": "DTCM; runtime address in foreground_data_address",
                "completion": "final provider ISR callback, core awake",
                "reader": "immediate provider wait",
                "foreground": "identical out-of-line register LCG, alternating baseline before/after DMA",
                "memory_diagnostic": "same loop with volatile reads over 256 DTCM bytes; paired spans in evidence",
                "observer_call_cycles_basis": "direct call to observer body; excludes interrupt entry and driver",
                "source": "PSRAM constant arena",
                "destination": "4096-aligned DTCM",
                "source_cache": "invalidated before cold memcpy; primed by full source read before warm memcpy",
                **probe,
            },
        },
        "transfers": rows[: len(curves)],
        "scheduled_units": [
            row for cell, row in zip(geometry, rows[len(curves) :], strict=True) if not cell.members
        ],
        "coissued_groups": [
            row for cell, row in zip(geometry, rows[len(curves) :], strict=True) if cell.members
        ],
    }
    write_text_atomically(output, json.dumps(document, indent=2, allow_nan=False) + "\n")
    return bundle
