import json
from pathlib import Path
from typing import Any

import pytest

from helia_profiler.report.model_explorer import (
    GRADIENT_COST,
    ModelNodeData,
    _write_model_explorer_overlays,
    build_multi_metric_overlays,
    build_overlay,
)
from helia_profiler.results import FirmwareMeta, LayerResult, PmuResult


def test_build_overlay_basic():
    values: dict[str, float] = {"conv2d_0:0": 1000, "depthwise_conv2d_1:0": 500, "fc_2:0": 200}
    overlay = build_overlay(values, metric_name="cycles")

    assert isinstance(overlay, ModelNodeData)
    assert "main" in overlay.graphsData
    graph = overlay.graphsData["main"]
    assert graph.name == "cycles"
    assert len(graph.results) == 3
    assert graph.results["conv2d_0:0"].value == 1000
    assert len(graph.gradient) == len(GRADIENT_COST)


def test_overlay_json_roundtrip():
    values: dict[str, float] = {"node_0": 42, "node_1": 99}
    overlay = build_overlay(values, metric_name="instructions")

    json_str = overlay.to_json()
    parsed = json.loads(json_str)

    assert "main" in parsed
    graph = parsed["main"]
    assert graph["name"] == "instructions"
    assert graph["results"]["node_0"]["value"] == 42
    assert graph["results"]["node_1"]["value"] == 99
    assert len(graph["gradient"]) > 0
    assert graph["gradient"][0]["stop"] == 0


def test_overlay_save_to_file(tmp_path: Path):
    values: dict[str, float] = {"a": 10, "b": 20}
    overlay = build_overlay(values, metric_name="cache_misses")

    out_path = tmp_path / "overlay.json"
    overlay.save(out_path)

    assert out_path.exists()
    data = json.loads(out_path.read_text())
    assert "main" in data


def test_build_multi_metric_overlays():
    metrics: dict[str, dict[str, float]] = {
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
    values: dict[str, float] = {"x": 1}
    overlay = build_overlay(values, metric_name="test")
    json_str = overlay.to_json()
    parsed = json.loads(json_str)

    result = parsed["main"]["results"]["x"]
    assert "value" in result
    assert "bgColor" not in result
    assert "textColor" not in result


def _overlay_values(
    tmp_path: Path,
    layers: list[LayerResult],
    aot_op_manifest: list[dict[str, Any]] | None = None,
) -> dict[str, float]:
    paths: list[Path] = []
    _write_model_explorer_overlays(
        PmuResult(meta=FirmwareMeta(), layers=layers), tmp_path, paths, aot_op_manifest
    )
    if not paths:
        return {}
    (path,) = paths
    results = json.loads(path.read_text())["main"]["results"]
    return {key: result["value"] for key, result in results.items()}


def _layer(layer_id: int, op: str, cycles: float, **kwargs: Any) -> LayerResult:
    return LayerResult(id=layer_id, op=op, counters={"ARM_PMU_CPU_CYCLES": cycles}, **kwargs)


# Model Explorer's LiteRT adapter ids operator nodes by flatbuffer operator
# index, so every expected key is an ORIGINAL tflite operator index.
@pytest.mark.parametrize(
    ("layers", "manifest", "expected"),
    [
        pytest.param(
            [_layer(0, "CONV_2D", 10), _layer(1, "CONV_2D", 20)],
            None,
            {"0": 10, "1": 20},
            id="tflm-plain-labels-use-execution-order",
        ),
        pytest.param(
            [_layer(0, "DEPTHWISE_CONV_2D", 10), _layer(1, "FULLY_CONNECTED", 20)],
            None,
            {"0": 10, "1": 20},
            id="helia-rt-plain-labels-use-execution-order",
        ),
        pytest.param(
            [_layer(0, "CONV_2D:2", 10), _layer(1, "FULLY_CONNECTED:43", 20)],
            None,
            {"2": 10, "43": 20},
            id="helia-aot-label-suffix-without-manifest",
        ),
        pytest.param(
            [_layer(0, "CONV_2D:0", 10), _layer(1, "FULLY_CONNECTED:1", 20)],
            [{"idx": 0, "id": 2}, {"idx": 1, "id": 43}],
            {"2": 10, "43": 20},
            id="helia-aot-manifest-outranks-positional-suffix",
        ),
        pytest.param(
            [_layer(0, "CONV_2D:0", 10)],
            [],
            {},
            id="helia-aot-failed-manifest-attributes-nothing",
        ),
        pytest.param(
            [_layer(0, "aten::add.out:c3i12", 10), _layer(1, "OPERATOR_CALL:c3i13", 20)],
            None,
            {},
            id="executorch-plan-instructions-name-no-graph-node",
        ),
        pytest.param(
            [_layer(0, "CONV_2D", 10, source_index=5)],
            None,
            {"5": 10},
            id="carried-source-index-outranks-execution-order",
        ),
    ],
)
def test_overlay_keys_are_original_tflite_operator_indices(
    tmp_path: Path,
    layers: list[LayerResult],
    manifest: list[dict[str, Any]] | None,
    expected: dict[str, float],
):
    assert _overlay_values(tmp_path, layers, manifest) == expected
