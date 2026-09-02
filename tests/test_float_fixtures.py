"""The float fixtures are what their names claim (#246).

``kws_float_fp16.tflite`` is the *true* all-FLOAT16 cast of the FP32 fixture
(``tools/cast_fp16.py``); ``kws_float_fp16_weights.tflite`` is the converter's
weights-only float16 quantization. Regenerating either must not silently swap
them.
"""

from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path
from types import ModuleType

import pytest

schema = pytest.importorskip("ai_edge_litert.schema_py_generated")
pytest.importorskip("flatbuffers")

_FIXTURES = Path(__file__).parent / "fixtures"
_TOOLS = Path(__file__).parents[1] / "tools"


def _load_cast_fp16() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cast_fp16", _TOOLS / "cast_fp16.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _inspect(path: Path) -> tuple[Counter[str], set[str]]:
    """(tensor dtype histogram, builtin op names) for the model's first subgraph."""
    model = schema.Model.GetRootAsModel(path.read_bytes(), 0)
    subgraph = model.Subgraphs(0)
    type_names = {v: k for k, v in vars(schema.TensorType).items() if not k.startswith("_")}
    op_names = {v: k for k, v in vars(schema.BuiltinOperator).items() if not k.startswith("_")}
    dtypes = Counter(
        type_names[subgraph.Tensors(i).Type()] for i in range(subgraph.TensorsLength())
    )
    ops = set()
    for i in range(subgraph.OperatorsLength()):
        code = model.OperatorCodes(subgraph.Operators(i).OpcodeIndex())
        ops.add(op_names[max(code.BuiltinCode(), code.DeprecatedBuiltinCode())])
    return dtypes, ops


def test_fp32_fixture_is_plain_float32() -> None:
    dtypes, ops = _inspect(_FIXTURES / "kws_float_fp32.tflite")
    assert set(dtypes) == {"FLOAT32", "INT32"}
    assert "DEQUANTIZE" not in ops


def test_fp16_fixture_is_the_true_cast_of_the_fp32_fixture() -> None:
    cast, _ = _load_cast_fp16().cast_model((_FIXTURES / "kws_float_fp32.tflite").read_bytes())
    assert cast == (_FIXTURES / "kws_float_fp16.tflite").read_bytes()
    dtypes, ops = _inspect(_FIXTURES / "kws_float_fp16.tflite")
    assert "FLOAT32" not in dtypes and "DEQUANTIZE" not in ops


def test_fp16_weights_fixture_dequantizes_float16_weights() -> None:
    dtypes, ops = _inspect(_FIXTURES / "kws_float_fp16_weights.tflite")
    assert dtypes["FLOAT16"] > 0 and dtypes["FLOAT32"] > 0
    assert "DEQUANTIZE" in ops
