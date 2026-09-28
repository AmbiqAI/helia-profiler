"""Bounded, static model metadata for fixed INT8 inference fixtures."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .firmware.op_resolver import ResolverPlan, build_fixture_resolver_plan
from .modelcost import model_analysis
from .modelcost.model_analysis import ModelAnalysis


@dataclass(frozen=True)
class Int8Tensor:
    shape: tuple[int, ...]
    scale: float
    zero_point: int
    tensor_index: int

    def __post_init__(self) -> None:
        if not self.shape or any(type(n) is not int or n <= 0 for n in self.shape):
            raise ValueError("Positive fixed tensor dimensions required")
        if not math.isfinite(self.scale) or self.scale <= 0:
            raise ValueError("Positive finite quantization scale required")
        if type(self.zero_point) is not int or not -128 <= self.zero_point <= 127:
            raise ValueError("INT8 zero point required")
        if type(self.tensor_index) is not int or self.tensor_index < 0:
            raise ValueError("Nonnegative tensor index required")

    @property
    def size(self) -> int:
        return math.prod(self.shape)


@dataclass(frozen=True)
class FixtureModelAnalysis:
    input_tensor: Int8Tensor
    output_tensor: Int8Tensor
    analysis: ModelAnalysis
    resolver: ResolverPlan


def _io_tensor(graph: Any, index: int, int8_type: int) -> Int8Tensor:
    tensor = graph.Tensors(index)
    if tensor.Type() != int8_type:
        raise ValueError("Fixture input and output must be INT8")
    quant = tensor.Quantization()
    if quant is None or quant.ScaleLength() != 1 or quant.ZeroPointLength() != 1:
        raise ValueError("Fixture input and output require per-tensor quantization")
    return Int8Tensor(
        shape=tuple(tensor.Shape(i) for i in range(tensor.ShapeLength())),
        scale=float(quant.Scale(0)),
        zero_point=int(quant.ZeroPoint(0)),
        tensor_index=index,
    )


def analyze_fixture_model(path: Path) -> FixtureModelAnalysis:
    """Read a static single-subgraph model without running an interpreter."""
    schema = model_analysis._schema
    if schema is None:
        raise ValueError("Fixture model analysis requires ai-edge-litert (the analysis extra)")
    data = path.read_bytes()
    if len(data) < 8 or data[4:8] != b"TFL3":
        raise ValueError("Invalid fixture model: expected a TFL3 flatbuffer")
    try:
        model = schema.Model.GetRootAs(data, 0)
        if model.Version() != 3:
            raise ValueError("Unsupported fixture model schema version")
        if model.SubgraphsLength() != 1:
            raise ValueError("Fixture model requires exactly one subgraph")
        graph = model.Subgraphs(0)
        if graph.InputsLength() != 1 or graph.OutputsLength() != 1:
            raise ValueError("Fixture model requires exactly one input and one output")
        for index in range(graph.TensorsLength()):
            tensor = graph.Tensors(index)
            if tensor.IsVariable() or tensor.Type() in (
                schema.TensorType.RESOURCE,
                schema.TensorType.VARIANT,
            ):
                raise ValueError("Variable or stateful fixture tensors are unsupported")
            shape = tuple(tensor.Shape(i) for i in range(tensor.ShapeLength()))
            signature = tuple(
                tensor.ShapeSignature(i) for i in range(tensor.ShapeSignatureLength())
            )
            # Fixed fixtures never resize tensors. A symbolic batch declaration
            # may describe the concrete stored batch-one allocation.
            fixed_batch_signature = bool(shape) and shape[0] == 1 and signature == (-1, *shape[1:])
            if any(n <= 0 for n in shape) or (
                signature and signature != shape and not fixed_batch_signature
            ):
                raise ValueError("Dynamic fixture tensor shapes are unsupported")
            if tensor.Buffer() >= model.BuffersLength():
                raise ValueError("Invalid fixture tensor buffer index")
            buffer = model.Buffers(tensor.Buffer())
            if buffer.DataLength():
                buffer.Data(buffer.DataLength() - 1)
        for index in (graph.Inputs(0), graph.Outputs(0)):
            if not 0 <= index < graph.TensorsLength():
                raise ValueError("Invalid fixture input/output tensor index")
        for index in range(graph.OperatorsLength()):
            op = graph.Operators(index)
            if not 0 <= op.OpcodeIndex() < model.OperatorCodesLength():
                raise ValueError("Invalid fixture operator code index")
            for position in range(op.MutatingVariableInputsLength()):
                if op.MutatingVariableInputs(position):
                    raise ValueError("Stateful fixture operators are unsupported")
            for getter, length, optional in (
                (op.Inputs, op.InputsLength(), True),
                (op.Outputs, op.OutputsLength(), False),
                (op.Intermediates, op.IntermediatesLength(), True),
            ):
                for position in range(length):
                    value = getter(position)
                    if not (0 <= value < graph.TensorsLength() or (optional and value == -1)):
                        raise ValueError("Invalid fixture operator tensor index")
        input_tensor = _io_tensor(graph, graph.Inputs(0), schema.TensorType.INT8)
        output_tensor = _io_tensor(graph, graph.Outputs(0), schema.TensorType.INT8)
        analysis = model_analysis.analyze_model(path)
        if analysis is None:
            raise ValueError("Fixture model analysis unavailable")
        resolver = build_fixture_resolver_plan(analysis)
        return FixtureModelAnalysis(input_tensor, output_tensor, analysis, resolver)
    except ValueError:
        raise
    except (IndexError, TypeError, AttributeError, OverflowError, RuntimeError) as exc:
        raise ValueError(f"Malformed fixture model flatbuffer: {exc}") from exc
    except Exception as exc:
        # Flatbuffers raises struct.error and NumPy errors for truncated vectors.
        raise ValueError(f"Malformed fixture model flatbuffer: {exc}") from exc
