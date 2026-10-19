"""Tests for heliaAOT operator manifest extraction and persistence."""

from __future__ import annotations

import csv
import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from helia_profiler.engines.base import HeliaAotArtifacts
from helia_profiler.engines.helia_aot.compile import _BOARD_TO_AOT_PLATFORM
from helia_profiler.engines.helia_aot.manifest import (
    _extract_operator_manifest,
    _tensor_metadata,
)
from helia_profiler.report import _write_aot_manifest, _write_aot_memory_layers
from tests import aot_views
from tests.aot_views import PlacementView, RegionView, Result, TensorView, operator


def test_apollo4_board_variants_map_to_aot_platform():
    # heliaAOT registers exact platforms for the Apollo4 blue/L variants; hpx
    # must map each profiler board to its heliaAOT platform or AOT profiling
    # fails with "No heliaAOT platform mapping for board".
    for board in (
        "apollo4p_evb",
        "apollo4p_blue_kbr_evb",
        "apollo4p_blue_kxr_evb",
        "apollo4l_evb",
        "apollo4l_blue_evb",
    ):
        assert _BOARD_TO_AOT_PLATFORM.get(board) == board


def test_aot_views_match_helia_aot():
    views = pytest.importorskip("helia_aot.views")
    from helia_aot import ConversionResult

    def names(cls: Any) -> list[str]:
        return [f.name for f in dataclasses.fields(cls)]

    for name in ("OperatorView", "QuantizationView", "TensorView", "RegionView", "PlacementView"):
        assert names(getattr(aot_views, name)) == names(getattr(views, name)), name
    assert set(names(Result)) <= set(names(ConversionResult))


def _tensor(tid: str, name: str | None = None, shape: tuple[int, ...] = (1, 8, 8, 3), **fields):
    return TensorView(tid, name, shape, **fields)


class TestTensorMetadata:
    def test_unplaced_tensor_has_no_placement_keys(self):
        t = _tensor("4", "conv_in", nbytes=192, kind="input")
        assert _tensor_metadata(t, None, {}) == {
            "id": "4",
            "name": "conv_in",
            "dtype": "int8",
            "ctype": "int8_t",
            "kind": "input",
            "nbytes": 192,
            "shape": [1, 8, 8, 3],
        }

    def test_unnamed_tensor_omits_name(self):
        assert "name" not in _tensor_metadata(_tensor("4"), None, {})

    def test_placement_without_a_region_adds_nothing(self):
        placement = PlacementView(None, 0, 0, "sram")
        assert "memory" not in _tensor_metadata(_tensor("4"), placement, {})

    def test_placement_metadata_comes_from_the_placement_and_its_region(self):
        region = RegionView(3, "DTCM", "DTCM", "scratch", size=4096)
        placement = PlacementView(3, 128, 256, "DTCM")

        meta = _tensor_metadata(_tensor("7", "x"), placement, {3: region})

        assert meta["memory"] == "dtcm"
        assert meta["source_memory"] == "dtcm"
        assert meta["arena_role"] == "scratch"
        assert meta["arena_region_id"] == 3
        assert meta["offset"] == 128
        assert meta["allocation_size"] == 256
        assert meta["staged"] is False


