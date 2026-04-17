"""Report generation — CSV, JSON, terminal summary, and Model Explorer overlays.

The ``write_report`` function is called by the report stage and dispatches to
the appropriate formatters based on ``OutputConfig``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import ReportError

if TYPE_CHECKING:
    from ..pipeline import PipelineContext


def write_report(ctx: PipelineContext) -> list[Path]:
    """Generate all configured report outputs.

    Returns a list of paths to the files written.
    """
    assert ctx.pmu_raw is not None

    output_dir = ctx.config.output.dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    fmt = ctx.config.output.format

    # Primary format
    if fmt == "csv":
        # TODO: Write CSV from ctx.pmu_raw to output_dir / "profile_results.csv"
        raise ReportError(
            "CSV report generation not yet implemented.",
            hint="This feature is under development.",
        )
    elif fmt == "json":
        # TODO: Write JSON from ctx.pmu_raw to output_dir / "profile_results.json"
        raise ReportError(
            "JSON report generation not yet implemented.",
            hint="This feature is under development.",
        )
    else:
        raise ReportError(f"Unknown output format: '{fmt}'")

    # Model Explorer overlays (emitted alongside primary format)
    if ctx.config.output.model_explorer:
        try:
            _write_model_explorer_overlays(ctx, output_dir, paths)
        except Exception as exc:
            raise ReportError(
                f"Model Explorer overlay generation failed: {exc}",
            ) from exc

    return paths


def _write_model_explorer_overlays(
    ctx: PipelineContext,
    output_dir: Path,
    paths: list[Path],
) -> None:
    """Build and save Model Explorer overlay files from PMU data."""
    from .model_explorer import build_multi_metric_overlays

    assert ctx.pmu_raw is not None
    layers = ctx.pmu_raw.get("layers", [])
    if not layers:
        return

    # Extract per-metric node_key→value dicts from layer data
    # Each layer dict is expected to have a "name" key and metric keys
    metrics: dict[str, dict[str, float]] = {}
    for layer in layers:
        node_key = layer.get("name", layer.get("id", "unknown"))
        for key, val in layer.items():
            if key in ("name", "id"):
                continue
            if isinstance(val, (int, float)):
                metrics.setdefault(key, {})[str(node_key)] = val

    if not metrics:
        return

    overlays = build_multi_metric_overlays(metrics)
    for metric_name, overlay in overlays.items():
        out_path = output_dir / f"me_overlay_{metric_name}.json"
        overlay.save(out_path)
        paths.append(out_path)
