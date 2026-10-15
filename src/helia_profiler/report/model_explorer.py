"""Model Explorer JSON overlay export.

Generates per-layer profiling overlays compatible with Google's Model Explorer
(https://github.com/google-ai-edge/model-explorer).  Each overlay file can be
loaded alongside the source .tflite model to color-code nodes by cycle count,
instruction count, cache misses, or any other captured PMU metric.

The JSON schema mirrors Model Explorer's ``node_data_builder`` data classes:

    ModelNodeData
      └── graphsData: {graph_id: GraphNodeData}
              ├── results: {node_key: NodeDataResult}
              │                └── value: float
              └── gradient: [{stop, bgColor}, ...]

Model Explorer's LiteRT adapter ids each operator node by its index in the
flatbuffer subgraph, so a layer is keyed by its ORIGINAL tflite operator
index, resolved by :class:`~helia_profiler.modelcost.LayerAttributor` exactly
as the CSV report joins it (#218). A layer with no original index (an
ExecuTorch plan instruction, or an AOT position the manifest does not name)
is left out rather than painted onto an unrelated node.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .._model_explorer_overlay import (
    GRADIENT_COST as GRADIENT_COST,
)
from .._model_explorer_overlay import (
    GradientItem as GradientItem,
)
from .._model_explorer_overlay import (
    GraphNodeData as GraphNodeData,
)
from .._model_explorer_overlay import (
    ModelNodeData as ModelNodeData,
)
from .._model_explorer_overlay import (
    NodeDataResult as NodeDataResult,
)
from .._model_explorer_overlay import (
    Num as Num,
)
from .._model_explorer_overlay import (
    build_multi_metric_overlays as build_multi_metric_overlays,
)
from .._model_explorer_overlay import (
    build_overlay as build_overlay,
)
from ..modelcost.layer_attribution import LayerAttributor

if TYPE_CHECKING:
    from ..results import PmuResult


def _write_model_explorer_overlays(
    pmu: PmuResult,
    me_dir: Path,
    paths: list[Path],
    aot_op_manifest: list[dict[str, Any]] | None = None,
) -> None:
    """Build and save Model Explorer overlay files from PMU data."""
    # A reused output directory must not keep a previous run's overlays: this
    # run may write fewer metrics, or none when no layer is attributable.
    for stale in me_dir.glob("me_overlay_*.json"):
        stale.unlink()
    attributor = LayerAttributor(None, aot_op_manifest)
    metrics: dict[str, dict[str, float]] = {}
    for layer in pmu.layers:
        source = attributor.attribute(layer.id, layer.op, layer.source_index).source_index
        if source is None:
            continue
        for key, val in layer.counters.items():
            metrics.setdefault(key, {})[str(source)] = val

    overlays = build_multi_metric_overlays(metrics)
    for metric_name, overlay in overlays.items():
        out_path = me_dir / f"me_overlay_{metric_name}.json"
        overlay.save(out_path)
        paths.append(out_path)
