"""Tests for Model Explorer overlay export."""

import json
from pathlib import Path

from helia_profiler.report.model_explorer import (
    GRADIENT_COST,
    ModelNodeData,
    build_multi_metric_overlays,
    build_overlay,
)


def test_build_overlay_basic():
    """Single metric overlay should produce valid ModelNodeData."""
    values = {"conv2d_0:0": 1000, "depthwise_conv2d_1:0": 500, "fc_2:0": 200}
    overlay = build_overlay(values, metric_name="cycles")

    assert isinstance(overlay, ModelNodeData)
    assert "main" in overlay.graphsData
    graph = overlay.graphsData["main"]
    assert graph.name == "cycles"
    assert len(graph.results) == 3
    assert graph.results["conv2d_0:0"].value == 1000
    assert len(graph.gradient) == len(GRADIENT_COST)


def test_overlay_json_roundtrip():
    """JSON output should be parseable and match the Model Explorer schema."""
    values = {"node_0": 42, "node_1": 99}
    overlay = build_overlay(values, metric_name="instructions")

    json_str = overlay.to_json()
    parsed = json.loads(json_str)

    # Top level is graph_id → graph data
    assert "main" in parsed
    graph = parsed["main"]
    assert graph["name"] == "instructions"
    assert graph["results"]["node_0"]["value"] == 42
    assert graph["results"]["node_1"]["value"] == 99
    # Gradient should be present
    assert len(graph["gradient"]) > 0
    assert graph["gradient"][0]["stop"] == 0


def test_overlay_save_to_file(tmp_path: Path):
    """save() should write a valid JSON file."""
    values = {"a": 10, "b": 20}
    overlay = build_overlay(values, metric_name="cache_misses")

    out_path = tmp_path / "overlay.json"
    overlay.save(out_path)

    assert out_path.exists()
    data = json.loads(out_path.read_text())
    assert "main" in data


def test_build_multi_metric_overlays():
    """Multi-metric builder should produce one overlay per metric."""
    metrics = {
        "cycles": {"a": 100, "b": 200},
        "instructions": {"a": 50, "b": 80},
        "cache_misses": {"a": 5, "b": 12},
    }
    overlays = build_multi_metric_overlays(metrics)

    assert len(overlays) == 3
    for name in ("cycles", "instructions", "cache_misses"):
        assert name in overlays
        assert overlays[name].graphsData["main"].name == name


def test_none_values_stripped_from_json():
    """Optional None fields (bgColor, textColor) should not appear in JSON."""
    values = {"x": 1}
    overlay = build_overlay(values, metric_name="test")
    json_str = overlay.to_json()
    parsed = json.loads(json_str)

    # NodeDataResult should only have "value", not "bgColor" or "textColor"
    result = parsed["main"]["results"]["x"]
    assert "value" in result
    assert "bgColor" not in result
    assert "textColor" not in result
