"""Bounded, static model metadata for fixed INT8 inference fixtures."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .._litert_inspection import LiteRTGraphFacts, LiteRTInspection, inspect_litert_model
from ..engines import EngineType, get_adapter
from ..firmware.op_resolver import ResolverPlan, build_fixture_resolver_plan
from ..modelcost.model_analysis import ModelAnalysis


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
    """A model's public inputs and outputs in model order, with its graph facts."""

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


def _single_io_analysis(
    inspected: LiteRTInspection, resolver: ResolverPlan
) -> FixtureModelAnalysis:
    schema, graph = inspected.schema, inspected.graph
    analysis, facts = inspected.analysis, inspected.facts
    input_tensor = _io_tensor(graph, graph.Inputs(0), schema.TensorType.INT8)
    output_tensor = _io_tensor(graph, graph.Outputs(0), schema.TensorType.INT8)
    return FixtureModelAnalysis(
        input_tensor=input_tensor,
        output_tensor=output_tensor,
        analysis=analysis,
        resolver=resolver,
        has_internal_state=facts.has_internal_state,
        has_initializer=facts.has_initializer,
        subgraph_count=facts.subgraph_count,
        resource_variable_count=facts.resource_variable_count,
        has_float16=facts.has_float16,
    )


def _typed_analysis(
    inspected: LiteRTInspection, resolver: ResolverPlan
) -> TypedFixtureModelAnalysis:
    schema, graph = inspected.schema, inspected.graph
    analysis, facts = inspected.analysis, inspected.facts
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
        inputs=tuple(tensor(graph.Inputs(i)) for i in range(graph.InputsLength())),
        outputs=tuple(tensor(graph.Outputs(i)) for i in range(graph.OutputsLength())),
        has_float16=facts.has_float16,
        analysis=analysis,
        resolver=resolver,
        has_internal_state=facts.has_internal_state,
        has_initializer=facts.has_initializer,
        subgraph_count=facts.subgraph_count,
        resource_variable_count=facts.resource_variable_count,
    )


def prepare_litert_fixture(
    path: Path,
    *,
    single_io: bool,
    resolver: Callable[[ModelAnalysis, LiteRTGraphFacts], ResolverPlan],
) -> FixtureModelAnalysis | TypedFixtureModelAnalysis:
    """Project inspected LiteRT facts into the existing fixture IO contract.

    Only the fixed-size public IO is a fixture constraint here; what the graph
    may contain is the selected engine's decision, made by *resolver*.
    """
    inspected = inspect_litert_model(path)
    facts, graph = inspected.facts, inspected.graph
    if single_io and (graph.InputsLength() != 1 or graph.OutputsLength() != 1):
        raise ValueError("Fixture model requires exactly one input and one output")
    if not single_io and (graph.InputsLength() < 1 or graph.OutputsLength() < 1):
        raise ValueError("Fixture model requires at least one input and one output")
    if facts.has_dynamic_io:
        raise ValueError("Dynamic fixture tensor shapes are unsupported")
    plan = resolver(inspected.analysis, facts)
    try:
        return (
            _single_io_analysis(inspected, plan) if single_io else _typed_analysis(inspected, plan)
        )
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Malformed fixture model flatbuffer: {exc}") from exc


def litert_fixture_resolver(analysis: ModelAnalysis, facts: LiteRTGraphFacts) -> ResolverPlan:
    """Register every graph operator with a TFLM-family kernel, including stateful ones."""
    return build_fixture_resolver_plan(
        analysis, allow_state=True, additional_ops=facts.operator_names
    )


def analyze_fixture_model(path: Path, *, _engine: EngineType | None = None) -> FixtureModelAnalysis:
    """Read a single-IO LiteRT fixture, admitted by the selected engine when one is given."""
    result = (
        prepare_litert_fixture(path, single_io=True, resolver=litert_fixture_resolver)
        if _engine is None
        else get_adapter(_engine).prepare_fixture_model(path, single_io=True)
    )
    if not isinstance(result, FixtureModelAnalysis):
        raise ValueError("Single-IO LiteRT fixture analysis required")
    return result


def analyze_typed_fixture_model(path: Path) -> TypedFixtureModelAnalysis:
    """Read a model's fixture IO and facts without selecting an engine."""
    result = prepare_litert_fixture(path, single_io=False, resolver=litert_fixture_resolver)
    assert isinstance(result, TypedFixtureModelAnalysis)
    return result


def analyze_typed_fixture_model_for_engine(
    path: Path, *, engine: EngineType
) -> TypedFixtureModelAnalysis:
    """Validate selected public IO through the engine's LiteRT fixture integration."""
    if not isinstance(engine, EngineType):
        raise ValueError("Explicit supported fixture engine required")
    result = get_adapter(engine).prepare_fixture_model(path, single_io=False)
    if not isinstance(result, TypedFixtureModelAnalysis):
        raise ValueError("Typed LiteRT fixture analysis required")
    return result
