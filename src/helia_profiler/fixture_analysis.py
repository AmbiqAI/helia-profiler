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


#: Fixture IO dtypes and their element width in bytes.
FIXTURE_DTYPE_BYTES = {"int8": 1, "int16": 2, "float16": 2, "float32": 4}
_ZERO_POINT_RANGE = {"int8": (-128, 127), "int16": (-32768, 32767)}


@dataclass(frozen=True)
class PerTensorQuantization:
    scale: float
    zero_point: int


@dataclass(frozen=True)
class PerAxisQuantization:
    axis: int
    scales: tuple[float, ...]
    zero_points: tuple[int, ...]


@dataclass(frozen=True)
class FixtureTensor:
    """One named model input or output with its dtype, shape and quantization."""

    name: str
    index: int
    dtype: str
    shape: tuple[int, ...]
    quantization: PerTensorQuantization | PerAxisQuantization | None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("Named fixture tensor required")
        if type(self.index) is not int or self.index < 0:
            raise ValueError("Nonnegative tensor index required")
        if self.dtype not in FIXTURE_DTYPE_BYTES:
            raise ValueError(f"Unsupported fixture tensor dtype: {self.dtype}")
        if not self.shape or any(type(n) is not int or n <= 0 for n in self.shape):
            raise ValueError("Positive fixed tensor dimensions required")
        quant = self.quantization
        if self.dtype in _ZERO_POINT_RANGE:
            low, high = _ZERO_POINT_RANGE[self.dtype]
            if isinstance(quant, PerTensorQuantization):
                scales, zero_points = (quant.scale,), (quant.zero_point,)
            elif isinstance(quant, PerAxisQuantization):
                if (
                    not 0 <= quant.axis < len(self.shape)
                    or len(quant.scales) != self.shape[quant.axis]
                    or len(quant.zero_points) != len(quant.scales)
                ):
                    raise ValueError("Per-axis quantization does not match the tensor shape")
                scales, zero_points = quant.scales, quant.zero_points
            else:
                raise ValueError("Integer fixture tensors require quantization")
            if any(not math.isfinite(scale) or scale <= 0 for scale in scales):
                raise ValueError("Positive finite quantization scale required")
            if any(type(zp) is not int or not low <= zp <= high for zp in zero_points):
                raise ValueError(f"{self.dtype} zero point out of range")
        elif quant is not None:
            raise ValueError("Float fixture tensors carry no quantization")

    @property
    def size_bytes(self) -> int:
        return math.prod(self.shape) * FIXTURE_DTYPE_BYTES[self.dtype]


@dataclass(frozen=True)
class FixtureModelAnalysis:
    input_tensor: Int8Tensor
    output_tensor: Int8Tensor
    analysis: ModelAnalysis
    resolver: ResolverPlan

    @property
    def input_bytes(self) -> int:
        return self.input_tensor.size

    @property
    def output_bytes(self) -> int:
        return self.output_tensor.size


@dataclass(frozen=True)
class TypedFixtureModelAnalysis:
    inputs: tuple[FixtureTensor, ...]
    outputs: tuple[FixtureTensor, ...]
    #: Any FLOAT16 tensor in the graph, including weights behind DEQUANTIZE.
    has_float16: bool
    analysis: ModelAnalysis
    resolver: ResolverPlan

    @property
    def input_bytes(self) -> int:
        return sum(tensor.size_bytes for tensor in self.inputs)

    @property
    def output_bytes(self) -> int:
        return sum(tensor.size_bytes for tensor in self.outputs)


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


def _read_fixture_graph(path: Path, *, single_io: bool, finish):
    """Validate a static single-subgraph model, then hand the graph to ``finish``."""
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
        if single_io and (graph.InputsLength() != 1 or graph.OutputsLength() != 1):
            raise ValueError("Fixture model requires exactly one input and one output")
        if not single_io and (graph.InputsLength() < 1 or graph.OutputsLength() < 1):
            raise ValueError("Fixture model requires at least one input and one output")
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
        for index in [graph.Inputs(i) for i in range(graph.InputsLength())] + [
            graph.Outputs(i) for i in range(graph.OutputsLength())
        ]:
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
        analysis = model_analysis.analyze_model(path)
        if analysis is None:
            raise ValueError("Fixture model analysis unavailable")
        return finish(schema, graph, analysis)
    except ValueError:
        raise
    except (IndexError, TypeError, AttributeError, OverflowError, RuntimeError) as exc:
        raise ValueError(f"Malformed fixture model flatbuffer: {exc}") from exc
    except Exception as exc:
        # Flatbuffers raises struct.error and NumPy errors for truncated vectors.
        raise ValueError(f"Malformed fixture model flatbuffer: {exc}") from exc


def analyze_fixture_model(path: Path) -> FixtureModelAnalysis:
    """Read a static single-subgraph model without running an interpreter."""

    def finish(schema, graph, analysis):
        input_tensor = _io_tensor(graph, graph.Inputs(0), schema.TensorType.INT8)
        output_tensor = _io_tensor(graph, graph.Outputs(0), schema.TensorType.INT8)
        resolver = build_fixture_resolver_plan(analysis)
        return FixtureModelAnalysis(input_tensor, output_tensor, analysis, resolver)

    return _read_fixture_graph(path, single_io=True, finish=finish)


def analyze_typed_fixture_model(path: Path) -> TypedFixtureModelAnalysis:
    """Read every input and output of a static single-subgraph model with its dtype."""

    def finish(schema, graph, analysis):
        dtypes = {
            schema.TensorType.INT8: "int8",
            schema.TensorType.INT16: "int16",
            schema.TensorType.FLOAT16: "float16",
            schema.TensorType.FLOAT32: "float32",
        }

        def tensor(index: int) -> FixtureTensor:
            raw = graph.Tensors(index)
            dtype = dtypes.get(raw.Type())
            if dtype is None:
                raise ValueError(f"Unsupported fixture IO tensor type {raw.Type()}")
            quant = raw.Quantization()
            scales = quant.ScaleLength() if quant is not None else 0
            if dtype in _ZERO_POINT_RANGE:
                if scales == 0 or quant.ZeroPointLength() != scales:
                    raise ValueError("Integer fixture IO requires quantization")
                quantization = (
                    PerTensorQuantization(float(quant.Scale(0)), int(quant.ZeroPoint(0)))
                    if scales == 1
                    else PerAxisQuantization(
                        int(quant.QuantizedDimension()),
                        tuple(float(quant.Scale(i)) for i in range(scales)),
                        tuple(int(quant.ZeroPoint(i)) for i in range(scales)),
                    )
                )
            else:
                if scales:
                    raise ValueError("Float fixture IO carries quantization")
                quantization = None
            name = raw.Name()
            return FixtureTensor(
                name.decode() if isinstance(name, bytes) else str(name or ""),
                index,
                dtype,
                tuple(raw.Shape(i) for i in range(raw.ShapeLength())),
                quantization,
            )

        return TypedFixtureModelAnalysis(
            tuple(tensor(graph.Inputs(i)) for i in range(graph.InputsLength())),
            tuple(tensor(graph.Outputs(i)) for i in range(graph.OutputsLength())),
            any(
                graph.Tensors(i).Type() == schema.TensorType.FLOAT16
                for i in range(graph.TensorsLength())
            ),
            analysis,
            build_fixture_resolver_plan(analysis),
        )

    return _read_fixture_graph(path, single_io=False, finish=finish)
