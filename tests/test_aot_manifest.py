"""Tests for heliaAOT operator manifest extraction and persistence."""

from __future__ import annotations

import json
import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from helia_profiler.engines.helia_aot import (
    _extract_operator_manifest,
    _tensor_metadata,
)
from helia_profiler.report import _write_aot_manifest, _write_aot_memory_layers


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
        local_tensors: list[_FakeTensor] | None = None,
    ):
        self.id = id
        self.TYPE = type_name
        self.name = f"{type_name.lower()}_{id}"
        self.input_tensors = inputs or []
        self.output_tensors = outputs or []
        self.local_tensors = local_tensors or []


class _FakeCtx:
    def __init__(self, operators: list[Any] | None, memory_plan: Any | None = None, render_plan: Any | None = None):
        if operators is not None:
            self.operators = operators
        if memory_plan is not None:
            self.memory_plan = memory_plan
        if render_plan is not None:
            self.render_plan = render_plan


@dataclass
class _FakeBinding:
    role: str
    memory: str
    source_memory: str
    offset: int


@dataclass
class _FakeAllocation:
    memory: str
    offset: int
    size: int
    binding: _FakeBinding


@dataclass
class _FakeMemoryPlan:
    tensor_allocs: dict[str, _FakeAllocation]


@dataclass
class _FakeArena:
    region_id: int
    role: str
    memory: str
    source_memory: str


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

    def test_allocation_metadata_is_embedded_when_available(self):
        t = _FakeTensor(name="x", id=7)
        alloc = _FakeAllocation(
            memory="DTCM",
            offset=128,
            size=256,
            binding=_FakeBinding(role="scratch", memory="DTCM", source_memory="DTCM", offset=128),
        )

        meta = _tensor_metadata(t, {"7": alloc}, {("scratch", "dtcm", "dtcm"): 3})

        assert meta["memory"] == "dtcm"
        assert meta["source_memory"] == "dtcm"
        assert meta["arena_role"] == "scratch"
        assert meta["arena_region_id"] == 3
        assert meta["offset"] == 128
        assert meta["allocation_size"] == 256
        assert meta["staged"] is False


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

    def test_local_tensor_placement_is_embedded_on_each_op(self):
        local = _FakeTensor(name="weights", id=17, kind="constant", is_constant=True)
        op = _FakeAotOp(id=0, type_name="CONV_2D", local_tensors=[local])
        memory_plan = _FakeMemoryPlan(
            {
                "17": _FakeAllocation(
                    memory="MRAM",
                    offset=64,
                    size=1024,
                    binding=_FakeBinding(
                        role="constant",
                        memory="DTCM",
                        source_memory="MRAM",
                        offset=64,
                    ),
                )
            }
        )
        render_plan = type(
            "_RenderPlan",
            (),
            {
                "scratch_arenas": [],
                "persistent_arenas": [],
                "constant_arenas": [_FakeArena(1, "constant", "DTCM", "MRAM")],
            },
        )()

        out = _extract_operator_manifest(_FakeCtx([op], memory_plan, render_plan))

        tensor = out[0]["local_tensors"][0]
        assert tensor["name"] == "weights"
        assert tensor["memory"] == "dtcm"
        assert tensor["source_memory"] == "mram"
        assert tensor["staged"] is True
        assert tensor["arena_region_id"] == 1

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


class TestWriteAotMemoryLayers:
    def test_writes_flat_placement_csv(self, tmp_path: Path):
        manifest = [
            {
                "idx": 0,
                "id": 0,
                "op_type": "CONV_2D",
                "name": "conv_2d_0",
                "local_tensors": [
                    {
                        "id": 17,
                        "name": "weights",
                        "kind": "constant",
                        "memory": "dtcm",
                        "source_memory": "mram",
                        "staged": True,
                        "arena_role": "constant",
                        "arena_region_id": 1,
                        "offset": 64,
                        "allocation_size": 1024,
                        "shape": [64, 1, 5, 1],
                    }
                ],
            }
        ]

        out = _write_aot_memory_layers(_StubCtx(manifest), tmp_path)

        assert out is not None
        rows = list(csv.DictReader(open(out)))
        assert rows[0]["layer_id"] == "0"
        assert rows[0]["tensor_role"] == "local"
        assert rows[0]["memory"] == "dtcm"
        assert rows[0]["source_memory"] == "mram"
