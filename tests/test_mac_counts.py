"""MAC counts checked against first-principles references.

Each case builds a one-op ``.tflite`` graph, proves LiteRT runs it and
agrees with a naive numpy reference that counts its own multiplies, then
asserts the analyzers report that count.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

schema = pytest.importorskip("ai_edge_litert.schema_py_generated")
flatbuffers = pytest.importorskip("flatbuffers")

from helia_profiler.evaluation.engine_analysis import analyze_for_engine  # noqa: E402
from helia_profiler.modelcost.model_analysis import (  # noqa: E402
    _batch_matmul_macs,
    _fully_connected_macs,
    _transpose_conv_macs,
    analyze_air_model,
    is_aot_available,
)

_FLOAT32 = schema.TensorType.FLOAT32
_INT32 = schema.TensorType.INT32


@dataclass(frozen=True)
class _Tensor:
    shape: tuple[int, ...]
    data: np.ndarray | None = None


def _write_model(
    path: Path,
    op_code: int,
    tensors: list[_Tensor],
    inputs: list[int],
    outputs: list[int],
    options_type: int,
    options: object,
) -> Path:
    """Serialize a single-op graph; constant tensors carry ``data``."""
    buffers = [schema.BufferT()]
    tensor_objs = []
    for index, spec in enumerate(tensors):
        t = schema.TensorT()
        t.shape = list(spec.shape)
        t.name = f"t{index}"
        t.type = _INT32 if spec.data is not None and spec.data.dtype == np.int32 else _FLOAT32
        t.buffer = 0
        if spec.data is not None:
            buf = schema.BufferT()
            buf.data = list(spec.data.tobytes())
            buffers.append(buf)
            t.buffer = len(buffers) - 1
        tensor_objs.append(t)

    code = schema.OperatorCodeT()
    code.builtinCode = op_code
    code.deprecatedBuiltinCode = min(op_code, 127)
    code.version = 1

    op = schema.OperatorT()
    op.opcodeIndex = 0
    op.inputs = inputs
    op.outputs = outputs
    op.builtinOptionsType = options_type
    op.builtinOptions = options

    graph = schema.SubGraphT()
    graph.name = "main"
    graph.tensors = tensor_objs
    graph.inputs = [i for i in inputs if i >= 0 and tensors[i].data is None]
    graph.outputs = outputs
    graph.operators = [op]

    model = schema.ModelT()
    model.version = 3
    model.operatorCodes = [code]
    model.subgraphs = [graph]
    model.buffers = buffers

    builder = flatbuffers.Builder(1024)
    builder.Finish(model.Pack(builder), file_identifier=b"TFL3")
    path.write_bytes(bytes(builder.Output()))
    return path


def _run_litert(path: Path, feeds: list[np.ndarray]) -> np.ndarray:
    from ai_edge_litert.interpreter import Interpreter

    interp = Interpreter(model_path=str(path))
    interp.allocate_tensors()
    for detail, value in zip(interp.get_input_details(), feeds, strict=True):
        interp.set_tensor(detail["index"], value)
    interp.invoke()
    return interp.get_tensor(interp.get_output_details()[0]["index"])


def _analyzed_macs(path: Path) -> int:
    analysis = analyze_for_engine(path, engine="tflm")
    assert len(analysis.layers) == 1
    return analysis.layers[0].macs


def _air_macs(path: Path) -> int:
    from helia_aot.converters import convert_backend_model
    from helia_aot.registry.context import build_default_registry_context

    air_model = convert_backend_model(path, build_default_registry_context(), verbose=0)
    analysis = analyze_air_model(air_model)
    assert analysis is not None
    return analysis.total_macs


_needs_aot = pytest.mark.skipif(not is_aot_available(), reason="helia-aot not installed")


def _reference_transpose_conv(
    x: np.ndarray, w: np.ndarray, stride: int, out_hw: tuple[int, int]
) -> tuple[np.ndarray, int]:
    """Scatter every input pixel through every tap; count each multiply."""
    n, h_in, w_in, c_in = x.shape
    c_out, k_h, k_w, _ = w.shape
    full = np.zeros((n, (h_in - 1) * stride + k_h, (w_in - 1) * stride + k_w, c_out), np.float64)
    multiplies = 0
    for b in range(n):
        for y in range(h_in):
            for xx in range(w_in):
                for ky in range(k_h):
                    for kx in range(k_w):
                        for co in range(c_out):
                            products = x[b, y, xx, :] * w[co, ky, kx, :]
                            multiplies += products.size
                            full[b, y * stride + ky, xx * stride + kx, co] += products.sum()
    pad_top = (full.shape[1] - out_hw[0]) // 2
    pad_left = (full.shape[2] - out_hw[1]) // 2
    cropped = full[:, pad_top : pad_top + out_hw[0], pad_left : pad_left + out_hw[1], :]
    return cropped, multiplies


@pytest.fixture
def transpose_conv_model(tmp_path: Path) -> tuple[Path, np.ndarray, int]:
    rng = np.random.default_rng(0)
    batch, h_in, w_in, c_in, c_out, kernel, stride = 1, 8, 8, 16, 32, 3, 2
    out_shape = (batch, h_in * stride, w_in * stride, c_out)
    x = rng.standard_normal((batch, h_in, w_in, c_in)).astype(np.float32)
    w = rng.standard_normal((c_out, kernel, kernel, c_in)).astype(np.float32)

    opts = schema.TransposeConvOptionsT()
    opts.padding = schema.Padding.SAME
    opts.strideH = stride
    opts.strideW = stride
    path = _write_model(
        tmp_path / "transpose_conv.tflite",
        schema.BuiltinOperator.TRANSPOSE_CONV,
        [
            _Tensor((4,), np.array(out_shape, np.int32)),
            _Tensor(w.shape, w),
            _Tensor(x.shape),
            _Tensor(out_shape),
        ],
        inputs=[0, 1, 2],
        outputs=[3],
        options_type=schema.BuiltinOptions.TransposeConvOptions,
        options=opts,
    )
    expected, multiplies = _reference_transpose_conv(x, w, stride, out_shape[1:3])
    np.testing.assert_allclose(_run_litert(path, [x]), expected, rtol=1e-4, atol=1e-4)
    return path, x, multiplies


def test_transpose_conv_counts_input_pixels(transpose_conv_model):
    path, _, multiplies = transpose_conv_model
    assert multiplies == 8 * 8 * 3 * 3 * 16 * 32
    assert _analyzed_macs(path) == multiplies


@_needs_aot
def test_air_transpose_conv_counts_input_pixels(transpose_conv_model):
    path, _, multiplies = transpose_conv_model
    assert _air_macs(path) == multiplies


def _reference_fully_connected(x: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, int]:
    """TFLite flattens the input into rows of N_in; count each multiply."""
    n_out, n_in = w.shape
    rows = x.reshape(-1, n_in)
    out = np.zeros((rows.shape[0], n_out), np.float64)
    multiplies = 0
    for r in range(rows.shape[0]):
        for o in range(n_out):
            for i in range(n_in):
                out[r, o] += rows[r, i] * w[o, i]
                multiplies += 1
    return out, multiplies


@pytest.mark.parametrize(
    ("input_shape", "n_in", "keep_num_dims", "output_shape"),
    [
        pytest.param((1, 4, 4, 8), 128, False, (1, 10), id="4d-input-flattened"),
        pytest.param((2, 3, 8), 8, True, (2, 3, 10), id="keep-num-dims"),
    ],
)
def test_fully_connected_counts_flattened_rows(
    tmp_path: Path, input_shape, n_in, keep_num_dims, output_shape
):
    rng = np.random.default_rng(1)
    n_out = output_shape[-1]
    x = rng.standard_normal(input_shape).astype(np.float32)
    w = rng.standard_normal((n_out, n_in)).astype(np.float32)

    opts = schema.FullyConnectedOptionsT()
    opts.keepNumDims = keep_num_dims
    path = _write_model(
        tmp_path / "fully_connected.tflite",
        schema.BuiltinOperator.FULLY_CONNECTED,
        [_Tensor(x.shape), _Tensor(w.shape, w), _Tensor(output_shape)],
        inputs=[0, 1, -1],
        outputs=[2],
        options_type=schema.BuiltinOptions.FullyConnectedOptions,
        options=opts,
    )
    expected, multiplies = _reference_fully_connected(x, w)
    actual = _run_litert(path, [x])
    np.testing.assert_allclose(actual.reshape(expected.shape), expected, rtol=1e-4, atol=1e-4)

    assert _analyzed_macs(path) == multiplies


def test_fully_connected_4d_example():
    assert _fully_connected_macs([1, 4, 4, 8], [10, 128], has_bias=True) == 1 * 128 * 10


@pytest.mark.parametrize("adj_x", [False, True], ids=["plain", "adj-x"])
def test_batch_matmul_counts_shared_dim(tmp_path: Path, adj_x: bool):
    rng = np.random.default_rng(2)
    batch, rows, inner, cols = 2, 3, 4, 5
    lhs_shape = (batch, inner, rows) if adj_x else (batch, rows, inner)
    lhs = rng.standard_normal(lhs_shape).astype(np.float32)
    rhs = rng.standard_normal((batch, inner, cols)).astype(np.float32)

    opts = schema.BatchMatMulOptionsT()
    opts.adjX = adj_x
    path = _write_model(
        tmp_path / "batch_matmul.tflite",
        schema.BuiltinOperator.BATCH_MATMUL,
        [_Tensor(lhs.shape), _Tensor(rhs.shape), _Tensor((batch, rows, cols))],
        inputs=[0, 1],
        outputs=[2],
        options_type=schema.BuiltinOptions.BatchMatMulOptions,
        options=opts,
    )
    left = np.swapaxes(lhs, 1, 2) if adj_x else lhs
    expected = np.zeros((batch, rows, cols), np.float64)
    multiplies = 0
    for b in range(batch):
        for r in range(rows):
            for c in range(cols):
                for k in range(inner):
                    expected[b, r, c] += left[b, r, k] * rhs[b, k, c]
                    multiplies += 1
    np.testing.assert_allclose(_run_litert(path, [lhs, rhs]), expected, rtol=1e-4, atol=1e-4)

    assert _analyzed_macs(path) == multiplies


@_needs_aot
def test_air_batch_matmul_counts_shared_dim():
    from helia_aot.air.enums import AirOpType

    shapes = {"lhs": [2, 3, 4], "rhs": [2, 4, 5], "out": [2, 3, 5]}
    air_model = SimpleNamespace(
        operators=[
            SimpleNamespace(
                id="0",
                op_type=AirOpType.BATCH_MATMUL,
                input_ids=["lhs", "rhs"],
                output_ids=["out"],
                named_tensors={},
                options=SimpleNamespace(adj_x=False, adj_y=False),
            )
        ],
        get_tensor=lambda tid: SimpleNamespace(shape=shapes[tid]),
    )
    analysis = analyze_air_model(air_model)
    assert analysis is not None
    assert analysis.total_macs == 2 * 3 * 5 * 4


def test_helpers_reject_malformed_shapes():
    assert _transpose_conv_macs([1, 8, 8, 16], [32, 3, 3]) == 0
    assert _transpose_conv_macs([], [32, 3, 3, 16]) == 0
    assert _fully_connected_macs([1, 128], [64, 0], has_bias=False) == 0
    assert _batch_matmul_macs([4], [4], adj_x=False) == 0
