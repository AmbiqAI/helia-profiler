"""Bounded, static model metadata for fixed INT8 inference fixtures."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .engines import EngineType
from .firmware.op_resolver import (
    FIXTURE_STATEFUL_OPERATORS,
    ResolverPlan,
    build_fixture_resolver_plan,
)
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
FIXTURE_DTYPE_BYTES = {"int8": 1, "int16": 2, "int32": 4, "float16": 2, "float32": 4}
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
            raise ValueError("Unquantized fixture tensors carry no quantization")

    @property
    def size_bytes(self) -> int:
        return math.prod(self.shape) * FIXTURE_DTYPE_BYTES[self.dtype]


@dataclass(frozen=True)
class FixtureModelAnalysis:
    input_tensor: Int8Tensor
    output_tensor: Int8Tensor
    analysis: ModelAnalysis
    resolver: ResolverPlan
    has_internal_state: bool = False
    has_initializer: bool = False
    subgraph_count: int = 1
    resource_variable_count: int = 0
    has_float16: bool = False

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
    has_internal_state: bool = False
    has_initializer: bool = False
    subgraph_count: int = 1
    resource_variable_count: int = 0

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


def _read_fixture_graph(path: Path, *, single_io: bool, finish, engine: EngineType | None = None):
    """Validate the container and selected public IO before engine admission."""
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
        if model.SubgraphsLength() < 1:
            raise ValueError("Fixture model requires at least one subgraph")
        if engine is None and model.SubgraphsLength() != 1:
            raise ValueError("Fixture model requires exactly one subgraph")
        graph = model.Subgraphs(0)
        if single_io and (graph.InputsLength() != 1 or graph.OutputsLength() != 1):
            raise ValueError("Fixture model requires exactly one input and one output")
        if not single_io and (graph.InputsLength() < 1 or graph.OutputsLength() < 1):
            raise ValueError("Fixture model requires at least one input and one output")
        has_internal_state = False
        has_initializer = False
        has_float16 = False
        resource_variable_count = 0
        operator_names: set[str] = set()
        for subgraph in range(model.SubgraphsLength()):
            current = model.Subgraphs(subgraph)
            io_indices = [current.Inputs(i) for i in range(current.InputsLength())] + [
                current.Outputs(i) for i in range(current.OutputsLength())
            ]
            for index in io_indices:
                if not 0 <= index < current.TensorsLength():
                    raise ValueError("Invalid fixture input/output tensor index")
            for index in range(current.TensorsLength()):
                tensor = current.Tensors(index)
                stateful = tensor.IsVariable() or tensor.Type() in (
                    schema.TensorType.RESOURCE,
                    schema.TensorType.VARIANT,
                )
                has_internal_state |= stateful
                has_float16 |= tensor.Type() == schema.TensorType.FLOAT16
                if engine is None and stateful:
                    raise ValueError("Variable or stateful fixture tensors are unsupported")
                shape = tuple(tensor.Shape(i) for i in range(tensor.ShapeLength()))
                signature = tuple(
                    tensor.ShapeSignature(i) for i in range(tensor.ShapeSignatureLength())
                )
                fixed_batch_signature = (
                    bool(shape) and shape[0] == 1 and signature == (-1, *shape[1:])
                )
                if (engine is None or (subgraph == 0 and index in io_indices)) and (
                    any(n <= 0 for n in shape)
                    or (signature and signature != shape and not fixed_batch_signature)
                ):
                    raise ValueError("Dynamic fixture tensor shapes are unsupported")
                if tensor.Buffer() >= model.BuffersLength():
                    raise ValueError("Invalid fixture tensor buffer index")
                buffer = model.Buffers(tensor.Buffer())
                if buffer.DataLength():
                    buffer.Data(buffer.DataLength() - 1)
            for index in range(current.OperatorsLength()):
                op = current.Operators(index)
                if not 0 <= op.OpcodeIndex() < model.OperatorCodesLength():
                    raise ValueError("Invalid fixture operator code index")
                code = model.OperatorCodes(op.OpcodeIndex())
                builtin = code.BuiltinCode() or code.DeprecatedBuiltinCode()
                name = model_analysis._op_name(builtin)
                operator_names.add(name)
                has_internal_state |= name in FIXTURE_STATEFUL_OPERATORS
                has_initializer |= builtin == schema.BuiltinOperator.CALL_ONCE
                resource_variable_count += builtin == schema.BuiltinOperator.VAR_HANDLE
                for position in range(op.MutatingVariableInputsLength()):
                    if op.MutatingVariableInputs(position):
                        has_internal_state = True
                        if engine is None:
                            raise ValueError("Stateful fixture operators are unsupported")
                for getter, length, optional in (
                    (op.Inputs, op.InputsLength(), True),
                    (op.Outputs, op.OutputsLength(), False),
                    (op.Intermediates, op.IntermediatesLength(), True),
                ):
                    for position in range(length):
                        value = getter(position)
                        if not (0 <= value < current.TensorsLength() or (optional and value == -1)):
                            raise ValueError("Invalid fixture operator tensor index")
        facts = (
            has_internal_state or has_initializer,
            has_initializer,
            model.SubgraphsLength(),
            resource_variable_count,
            tuple(sorted(operator_names)),
            has_float16,
        )
        analysis = model_analysis.analyze_model(path)
        if analysis is None:
            raise ValueError("Fixture model analysis unavailable")
        return finish(schema, graph, analysis, facts)
    except ValueError:
        raise
    except (IndexError, TypeError, AttributeError, OverflowError, RuntimeError) as exc:
        raise ValueError(f"Malformed fixture model flatbuffer: {exc}") from exc
    except Exception as exc:
        # Flatbuffers raises struct.error and NumPy errors for truncated vectors.
        raise ValueError(f"Malformed fixture model flatbuffer: {exc}") from exc


def analyze_fixture_model(path: Path, *, _engine: EngineType | None = None) -> FixtureModelAnalysis:
    """Read a static single-subgraph model without running an interpreter."""

    def finish(schema, graph, analysis, facts):
        input_tensor = _io_tensor(graph, graph.Inputs(0), schema.TensorType.INT8)
        output_tensor = _io_tensor(graph, graph.Outputs(0), schema.TensorType.INT8)
        resolver = _engine_resolver(analysis, _engine, facts)
        return FixtureModelAnalysis(
            input_tensor,
            output_tensor,
            analysis,
            resolver,
            has_internal_state=facts[0],
            has_initializer=facts[1],
            subgraph_count=facts[2],
            resource_variable_count=facts[3],
            has_float16=facts[5],
        )

    return _read_fixture_graph(path, single_io=True, finish=finish, engine=_engine)


def _analyze_typed_fixture_model(
    path: Path, engine: EngineType | None
) -> TypedFixtureModelAnalysis:
    """Read every input and output of a static single-subgraph model with its dtype."""

    def finish(schema, graph, analysis, facts):
        dtypes = {
            schema.TensorType.INT8: "int8",
            schema.TensorType.INT16: "int16",
            schema.TensorType.INT32: "int32",
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
                if scales or (dtype == "int32" and quant is not None and quant.ZeroPointLength()):
                    raise ValueError("Unquantized fixture IO carries quantization")
                if (
                    dtype == "int32"
                    and quant is not None
                    and (
                        quant.DetailsType() != schema.QuantizationDetails.NONE
                        or quant.Details() is not None
                    )
                ):
                    raise ValueError("INT32 fixture IO carries unsupported quantization details")
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
            facts[5],
            analysis,
            _engine_resolver(analysis, engine, facts),
            has_internal_state=facts[0],
            has_initializer=facts[1],
            subgraph_count=facts[2],
            resource_variable_count=facts[3],
        )

    return _read_fixture_graph(path, single_io=False, finish=finish, engine=engine)


def _engine_resolver(analysis: ModelAnalysis, engine: EngineType | None, facts) -> ResolverPlan:
    if engine is EngineType.HELIA_AOT:
        return ResolverPlan(mode="engine", registrations=())
    if engine is EngineType.HELIA_RT and (facts[0] or facts[2] != 1):
        raise ValueError("helia-rt fixture resource/multiple-subgraph integration is unavailable")
    return build_fixture_resolver_plan(
        analysis, allow_state=engine is EngineType.TFLM, additional_ops=facts[4]
    )


def analyze_typed_fixture_model(path: Path) -> TypedFixtureModelAnalysis:
    """Read a model through the legacy stateless fixture admission contract."""
    return _analyze_typed_fixture_model(path, None)


def analyze_typed_fixture_model_for_engine(
    path: Path, *, engine: EngineType
) -> TypedFixtureModelAnalysis:
    """Validate selected public IO and delegate internal compatibility to the engine."""
    if not isinstance(engine, EngineType) or engine not in (
        EngineType.HELIA_AOT,
        EngineType.TFLM,
        EngineType.HELIA_RT,
    ):
        raise ValueError("Explicit supported fixture engine required")
    return _analyze_typed_fixture_model(path, engine)
