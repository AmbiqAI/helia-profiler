"""Report generation — CSV, JSON, terminal summary, and Model Explorer overlays.

The ``write_report`` function is called by the report stage and dispatches to
the appropriate formatters based on ``OutputConfig``. Each writer lives in its
own module; this file only orchestrates the pipeline and re-exports the
private helpers that existing tests import directly.

Output structure
----------------

Always generated (root of output_dir):
    summary.json          High-level machine-readable summary (cycles, memory,
                          cache highlights, binary sections, top layers).
    profile_results.csv   Merged per-layer profiling results.
    run_metadata.json     Full run metadata (config, toolchain, platform, …).

Model Explorer (``model_explorer/`` subfolder, unless ``--no-model-explorer``):
    me_overlay_<COUNTER>.json   Per-counter overlay files.

Detailed (``detailed/`` subfolder, only with ``--detailed``):
    profile_<preset>.csv        Per-preset CSV breakdowns.
    profile_<group>.csv         Per-group (compute-unit) merged CSVs.
    memory.json                 Memory breakdown (binary sections, arena,
                                per-layer cache counters).
    power_summary.csv           Power capture summary (when available).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import ReportError
from .aot import _write_aot_manifest, _write_aot_memory_layers
from .csv_writer import _layer_to_flat_dict, _write_csv, _write_preset_csv
from .json_writer import _write_json
from .memory import _serialise_memory_plan, _write_memory_breakdown
from .metadata import _firmware_meta_to_dict, _metadata_to_dict, _write_run_metadata
from .model_explorer import _write_model_explorer_overlays
from .power import _power_summary_to_dict, _write_power_csv
from .summary import _write_summary

if TYPE_CHECKING:
    from ..pipeline import PipelineContext

log = logging.getLogger("hpx")

__all__ = ["write_report"]


def write_report(ctx: PipelineContext) -> list[Path]:
    """Generate all configured report outputs.

    Returns a list of paths to the files written.
    """
    assert ctx.pmu_result is not None

    output_dir = ctx.config.output.dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    fmt = ctx.config.output.format
    pmu = ctx.pmu_result
    detailed = ctx.config.output.detailed
    analysis = ctx.model_analysis

    # --- Always: primary profile results ---
    if fmt == "csv":
        p = _write_csv(pmu, output_dir, analysis)
        paths.append(p)
    elif fmt == "json":
        p = _write_json(pmu, ctx.power_result, ctx.run_metadata, output_dir)
        paths.append(p)
    else:
        raise ReportError(f"Unknown output format: '{fmt}'")

    # --- Always: summary.json ---
    p = _write_summary(ctx, output_dir)
    paths.append(p)

    # --- Always: run metadata ---
    p = _write_run_metadata(ctx, output_dir)
    paths.append(p)

    # --- heliaAOT operator manifest (engine-specific) ---
    p = _write_aot_manifest(ctx, output_dir)
    if p is not None:
        paths.append(p)
        p = _write_aot_memory_layers(ctx, output_dir)
        if p is not None:
            paths.append(p)

    # --- Model Explorer overlays → model_explorer/ subfolder ---
    if ctx.config.output.model_explorer:
        try:
            me_dir = output_dir / "model_explorer"
            me_dir.mkdir(parents=True, exist_ok=True)
            _write_model_explorer_overlays(ctx, me_dir, paths)
        except Exception as exc:
            raise ReportError(
                f"Model Explorer overlay generation failed: {exc}",
            ) from exc

    # --- Detailed outputs → detailed/ subfolder ---
    if detailed:
        detail_dir = output_dir / "detailed"
        detail_dir.mkdir(parents=True, exist_ok=True)

        # Per-preset CSV breakdowns
        if len(pmu.presets) > 1:
            for preset_name, pr in pmu.presets.items():
                if preset_name.startswith("_"):
                    continue
                p = _write_preset_csv(preset_name, pr.layers, detail_dir)
                paths.append(p)

        # Per-group (compute-unit) unified CSVs
        if pmu.groups:
            for group_name, group_layers in pmu.groups.items():
                p = _write_preset_csv(group_name, group_layers, detail_dir)
                paths.append(p)

        # Memory breakdown JSON
        p = _write_memory_breakdown(ctx, detail_dir)
        paths.append(p)

        # Power summary CSV
        if ctx.power_result is not None:
            p = _write_power_csv(ctx.power_result, detail_dir)
            paths.append(p)

    return paths
