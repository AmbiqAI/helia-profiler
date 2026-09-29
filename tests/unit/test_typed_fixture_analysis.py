"""Typed fixture analysis reads every IO tensor's dtype, shape and quantization."""

from pathlib import Path

import pytest

from helia_profiler.fixture_analysis import (
    FixtureTensor,
    PerAxisQuantization,
    PerTensorQuantization,
    analyze_fixture_model,
    analyze_typed_fixture_model,
)

s = pytest.importorskip("ai_edge_litert.schema_py_generated")
flatbuffers = pytest.importorskip("flatbuffers")

_INT16 = ("int16", [0.001], [0])
_F32 = ("float32", None, None)


def _tensor(name, dtype, shape, scales, zero_points, axis=0):
    tensor = s.TensorT()
    tensor.name = name
    tensor.shape = list(shape)
    tensor.type = {
        "int8": s.TensorType.INT8,
        "int16": s.TensorType.INT16,
        "float16": s.TensorType.FLOAT16,
        "float32": s.TensorType.FLOAT32,
    }[dtype]
    if scales is not None:
        tensor.quantization = s.QuantizationParametersT()
        tensor.quantization.scale = list(scales)
        tensor.quantization.zeroPoint = list(zero_points)
        tensor.quantization.quantizedDimension = axis
    return tensor


def typed_model(tmp_path: Path, inputs, outputs, *, extra=(), change=None) -> Path:
    """One ADD per output over the first input; tensors are inputs, outputs, then extras."""
    model = s.ModelT()
    model.version = 3
    model.buffers = [s.BufferT()]
    code = s.OperatorCodeT()
    code.builtinCode = s.BuiltinOperator.ADD
    code.deprecatedBuiltinCode = code.builtinCode
    model.operatorCodes = [code]
    graph = s.SubGraphT()
    graph.tensors = [_tensor(*spec) for spec in (*inputs, *outputs, *extra)]
    graph.inputs = list(range(len(inputs)))
    graph.outputs = list(range(len(inputs), len(inputs) + len(outputs)))
    graph.operators = []
    for out in graph.outputs:
        operator = s.OperatorT()
        operator.inputs = [0, 0]
        operator.outputs = [out]
        graph.operators.append(operator)
    model.subgraphs = [graph]
    if change:
        change(model, graph)
    builder = flatbuffers.Builder(1024)
    builder.Finish(model.Pack(builder), file_identifier=b"TFL3")
    path = tmp_path / "model.tflite"
    path.write_bytes(bytes(builder.Output()))
    return path


def test_reads_every_input_and_output_in_graph_order(tmp_path):
    path = typed_model(
        tmp_path,
        [("signal", *_INT16[:1], (1, 16), *_INT16[1:]), ("gain", "float32", (1, 1), None, None)],
        [
            ("label", "int8", (1, 4), [0.00390625], [-128]),
            ("level", "float32", (1, 2), None, None),
        ],
    )
    result = analyze_typed_fixture_model(path)
    assert result.inputs == (
        FixtureTensor(
            "signal", 0, "int16", (1, 16), PerTensorQuantization(0.0010000000474974513, 0)
        ),
        FixtureTensor("gain", 1, "float32", (1, 1), None),
    )
    assert result.outputs == (
        FixtureTensor("label", 2, "int8", (1, 4), PerTensorQuantization(0.00390625, -128)),
        FixtureTensor("level", 3, "float32", (1, 2), None),
    )
    assert (result.input_bytes, result.output_bytes) == (36, 12)
    assert result.has_float16 is False
    assert result.resolver.registrations == ("r.AddAdd();",)


def test_per_axis_quantization_is_kept_per_channel(tmp_path):
    path = typed_model(
        tmp_path,
        [("x", "int8", (1, 3), [0.5, 0.25, 0.125], [0, 1, -1], 1)],
        [("y", "int8", (1, 3), [0.5], [0])],
    )
    (x,) = analyze_typed_fixture_model(path).inputs
    assert x.quantization == PerAxisQuantization(1, (0.5, 0.25, 0.125), (0, 1, -1))


