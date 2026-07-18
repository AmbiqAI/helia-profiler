"""Run metadata serialisation and ``run_metadata.json`` persistence."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..results import FirmwareMeta, RunMetadata

if TYPE_CHECKING:
    from ..pipeline import PipelineContext

log = logging.getLogger("hpx")


def _firmware_meta_to_dict(meta: FirmwareMeta) -> dict[str, Any]:
    """Convert FirmwareMeta to a JSON-safe dict, dropping None values."""
    if isinstance(meta, FirmwareMeta):
        return {k: v for k, v in asdict(meta).items() if v is not None}
    return {}


def _metadata_to_dict(meta: RunMetadata) -> dict[str, Any]:
    """Convert RunMetadata to a JSON-safe dict."""
    d: dict[str, Any] = {
        "hpx_version": meta.hpx_version,
        "run_id": meta.run_id,
        "timestamp": meta.timestamp,
    }
    if meta.config_snapshot:
        d["config"] = meta.config_snapshot
    if meta.platform is not None:
        d["platform"] = asdict(meta.platform)
    if meta.model is not None:
        d["model"] = asdict(meta.model)
    if meta.toolchain is not None:
        d["toolchain"] = asdict(meta.toolchain)
    if meta.timing is not None:
        d["timing"] = {k: v for k, v in asdict(meta.timing).items() if v is not None}
    return d


def _write_run_metadata(ctx: PipelineContext, output_dir: Path) -> Path:
    """Write run metadata (config, toolchain, model info, etc.) as JSON."""
    out_path = output_dir / "run_metadata.json"

    meta_dict = _metadata_to_dict(ctx.run_metadata)

    # Enrich with firmware-reported values from the capture
    if ctx.pmu_result is not None:
        meta_dict["firmware"] = _firmware_meta_to_dict(ctx.pmu_result.meta)
    if ctx.power_result is not None:
        lifecycle = ctx.power_result.metadata.get("target_lifecycle")
        if lifecycle is not None:
            meta_dict["target_lifecycle"] = lifecycle
    if ctx.power_run is not None and ctx.power_run.terminal is not None:
        meta_dict["power_terminal"] = asdict(ctx.power_run.terminal)
    if ctx.power_run is not None and ctx.power_run.on_device_summary is not None:
        meta_dict["on_device_power"] = asdict(ctx.power_run.on_device_summary)

    out_path.write_text(json.dumps(meta_dict, indent=2, default=str))
    log.info("Wrote run metadata: %s", out_path)
    return out_path
