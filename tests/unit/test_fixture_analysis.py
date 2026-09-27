"""Static fixture validation uses generated flatbuffers, never an interpreter."""

from pathlib import Path

import pytest

from helia_profiler.modelcost.fixture_analysis import analyze_fixture_model

s = pytest.importorskip("ai_edge_litert.schema_py_generated")
flatbuffers = pytest.importorskip("flatbuffers")


def model_file(tmp_path: Path, *, shape=(1, 4), op=None, change=None) -> Path:
    model = s.ModelT()
    model.version = 3
    model.buffers = [s.BufferT()]
    code = s.OperatorCodeT()
    code.builtinCode = s.BuiltinOperator.RELU if op is None else op
    code.deprecatedBuiltinCode = min(code.builtinCode, 127)
    model.operatorCodes = [code]
    graph = s.SubGraphT()
    graph.inputs = [0]
    graph.outputs = [1]
    graph.tensors = []
    for index in range(2):
        tensor = s.TensorT()
        tensor.shape = list(shape)
        tensor.type = s.TensorType.INT8
        tensor.quantization = s.QuantizationParametersT()
        tensor.quantization.scale = [0.25 if index == 0 else 0.5]
        tensor.quantization.zeroPoint = [-3 if index == 0 else 2]
        graph.tensors.append(tensor)
    operator = s.OperatorT()
    operator.inputs = [0]
    operator.outputs = [1]
    graph.operators = [operator]
    model.subgraphs = [graph]
    if change:
        change(model, graph)
    builder = flatbuffers.Builder(1024)
    builder.Finish(model.Pack(builder), file_identifier=b"TFL3")
    path = tmp_path / "model.tflite"
    path.write_bytes(bytes(builder.Output()))
    return path


@pytest.mark.parametrize(
    "shape,op,registration",
    [
        ((1, 4), s.BuiltinOperator.RELU, "r.AddRelu();"),
        ((2, 3, 5), s.BuiltinOperator.TANH, "r.AddTanh();"),
    ],
)
def test_derives_signature_and_only_used_operator(tmp_path, shape, op, registration):
    result = analyze_fixture_model(model_file(tmp_path, shape=shape, op=op))
    assert result.input_tensor.shape == shape
    assert result.output_tensor.shape == shape
    assert result.input_tensor.size == (4 if len(shape) == 2 else 30)
    assert result.input_tensor.scale == 0.25
    assert result.input_tensor.zero_point == -3
    assert result.output_tensor.scale == 0.5
    assert result.output_tensor.zero_point == 2
    assert result.output_tensor.tensor_index == 1
    assert result.resolver.registrations == (registration,)
    assert result.analysis.total_ops == result.output_tensor.size


@pytest.mark.parametrize(
    "change,match",
    [
        (lambda m, g: setattr(m, "subgraphs", [g, g]), "one subgraph"),
        (lambda m, g: setattr(g, "inputs", [0, 1]), "one input"),
        (lambda m, g: setattr(g, "outputs", [0, 1]), "one output"),
        (lambda m, g: setattr(g.tensors[0], "shapeSignature", [1, -1]), "Dynamic"),
        (lambda m, g: setattr(g.tensors[1], "shape", [1, 0]), "Dynamic"),
        (lambda m, g: setattr(g.tensors[0], "isVariable", True), "stateful"),
        (lambda m, g: setattr(g.tensors[1], "type", s.TensorType.FLOAT32), "INT8"),
        (lambda m, g: setattr(g.tensors[0].quantization, "scale", [0.1, 0.2]), "per-tensor"),
        (lambda m, g: setattr(g.tensors[0].quantization, "scale", [float("nan")]), "finite"),
        (lambda m, g: setattr(g.tensors[0].quantization, "zeroPoint", [128]), "zero point"),
        (lambda m, g: setattr(g, "inputs", [9]), "tensor index"),
        (lambda m, g: setattr(g.operators[0], "opcodeIndex", 9), "code index"),
        (lambda m, g: setattr(g.operators[0], "outputs", [9]), "tensor index"),
        (lambda m, g: setattr(g.tensors[0], "buffer", 9), "buffer index"),
        (lambda m, g: setattr(g.operators[0], "mutatingVariableInputs", [True]), "Stateful"),
    ],
)
def test_rejects_unsupported_signature(tmp_path, change, match):
    with pytest.raises(ValueError, match=match):
        analyze_fixture_model(model_file(tmp_path, change=change))


@pytest.mark.parametrize("op", [s.BuiltinOperator.CUSTOM, s.BuiltinOperator.VAR_HANDLE, 999])
def test_rejects_unsupported_operator(tmp_path, op):
    with pytest.raises(ValueError, match="Unsupported fixture operators"):
        analyze_fixture_model(model_file(tmp_path, op=op))


@pytest.mark.parametrize("data", [b"", b"invalid model", b"\xff\xff\xff\xffTFL3"])
def test_malformed_flatbuffer_is_clear_error(tmp_path, data):
    path = tmp_path / "invalid.tflite"
    path.write_bytes(data)
    with pytest.raises(ValueError, match="fixture model"):
        analyze_fixture_model(path)


def test_missing_optional_schema_has_actionable_error(tmp_path, monkeypatch):
    from helia_profiler.modelcost import model_analysis

    monkeypatch.setattr(model_analysis, "_schema", None)
    with pytest.raises(ValueError, match="analysis extra"):
        analyze_fixture_model(tmp_path / "unused.tflite")


def test_truncated_tensor_table_is_rejected(tmp_path):
    path = model_file(tmp_path)
    path.write_bytes(path.read_bytes()[:80])
    with pytest.raises(ValueError, match="fixture model"):
        analyze_fixture_model(path)


def test_concrete_batch_one_with_symbolic_batch_is_not_resized(tmp_path):
    path = model_file(
        tmp_path, change=lambda m, g: setattr(g.tensors[0], "shapeSignature", [-1, 4])
    )
    original = path.read_bytes()
    result = analyze_fixture_model(path)
    assert result.input_tensor.shape == (1, 4)
    assert result.input_tensor.size == 4
    assert path.read_bytes() == original


def test_symbolic_batch_with_nonunit_stored_batch_is_rejected(tmp_path):
    path = model_file(
        tmp_path, shape=(2, 4), change=lambda m, g: setattr(g.tensors[0], "shapeSignature", [-1, 4])
    )
    with pytest.raises(ValueError, match="Dynamic"):
        analyze_fixture_model(path)