class TestExtractOperatorManifest:
    def test_empty_operators_returns_empty_list(self):
        assert _extract_operator_manifest(Result(), "hpx") == []

    def test_single_op_minimal_fields(self):
        result = Result(operators=(operator(0, "CONV_2D"),))
        assert _extract_operator_manifest(result, "hpx") == [
            {
                "idx": 0,
                "id": 0,
                "source_indices": [0],
                "op_type": "CONV_2D",
                "name": "conv_2d_0",
                "kernel_calls": None,
                "inputs": [],
                "outputs": [],
            }
        ]

    def test_name_strips_only_the_module_prefix(self):
        result = Result(operators=(operator(0, "ADD", prefix="kws"),))
        assert _extract_operator_manifest(result, "kws")[0]["name"] == "add_0"
        assert _extract_operator_manifest(result, "hpx")[0]["name"] == "kws_add_0"

    def test_operators_without_a_single_source_index_are_kept(self):
        created = operator(1, "RESHAPE", sources=())
        fused = operator(2, "WHILE", sources=(4, 5))
        result = Result(operators=(operator(0, "CONV_2D", sources=(3,)), created, fused))

        out = _extract_operator_manifest(result, "hpx")

        assert [(e["idx"], e["id"], e["source_indices"]) for e in out] == [
            (0, 3, [3]),
            (1, None, []),
            (2, None, [4, 5]),
        ]

    def test_kernel_calls_record_the_compilers_selected_entries(self):
        conv = operator(0, "CONV_2D", kernel_calls={"run": "arm_convolve_s8"})
        reshape = operator(1, "RESHAPE", kernel_calls={})
        out = _extract_operator_manifest(Result(operators=(conv, reshape)), "hpx")
        assert [e["kernel_calls"] for e in out] == [{"run": "arm_convolve_s8"}, {}]

    @pytest.mark.parametrize(
        ("report", "warns"),
        [
            (None, False),
            ({"run": 7}, True),
            ({"run": ""}, True),
            ({"run": "a;b"}, True),
            ({"": "arm_convolve_s8"}, True),
            ({"run=x": "arm_convolve_s8"}, True),
        ],
        ids=[
            "none",
            "non-string-symbol",
            "empty-symbol",
            "non-identifier-symbol",
            "empty-role",
            "role-with-separator",
        ],
    )
    def test_kernel_calls_without_a_usable_compiler_record_stay_unrecorded(
        self, report, warns, caplog
    ):
        result = Result(operators=(operator(0, "CONV_2D", kernel_calls=report),))
        with caplog.at_level("WARNING", logger="hpx"):
            entry = _extract_operator_manifest(result, "hpx")[0]
        assert entry["kernel_calls"] is None
        assert ("CORE calls" in caplog.text) is warns

    def test_multi_op_preserves_execution_order(self):
        ops = (
            operator(0, "CONV_2D", sources=(0,)),
            operator(1, "DEPTHWISE_CONV_2D", sources=(3,)),
            operator(2, "ADD", sources=(7,)),
        )
        out = _extract_operator_manifest(Result(operators=ops), "hpx")
        assert [e["idx"] for e in out] == [0, 1, 2]
        assert [e["id"] for e in out] == [0, 3, 7]
        assert [e["op_type"] for e in out] == ["CONV_2D", "DEPTHWISE_CONV_2D", "ADD"]

    def test_idx_is_the_layer_index_not_the_list_position(self):
        result = Result(operators=(operator(4, "ADD"),))
        assert _extract_operator_manifest(result, "hpx")[0]["idx"] == 4

    def test_tensor_metadata_embedded_on_each_op(self):
        result = Result(
            operators=(operator(0, "CONV_2D", ("x",), ("y",)),),
            tensors=(_tensor("x", "x", (1, 8, 8, 3)), _tensor("y", "y", (1, 8, 8, 16))),
        )
        entry = _extract_operator_manifest(result, "hpx")[0]
        assert entry["inputs"][0]["shape"] == [1, 8, 8, 3]
        assert entry["outputs"][0]["shape"] == [1, 8, 8, 16]
        assert entry["inputs"][0]["name"] == "x"

    def test_local_tensor_placement_is_embedded_on_each_op(self):
        result = Result(
            operators=(operator(0, "CONV_2D", local_tensor_ids=("17",)),),
            tensors=(_tensor("17", "weights", kind="constant"),),
            regions=(
                RegionView(0, "DTCM", "DTCM", "scratch", size=64),
                RegionView(1, "DTCM", "MRAM", "constant", size=2048),
            ),
            placements={"17": PlacementView(1, 64, 1024, "MRAM", staged=True)},
        )

        tensor = _extract_operator_manifest(result, "hpx")[0]["local_tensors"][0]

        assert tensor["name"] == "weights"
        assert tensor["memory"] == "dtcm"
        assert tensor["source_memory"] == "mram"
        assert tensor["staged"] is True
        assert tensor["arena_region_id"] == 1

    def test_operators_without_local_tensors_have_no_local_tensors_key(self):
        result = Result(operators=(operator(0, "ADD"),))
        assert "local_tensors" not in _extract_operator_manifest(result, "hpx")[0]


def _aot_artifacts(manifest: list[dict[str, Any]] | None) -> HeliaAotArtifacts:
    """A real heliaAOT artifact bundle carrying *manifest*.

    The writers narrow on the artifact type, so this is the type the
    production path actually hands them.
    """
    return HeliaAotArtifacts(
        engine_header="model_model.h",
        aot_prefix="model",
        aot_module_name="aot-model",
        aot_cmake_target="nsx::aot_model",
        helia_aot_version="0.18.4",
        aot_op_manifest=manifest,
    )


class _StubCtx:
    def __init__(self, manifest: list[dict[str, Any]] | None):
        self.engine_artifacts = _aot_artifacts(manifest)


class TestWriteAotManifest:
    def test_returns_none_when_no_engine_artifacts(self, tmp_path: Path):
        class _NoArtifacts:
            pass

        assert (
            _write_aot_manifest(_NoArtifacts(), tmp_path)  # ty: ignore[invalid-argument-type]
            is None  # duck-typed ctx stand-in
        )

    def test_returns_none_for_empty_manifest(self, tmp_path: Path):
        assert (
            _write_aot_manifest(_StubCtx([]), tmp_path)  # ty: ignore[invalid-argument-type]
            is None  # duck-typed ctx stand-in
        )
        assert not (tmp_path / "aot_operator_manifest.json").exists()

    def test_returns_none_for_missing_manifest_key(self, tmp_path: Path):
        assert (
            _write_aot_manifest(_StubCtx(None), tmp_path)  # ty: ignore[invalid-argument-type]
            is None  # duck-typed ctx stand-in
        )

    def test_writes_manifest_json_round_trip(self, tmp_path: Path):
        manifest = [
            {"idx": 0, "id": 0, "op_type": "CONV_2D", "name": "conv_2d_0"},
            {"idx": 1, "id": 3, "op_type": "ADD", "name": "add_3"},
        ]
        out = _write_aot_manifest(
            _StubCtx(manifest),  # ty: ignore[invalid-argument-type]
            tmp_path,
        )  # duck-typed ctx stand-in
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

        out = _write_aot_memory_layers(
            _StubCtx(manifest),  # ty: ignore[invalid-argument-type]
            tmp_path,
        )  # duck-typed ctx stand-in

        assert out is not None
        rows = list(csv.DictReader(open(out, encoding="utf-8")))
        assert rows[0]["layer_id"] == "0"
        assert rows[0]["tensor_role"] == "local"
        assert rows[0]["memory"] == "dtcm"
        assert rows[0]["source_memory"] == "mram"

    def test_prefers_nbytes_when_allocation_size_missing(self, tmp_path: Path):
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
                        "nbytes": 1024,
                    }
                ],
            }
        ]

        out = _write_aot_memory_layers(
            _StubCtx(manifest),  # ty: ignore[invalid-argument-type]
            tmp_path,
        )  # duck-typed ctx stand-in

        assert out is not None
        rows = list(csv.DictReader(open(out, encoding="utf-8")))
        assert rows[0]["size"] == "1024"