@pytest.mark.parametrize("where", ["io", "weight"])
def test_float16_anywhere_in_the_graph_is_flagged(tmp_path, where):
    f16 = ("h", "float16", (1, 2), None, None)
    f32 = ("h", "float32", (1, 2), None, None)
    path = typed_model(
        tmp_path,
        [f16 if where == "io" else f32],
        [("y", "float32", (1, 2), None, None)],
        extra=[("w", "float16", (2,), None, None)] if where == "weight" else (),
    )
    assert analyze_typed_fixture_model(path).has_float16 is True


def test_single_int8_model_reads_the_same_under_both_analyses(tmp_path):
    path = typed_model(
        tmp_path, [("x", "int8", (1, 4), [0.25], [-3])], [("y", "int8", (1, 4), [0.5], [2])]
    )
    legacy = analyze_fixture_model(path)
    typed = analyze_typed_fixture_model(path)
    (x,), (y,) = typed.inputs, typed.outputs
    assert (x.index, x.shape, x.size_bytes) == (
        legacy.input_tensor.tensor_index,
        legacy.input_tensor.shape,
        legacy.input_tensor.size,
    )
    assert x.quantization == PerTensorQuantization(
        legacy.input_tensor.scale, legacy.input_tensor.zero_point
    )
    assert (y.index, y.size_bytes) == (legacy.output_tensor.tensor_index, legacy.output_bytes)
    assert typed.resolver == legacy.resolver


def test_legacy_analysis_still_requires_exactly_one_input_and_output(tmp_path):
    path = typed_model(
        tmp_path,
        [("a", "int8", (1, 4), [0.25], [0]), ("b", "int8", (1, 4), [0.25], [0])],
        [("y", "int8", (1, 4), [0.5], [0])],
    )
    with pytest.raises(ValueError, match="exactly one input and one output"):
        analyze_fixture_model(path)
    assert len(analyze_typed_fixture_model(path).inputs) == 2


@pytest.mark.parametrize(
    "spec,match",
    [
        (("x", "int8", (1, 4), None, None), "requires quantization"),
        (("x", "int16", (1, 4), [0.1, 0.2], [0]), "requires quantization"),
        (("x", "float32", (1, 4), [0.1], [0]), "carries quantization"),
        (("x", "int16", (1, 4), [0.1], [40000]), "zero point out of range"),
        (("x", "int8", (1, 4), [0.0], [0]), "Positive finite"),
        (("x", "int8", (1, 3), [0.5, 0.5], [0, 0], 1), "Per-axis"),
        (("", "int8", (1, 4), [0.1], [0]), "Named"),
    ],
)
def test_rejects_malformed_io_declarations(tmp_path, spec, match):
    path = typed_model(tmp_path, [spec], [("y", "float32", (1, 4), None, None)])
    with pytest.raises(ValueError, match=match):
        analyze_typed_fixture_model(path)


def test_rejects_unsupported_io_dtype(tmp_path):
    def uint8_input(model, graph):
        graph.tensors[0].type = s.TensorType.UINT8

    path = typed_model(
        tmp_path,
        [("x", "int8", (1, 4), [0.1], [0])],
        [("y", "float32", (1, 4), None, None)],
        change=uint8_input,
    )
    with pytest.raises(ValueError, match="Unsupported fixture IO tensor type"):
        analyze_typed_fixture_model(path)


@pytest.mark.parametrize(
    "change,match",
    [
        (lambda m, g: setattr(m, "subgraphs", [g, g]), "one subgraph"),
        (lambda m, g: setattr(g, "outputs", []), "at least one input and one output"),
        (lambda m, g: setattr(g.tensors[1], "isVariable", True), "stateful"),
        (lambda m, g: setattr(g.tensors[0], "shapeSignature", [1, -1]), "Dynamic"),
    ],
)
def test_typed_analysis_keeps_the_static_graph_rules(tmp_path, change, match):
    path = typed_model(
        tmp_path,
        [("x", "float32", (1, 4), None, None)],
        [("y", "float32", (1, 4), None, None)],
        change=change,
    )
    with pytest.raises(ValueError, match=match):
        analyze_typed_fixture_model(path)
