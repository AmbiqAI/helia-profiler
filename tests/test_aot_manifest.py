"""Tests for heliaAOT operator manifest extraction and persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from helia_profiler.engines.helia_aot import (
    _extract_operator_manifest,
    _tensor_metadata,
)
from helia_profiler.report import _write_aot_manifest


# ---------- fake AotOperator surface -----------------------------------------


@dataclass
class _FakeTensor:
    name: str = "t"
    shape: list[int] = field(default_factory=lambda: [1, 8, 8, 3])
    dtype: str = "int8"
    ctype: str = "int8_t"
    nbytes: int = 192
    size: int = 192
    ndim: int = 4
    is_constant: bool = False
    is_persistent: bool = False
    is_scratch: bool = False
    id: int = 0
    buffer_index: int = 0
    kind: str = "input"


class _FakeAotOp:
    def __init__(
        self,
        id: int,
        type_name: str,
        inputs: list[_FakeTensor] | None = None,
        outputs: list[_FakeTensor] | None = None,
    ):
        self.id = id
        self.TYPE = type_name
        self.name = f"{type_name.lower()}_{id}"
        self.input_tensors = inputs or []
        self.output_tensors = outputs or []


class _FakeCtx:
    def __init__(self, operators: list[Any] | None):
        if operators is not None:
            self.operators = operators


# ---------- _tensor_metadata -------------------------------------------------


class TestTensorMetadata:
    def test_full_tensor_produces_full_metadata(self):
        t = _FakeTensor(name="conv_in", shape=[1, 8, 8, 3], dtype="int8", nbytes=192)
        meta = _tensor_metadata(t)
        assert meta["name"] == "conv_in"
        assert meta["dtype"] == "int8"
        assert meta["shape"] == [1, 8, 8, 3]
        assert meta["nbytes"] == 192
        assert meta["is_constant"] is False

    def test_missing_attributes_are_omitted(self):
        class _Bare:
            name = "x"

        meta = _tensor_metadata(_Bare())
        assert meta == {"name": "x"}

    def test_non_iterable_shape_is_dropped(self):
        class _Weird:
            name = "y"
            shape = 42  # noqa — simulates malformed AOT surface

        meta = _tensor_metadata(_Weird())
        assert "shape" not in meta
        assert meta["name"] == "y"


# ---------- _extract_operator_manifest ---------------------------------------


class TestExtractOperatorManifest:
    def test_missing_operators_returns_empty_list(self):
        assert _extract_operator_manifest(_FakeCtx(None)) == []

    def test_empty_operators_returns_empty_list(self):
        assert _extract_operator_manifest(_FakeCtx([])) == []

    def test_single_op_minimal_fields(self):
        op = _FakeAotOp(id=0, type_name="CONV_2D")
        out = _extract_operator_manifest(_FakeCtx([op]))
        assert len(out) == 1
        entry = out[0]
        assert entry["idx"] == 0
        assert entry["id"] == 0
        assert entry["op_type"] == "CONV_2D"
        assert entry["name"] == "conv_2d_0"
        assert entry["inputs"] == []
        assert entry["outputs"] == []

    def test_multi_op_preserves_execution_order(self):
        ops = [
            _FakeAotOp(id=0, type_name="CONV_2D"),
            _FakeAotOp(id=3, type_name="DEPTHWISE_CONV_2D"),
            _FakeAotOp(id=7, type_name="ADD"),
        ]
        out = _extract_operator_manifest(_FakeCtx(ops))
        assert [e["idx"] for e in out] == [0, 1, 2]
        assert [e["id"] for e in out] == [0, 3, 7]
        assert [e["op_type"] for e in out] == ["CONV_2D", "DEPTHWISE_CONV_2D", "ADD"]

    def test_tensor_metadata_embedded_on_each_op(self):
        x = _FakeTensor(name="x", shape=[1, 8, 8, 3])
        y = _FakeTensor(name="y", shape=[1, 8, 8, 16])
        op = _FakeAotOp(id=0, type_name="CONV_2D", inputs=[x], outputs=[y])
        out = _extract_operator_manifest(_FakeCtx([op]))
        entry = out[0]
        assert entry["inputs"][0]["shape"] == [1, 8, 8, 3]
        assert entry["outputs"][0]["shape"] == [1, 8, 8, 16]
        assert entry["inputs"][0]["name"] == "x"

    def test_tensor_property_raising_is_swallowed(self):
        class _BadOp:
            id = 0
            TYPE = "ADD"
            name = "add_0"

            @property
            def input_tensors(self):
                raise RuntimeError("AOT internals broke")

            @property
            def output_tensors(self):
                return []

        out = _extract_operator_manifest(_FakeCtx([_BadOp()]))
        assert len(out) == 1
        # inputs field is omitted when access fails; outputs still captured.
        assert "inputs" not in out[0]
        assert out[0]["outputs"] == []


# ---------- _write_aot_manifest ----------------------------------------------


class _StubArtifacts:
    def __init__(self, manifest: list[dict[str, Any]] | None):
        self.aot_op_manifest = manifest


class _StubCtx:
    def __init__(self, manifest: list[dict[str, Any]] | None):
        self.engine_artifacts = _StubArtifacts(manifest)


class TestWriteAotManifest:
    def test_returns_none_when_no_engine_artifacts(self, tmp_path: Path):
        class _NoArtifacts:
            pass

        assert _write_aot_manifest(_NoArtifacts(), tmp_path) is None

    def test_returns_none_for_empty_manifest(self, tmp_path: Path):
        assert _write_aot_manifest(_StubCtx([]), tmp_path) is None
        assert not (tmp_path / "aot_operator_manifest.json").exists()

    def test_returns_none_for_missing_manifest_key(self, tmp_path: Path):
        assert _write_aot_manifest(_StubCtx(None), tmp_path) is None

    def test_writes_manifest_json_round_trip(self, tmp_path: Path):
        manifest = [
            {"idx": 0, "id": 0, "op_type": "CONV_2D", "name": "conv_2d_0"},
            {"idx": 1, "id": 3, "op_type": "ADD", "name": "add_3"},
        ]
        out = _write_aot_manifest(_StubCtx(manifest), tmp_path)
        assert out is not None
        assert out.name == "aot_operator_manifest.json"
        round_trip = json.loads(out.read_text())
        assert round_trip == manifest
