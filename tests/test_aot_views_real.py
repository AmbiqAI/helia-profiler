"""Real heliaAOT conversions through hpx's view readers.

The unit tests use the stand-ins in ``tests/aot_views.py``; these run only
where heliaAOT is installed and check the readers against real views, whose
mappings and memory enums are heliaAOT's own types.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("helia_aot")

from helia_profiler import Session  # noqa: E402
from helia_profiler.engines.helia_aot.compile import (  # noqa: E402
    _run_aot_compiler,
    aot_platform_for_board,
)
from helia_profiler.engines.helia_aot.manifest import (  # noqa: E402
    _extract_arena_regions,
    _extract_memory_plan,
    _extract_operator_manifest,
)
from helia_profiler.evaluation.engine_analysis import analyze_for_engine  # noqa: E402
from helia_profiler.modelcost import analyze_model, is_available  # noqa: E402

KWS = Path(__file__).parent / "fixtures" / "kws_ref_model.tflite"
BOARD = "apollo510_evb"


def _convert(tmp_path: Path, arena: str, weights: str, *, external: bool = False):
    engine = {"config": {"aot_args": {"memory": {"allocate_arenas": False}}}} if external else {}
    config = (
        Session.from_dict({})
        .with_model(KWS, arena_location=arena, weights_location=weights)
        .with_engine("helia-aot", **engine)
        .with_target(board=BOARD)
        .resolve()
    )
    platform = aot_platform_for_board(BOARD)
    assert platform is not None
    return _run_aot_compiler(config, tmp_path, "hpx_aot", "hpx", platform)


def _generated_text(tmp_path: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (tmp_path / "hpx_aot").rglob("*")
        if path.suffix in {".c", ".h"}
    )


def test_manifest_from_a_real_conversion(tmp_path: Path):
    manifest = _extract_operator_manifest(_convert(tmp_path, "sram", "mram"), "hpx")

    assert [(op["idx"], op["id"]) for op in manifest] == [(i, i) for i in range(13)]
    assert [op["op_type"] for op in manifest][-4:] == [
        "AVERAGE_POOL_2D",
        "RESHAPE",
        "FULLY_CONNECTED",
        "SOFTMAX",
    ]
    assert manifest[0]["name"] == "conv_2d_0"
    assert all(isinstance(op["kernel_calls"], dict) for op in manifest)
    assert manifest[0]["kernel_calls"]
    assert {tensor["memory"] for op in manifest for tensor in op["outputs"]} == {"sram"}
    json.dumps(manifest)


@pytest.mark.parametrize(("arena", "weights"), [("sram", "mram"), ("tcm", "tcm")])
def test_internal_arena_symbols_exist_in_the_generated_module(
    tmp_path: Path, arena: str, weights: str
):
    result = _convert(tmp_path, arena, weights)
    plan = _extract_memory_plan(result)
    assert plan is not None
    symbols = {c.symbol for region in plan.regions for c in region.consumers}
    assert None not in symbols
    text = _generated_text(tmp_path)
    assert all(symbol in text for symbol in symbols if symbol is not None)
    assert all(region.blob_filename is None for region in _extract_arena_regions(result, "hpx"))


def test_external_arenas_bind_hpx_storage_and_sidecar_files(tmp_path: Path):
    result = _convert(tmp_path, "sram", "mram", external=True)
    plan = _extract_memory_plan(result, allocate_arenas=False)
    assert plan is not None
    by_name = {c.name: c.symbol for region in plan.regions for c in region.consumers}
    assert by_name == {
        "mram_constant_arena_1": None,
        "sram_scratch_arena_0": "hpx_arena_buf_storage_0",
    }
    regions = _extract_arena_regions(result, "hpx")
    blobs = {region.region_id: region.blob_filename for region in regions if region.blob_filename}
    assert list(blobs) == [1]
    assert (tmp_path / "hpx_aot" / blobs[1]).is_file()


@pytest.mark.skipif(not is_available(), reason="ai-edge-litert not installed")
def test_analysis_of_a_real_conversion_matches_the_source_model():
    compiled = analyze_for_engine(KWS, engine="helia-aot", board=BOARD)
    source = analyze_model(KWS)
    assert source is not None
    assert (compiled.total_macs, compiled.num_parameters, compiled.total_ops) == (
        source.total_macs,
        source.num_parameters,
        source.total_ops,
    )
    pools = [layer for layer in compiled.layers if layer.op == "AVERAGE_POOL_2D"]
    assert [layer.params["filter_h"] for layer in pools] == [25]
    assert all(layer.ops > 0 for layer in pools)
