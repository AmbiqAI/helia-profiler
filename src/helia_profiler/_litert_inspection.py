"""LiteRT container inspection shared by fixture engine integrations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .firmware.op_resolver import FIXTURE_STATEFUL_OPERATORS
from .modelcost import model_analysis
from .modelcost.model_analysis import ModelAnalysis


@dataclass(frozen=True)
class LiteRTGraphFacts:
    has_internal_state: bool
    has_stateful_tensors: bool
    has_mutating_inputs: bool
    has_initializer: bool
    subgraph_count: int
    resource_variable_count: int
    operator_names: tuple[str, ...]
    has_float16: bool
    has_dynamic_shapes: bool
    has_dynamic_io: bool


@dataclass(frozen=True)
class LiteRTInspection:
    schema: Any
    graph: Any
    analysis: ModelAnalysis
    facts: LiteRTGraphFacts


def inspect_litert_model(path: Path) -> LiteRTInspection:
    """Read container validity and graph facts without engine admission."""
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
        graph = model.Subgraphs(0)
        has_dynamic_shapes = False
        has_dynamic_io = False
        has_stateful_tensors = False
        has_mutating_inputs = False
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
                has_stateful_tensors |= stateful
                has_internal_state |= stateful
                has_float16 |= tensor.Type() == schema.TensorType.FLOAT16
                shape = tuple(tensor.Shape(i) for i in range(tensor.ShapeLength()))
                signature = tuple(
                    tensor.ShapeSignature(i) for i in range(tensor.ShapeSignatureLength())
                )
                fixed_batch_signature = (
                    bool(shape) and shape[0] == 1 and signature == (-1, *shape[1:])
                )
                dynamic = any(n <= 0 for n in shape) or bool(
                    signature and signature != shape and not fixed_batch_signature
                )
                has_dynamic_shapes |= dynamic
                has_dynamic_io |= dynamic and subgraph == 0 and index in io_indices
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
                        has_mutating_inputs = True
                        has_internal_state = True
                for getter, length, optional in (
                    (op.Inputs, op.InputsLength(), True),
                    (op.Outputs, op.OutputsLength(), False),
                    (op.Intermediates, op.IntermediatesLength(), True),
                ):
                    for position in range(length):
                        value = getter(position)
                        if not (0 <= value < current.TensorsLength() or (optional and value == -1)):
                            raise ValueError("Invalid fixture operator tensor index")
        facts = LiteRTGraphFacts(
            has_internal_state=has_internal_state or has_initializer,
            has_stateful_tensors=has_stateful_tensors,
            has_mutating_inputs=has_mutating_inputs,
            has_initializer=has_initializer,
            subgraph_count=model.SubgraphsLength(),
            resource_variable_count=resource_variable_count,
            operator_names=tuple(sorted(operator_names)),
            has_float16=has_float16,
            has_dynamic_shapes=has_dynamic_shapes,
            has_dynamic_io=has_dynamic_io,
        )
        analysis = model_analysis._analyze_parsed_model(model)
        if analysis is None:
            raise ValueError("Fixture model analysis unavailable")
        return LiteRTInspection(schema, graph, analysis, facts)
    except ValueError:
        raise
    except (IndexError, TypeError, AttributeError, OverflowError, RuntimeError) as exc:
        raise ValueError(f"Malformed fixture model flatbuffer: {exc}") from exc
    except Exception as exc:
        # Flatbuffers raises struct.error and NumPy errors for truncated vectors.
        raise ValueError(f"Malformed fixture model flatbuffer: {exc}") from exc
