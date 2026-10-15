"""Dependency-free Model Explorer node-data builders shared by report exporters."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Union

from .results.serde import strip_none

Num = Union[float, int]


@dataclass
class GradientItem:
    """A gradient stop mapping a normalized position [0,1] to a color."""

    stop: Num
    bgColor: str | None = None
    textColor: str | None = None


@dataclass
class NodeDataResult:
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
        data = {k: strip_none(asdict(v)) for k, v in self.graphsData.items()}
        return json.dumps(data, indent=indent)

    def save(self, path: Path | str, indent: int | None = 2) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            self.to_json(indent=indent),
            encoding="utf-8",
            newline="\n",
        )


#: Cool-to-hot gradient (green → yellow → red) for cost metrics.
GRADIENT_COST: list[GradientItem] = [
    GradientItem(stop=0, bgColor="#22c55e"),
    GradientItem(stop=0.5, bgColor="#eab308"),
    GradientItem(stop=1, bgColor="#ef4444"),
]


def build_overlay(
    layer_values: dict[str, Num],
    *,
    metric_name: str = "cycles",
    graph_id: str = "main",
    gradient: list[GradientItem] | None = None,
) -> ModelNodeData:
    """Build a Model Explorer overlay from a flat {node_key: value} dict.

    Args:
        layer_values: Mapping of node key (output tensor name or node id) to a
            numeric profiling value.
        metric_name: Human-readable name shown in Model Explorer's overlay
            selector.
        graph_id: TFLite graph identifier.  ``"main"`` is the default for
            single-subgraph models.
        gradient: Color gradient for the overlay.  Defaults to
            ``GRADIENT_COST``.
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
