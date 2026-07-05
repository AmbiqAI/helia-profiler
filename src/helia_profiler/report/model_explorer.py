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

Node keys are matched to TFLite graph nodes by either:
  - output tensor name  (preferred — stable across builds), or
  - node id             (fallback — index-based, fragile).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from ..pipeline import PipelineContext

Num = Union[float, int]


# ---------------------------------------------------------------------------
# Data classes (mirror Model Explorer's node_data_builder.py)
# ---------------------------------------------------------------------------


@dataclass
class GradientItem:
    """A gradient stop mapping a normalized position [0,1] to a color."""

    stop: Num
    bgColor: str | None = None
    textColor: str | None = None


@dataclass
class NodeDataResult:
    """A single per-node value."""

    value: Num
    bgColor: str | None = None
    textColor: str | None = None


@dataclass
class GraphNodeData:
    """Per-node results for one graph, plus coloring rules."""

    results: dict[str, NodeDataResult]
    gradient: list[GradientItem] = field(default_factory=list)
    name: str | None = None


@dataclass
class ModelNodeData:
    """Top-level container: one or more graphs worth of node data."""

    graphsData: dict[str, GraphNodeData]

    def to_json(self, indent: int | None = 2) -> str:
        """Serialize to the JSON format Model Explorer expects."""
        data = {k: _strip_none(asdict(v)) for k, v in self.graphsData.items()}
        return json.dumps(data, indent=indent)

    def save(self, path: Path | str, indent: int | None = 2) -> None:
        """Write JSON overlay file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(indent=indent))


# ---------------------------------------------------------------------------
# Pre-built gradient palettes for common profiling metrics
# ---------------------------------------------------------------------------

#: Cool-to-hot gradient (green → yellow → red) for cost metrics.
GRADIENT_COST: list[GradientItem] = [
    GradientItem(stop=0, bgColor="#22c55e"),  # green-500
    GradientItem(stop=0.5, bgColor="#eab308"),  # yellow-500
    GradientItem(stop=1, bgColor="#ef4444"),  # red-500
]

#: Inverted gradient (red → green) for efficiency metrics.
GRADIENT_EFFICIENCY: list[GradientItem] = [
    GradientItem(stop=0, bgColor="#ef4444"),  # red-500
    GradientItem(stop=0.5, bgColor="#eab308"),  # yellow-500
    GradientItem(stop=1, bgColor="#22c55e"),  # green-500
]


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------


def build_overlay(
    layer_values: dict[str, Num],
    *,
    metric_name: str = "cycles",
    graph_id: str = "main",
    gradient: list[GradientItem] | None = None,
) -> ModelNodeData:
    """Build a Model Explorer overlay from a flat {node_key: value} dict.

    Parameters
    ----------
    layer_values:
        Mapping of node key (output tensor name or node id) to a numeric
        profiling value.
    metric_name:
        Human-readable name shown in Model Explorer's overlay selector.
    graph_id:
        TFLite graph identifier.  ``"main"`` is the default for single-
        subgraph models.
    gradient:
        Color gradient for the overlay.  Defaults to ``GRADIENT_COST``.
    """
    if gradient is None:
        gradient = list(GRADIENT_COST)

    results = {key: NodeDataResult(value=val) for key, val in layer_values.items()}

    graph_data = GraphNodeData(
        results=results,
        gradient=gradient,
        name=metric_name,
    )

    return ModelNodeData(graphsData={graph_id: graph_data})


def build_multi_metric_overlays(
    metrics: dict[str, dict[str, Num]],
    *,
    graph_id: str = "main",
    gradient: list[GradientItem] | None = None,
) -> dict[str, ModelNodeData]:
    """Build one overlay per metric from a dict of {metric_name: {node_key: value}}.

    Returns a dict keyed by metric name, each value a ``ModelNodeData`` ready
    for ``save()``.
    """
    overlays: dict[str, ModelNodeData] = {}
    for metric_name, layer_values in metrics.items():
        overlays[metric_name] = build_overlay(
            layer_values,
            metric_name=metric_name,
            graph_id=graph_id,
            gradient=gradient,
        )
    return overlays


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _strip_none(d: dict) -> dict:
    """Recursively remove None values from a dict (mirrors ME's remove_none)."""
    cleaned: dict = {}
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, dict):
            cleaned[k] = _strip_none(v)
        elif isinstance(v, list):
            cleaned[k] = [_strip_none(i) if isinstance(i, dict) else i for i in v]
        else:
            cleaned[k] = v
    return cleaned


# ---------------------------------------------------------------------------
# Report-stage entry point — builds and saves overlays into model_explorer/
# ---------------------------------------------------------------------------


def _write_model_explorer_overlays(
    ctx: PipelineContext,
    me_dir: Path,
    paths: list[Path],
) -> None:
    """Build and save Model Explorer overlay files from PMU data."""
    assert ctx.pmu_result is not None
    layers = ctx.pmu_result.layers
    if not layers:
        return

    # Extract per-metric node_key→value dicts from layer data.
    #
    # Model Explorer matches nodes by ID (integer string).  For TFLite
    # models the node ID is the sequential operator index in the graph.
    #
    # AOT firmware emits "TYPE:id" in the Op column (e.g. "CONV_2D:3")
    # where `id` is the original TFLite operator index preserved through
    # AOT transforms.  We extract that suffix as the node key.
    #
    # TFLM firmware emits just the type string (e.g. "CONV_2D").  Since
    # multiple layers can share the same type, we fall back to the
    # sequential layer index, which matches TFLite graph operator order.
    metrics: dict[str, dict[str, float]] = {}
    for layer in layers:
        op_str = str(layer.op) if layer.op else ""
        if ":" in op_str:
            # AOT format — "CONV_2D:3" → use "3" as node key
            node_key = op_str.rsplit(":", 1)[1]
        else:
            # TFLM / generic — use sequential layer index
            node_key = str(layer.id)
        for key, val in layer.counters.items():
            metrics.setdefault(key, {})[node_key] = val

    if not metrics:
        return

    overlays = build_multi_metric_overlays(metrics)
    for metric_name, overlay in overlays.items():
        out_path = me_dir / f"me_overlay_{metric_name}.json"
        overlay.save(out_path)
        paths.append(out_path)
