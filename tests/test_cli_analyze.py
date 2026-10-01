"""Tests for ``hpx analyze`` output, driven through the Typer app."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import helia_profiler.evaluation as evaluation
from helia_profiler.cli.app import app
from helia_profiler.console import HpxConsole
from helia_profiler.errors import ConfigError
from helia_profiler.modelcost import LayerOps, ModelAnalysis

runner = CliRunner()

ORIGINAL = ModelAnalysis(
    layers=[
        LayerOps(id=0, op="CONV_2D", macs=10, ops=20, params={"stride_h": 1}),
        LayerOps(id=1, op="DEPTHWISE_CONV_2D", macs=5, ops=10, params={"depth_multiplier": 1}),
        LayerOps(id=2, op="SOFTMAX", ops=3),
    ],
    total_macs=15,
    total_ops=33,
    num_parameters=7,
)
TRANSFORMED = ModelAnalysis(
    layers=[LayerOps(id=0, op="AOT_FUSED", macs=15, ops=30, original_id=0)],
    total_macs=15,
    total_ops=30,
    num_parameters=7,
    engine="helia-aot",
)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    def fake_analyze_for_engine(model_path, *, engine, board):
        seen.append(engine)
        return TRANSFORMED if engine == "helia-aot" else ORIGINAL

    monkeypatch.setattr(evaluation, "analyze_for_engine", fake_analyze_for_engine)
    return seen


def _analyze(*args: str):
    return runner.invoke(app, ["analyze", "model.tflite", *args])


def test_json_compare_labels_each_graph_correctly(tmp_path: Path, calls: list[str]) -> None:
    dest = tmp_path / "analysis.json"

    result = _analyze("--engine", "helia-aot", "--compare", "--format", "json", "-o", str(dest))

    assert result.exit_code == 0, result.output
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert data == {
        "original": ORIGINAL.to_dict(),
        "aot_transformed": TRANSFORMED.to_dict(),
    }
    assert calls == ["helia-aot", "tflm"]


def test_json_aot_without_compare_writes_only_transformed_graph(
    tmp_path: Path, calls: list[str]
) -> None:
    dest = tmp_path / "analysis.json"

    result = _analyze("--engine", "helia-aot", "--format", "json", "-o", str(dest))

    assert result.exit_code == 0, result.output
    assert json.loads(dest.read_text(encoding="utf-8")) == {
        "aot_transformed": TRANSFORMED.to_dict()
    }
    assert calls == ["helia-aot"]


def test_json_without_engine_writes_original_graph(tmp_path: Path, calls: list[str]) -> None:
    dest = tmp_path / "analysis.json"

    result = _analyze("--format", "json", "-o", str(dest))

    assert result.exit_code == 0, result.output
    assert json.loads(dest.read_text(encoding="utf-8")) == {"original": ORIGINAL.to_dict()}
    assert calls == ["tflm"]


@pytest.mark.parametrize("engine_args", [[], ["--engine", "helia-rt"], ["--engine", "tflm"]])
def test_compare_without_aot_analyzes_only_the_tflite_graph(
    tmp_path: Path, calls: list[str], engine_args: list[str]
) -> None:
    dest = tmp_path / "analysis.json"

    result = _analyze(*engine_args, "--compare", "--format", "json", "-o", str(dest))

    assert result.exit_code == 0, result.output
    assert json.loads(dest.read_text(encoding="utf-8")) == {"original": ORIGINAL.to_dict()}
    assert calls == ["tflm"]


def test_csv_includes_params_from_every_layer(tmp_path: Path, calls: list[str]) -> None:
    dest = tmp_path / "analysis.csv"

    result = _analyze("--format", "csv", "-o", str(dest))

    assert result.exit_code == 0, result.output
    with dest.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
    assert reader.fieldnames is not None
    assert reader.fieldnames[-2:] == ["stride_h", "depth_multiplier"]
    assert [row["op"] for row in rows] == ["CONV_2D", "DEPTHWISE_CONV_2D", "SOFTMAX"]
    assert rows[1]["depth_multiplier"] == "1"
    assert rows[2]["stride_h"] == ""
    assert calls == ["tflm"]


def test_csv_rejects_compare(tmp_path: Path, calls: list[str]) -> None:
    dest = tmp_path / "analysis.csv"

    result = _analyze("--engine", "helia-aot", "--compare", "--format", "csv", "-o", str(dest))

    assert result.exit_code == 1
    assert "--compare is not supported with --format csv" in result.output
    assert not dest.exists()
    assert calls == []


def test_table_compare_renders_transformed_against_original(
    monkeypatch: pytest.MonkeyPatch, calls: list[str]
) -> None:
    rendered: list[tuple] = []
    monkeypatch.setattr(
        HpxConsole,
        "print_analysis",
        lambda self, primary, name, reference=None: rendered.append((primary, name, reference)),
    )

    result = _analyze("--engine", "helia-aot", "--compare")

    assert result.exit_code == 0, result.output
    assert rendered == [(TRANSFORMED, "model.tflite", ORIGINAL)]


def test_analysis_errors_print_hint_and_exit_one(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(model_path, *, engine, board):
        raise ConfigError("helia-aot is not installed.", hint="Install the aot extra")

    monkeypatch.setattr(evaluation, "analyze_for_engine", fail)

    result = _analyze("--engine", "helia-aot")

    assert result.exit_code == 1
    assert "helia-aot is not installed." in result.output
    assert "Install the aot extra" in result.output
