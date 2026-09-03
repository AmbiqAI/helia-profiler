"""Minimal, dependency-free reader for the slice of TFLite this package gates on.

``ai-edge-litert`` is an OPTIONAL extra, absent from a plain helia-rt install
and from the CI unit-test environment -- but the Softmax preflight (#57) must
run everywhere a ``.tflite`` can be flashed, because the failure it prevents
is an on-target abort inside ``AllocateTensors()``. A check that silently
skips on the installs most likely to hit the bug is not a check.

The preflight needs exactly two numbers per Softmax op (the input tensor's
quantization scale and the op's beta), and the flatbuffers wire format is
frozen and small enough to read directly:

* every table starts with an int32 offset to its vtable (subtracted, not
  added); the vtable is ``[u16 vtable_size, u16 table_size, u16 field_offset
  per field id]``, where a zero field offset means "absent, use the default";
* references (tables, vectors, strings) are u32 offsets relative to their own
  location; vectors are ``[u32 length, elements...]``.

Field slots below are not hand-remembered: they were extracted from
``ai_edge_litert.schema_py_generated``'s own accessors, and
tests/test_softmax_preflight.py cross-validates this reader against litert on
every ``.tflite`` fixture in the repo whenever litert is installed -- so a
drift between the two fails a test rather than silently diverging. Schema
evolution cannot move them: flatbuffers appends new fields to the end of the
vtable precisely so existing slots stay fixed.

This is a reader for two narrow questions -- the Softmax scaling gate and which
float precisions a model computes in (#246). Anything needing real model
analysis (shapes, MACs, weights) should use ``model_analysis``'s litert path,
not grow this file.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# PROVENANCE (#229 D7): every constant below was extracted from
# ai_edge_litert 2.1.6's generated schema (schema_py_generated), re-verified
# unchanged against 2.2.0 (#246), and is frozen by flatbuffers
# schema-evolution rules (enum values immutable, vtable slots append-only).
# tests/test_softmax_preflight.py re-derives each one from the installed
# litert by introspection whenever the
# `analysis` extra is present, so silent drift is impossible.
#
# BuiltinOperator / TensorType / BuiltinOptions enum values:
BUILTIN_DEQUANTIZE = 6
BUILTIN_SOFTMAX = 25
BUILTIN_QUANTIZE = 114
TENSOR_TYPE_FLOAT32 = 0
TENSOR_TYPE_FLOAT16 = 1
TENSOR_TYPE_UINT8 = 3
TENSOR_TYPE_INT8 = 9
BUILTIN_OPTIONS_SOFTMAX = 9

# vtable slot = 4 + 2 * field_id, as extracted from the generated accessors.
_MODEL_OPERATOR_CODES = 6
_MODEL_SUBGRAPHS = 8
_OPCODE_DEPRECATED_BUILTIN = 4
_OPCODE_BUILTIN = 10
_SUBGRAPH_TENSORS = 4
_SUBGRAPH_OPERATORS = 10
_OPERATOR_OPCODE_INDEX = 4
_OPERATOR_INPUTS = 6
_OPERATOR_OPTIONS_TYPE = 10
_OPERATOR_OPTIONS = 12
_TENSOR_TYPE = 6
_TENSOR_NAME = 10
_TENSOR_QUANTIZATION = 12
_QUANT_SCALE = 8
_SOFTMAX_BETA = 4


class _Table:
    """One flatbuffers table: field lookups against its vtable."""

    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytes, pos: int):
        self.buf = buf
        self.pos = pos

    def _field_pos(self, slot: int) -> int | None:
        """Absolute position of a field's value, or None when absent."""
        vtable = self.pos - struct.unpack_from("<i", self.buf, self.pos)[0]
        vtable_size = struct.unpack_from("<H", self.buf, vtable)[0]
        if slot >= vtable_size:
            return None
        offset = struct.unpack_from("<H", self.buf, vtable + slot)[0]
        return self.pos + offset if offset else None

    def scalar(self, slot: int, fmt: str, default):
        pos = self._field_pos(slot)
        if pos is None:
            return default
        return struct.unpack_from(fmt, self.buf, pos)[0]

    def _indirect(self, pos: int) -> int:
        return pos + struct.unpack_from("<I", self.buf, pos)[0]

    def table(self, slot: int) -> "_Table | None":
        pos = self._field_pos(slot)
        if pos is None:
            return None
        return _Table(self.buf, self._indirect(pos))

    def vector(self, slot: int) -> tuple[int, int]:
        """(element-0 position, length); (0, 0) when absent."""
        pos = self._field_pos(slot)
        if pos is None:
            return 0, 0
        vec = self._indirect(pos)
        length = struct.unpack_from("<I", self.buf, vec)[0]
        return vec + 4, length

    def table_vector(self, slot: int) -> list["_Table"]:
        start, length = self.vector(slot)
        return [
            _Table(self.buf, self._indirect(start + 4 * i)) for i in range(length)
        ]

    def string(self, slot: int) -> str | None:
        pos = self._field_pos(slot)
        if pos is None:
            return None
        s = self._indirect(pos)
        length = struct.unpack_from("<I", self.buf, s)[0]
        return self.buf[s + 4 : s + 4 + length].decode("utf-8", "replace")


def _builtin_code(opcode: _Table) -> int:
    builtin = opcode.scalar(_OPCODE_BUILTIN, "<i", 0)
    if builtin == 0:
        # Pre-schema-v3a files store the enum only in the deprecated int8
        # field; BuiltinCode() keeps its ADD(0) placeholder.
        builtin = opcode.scalar(_OPCODE_DEPRECATED_BUILTIN, "<b", 0)
    return builtin


def read_float_compute_types(buf: bytes) -> set[int]:
    """Float tensor types a kernel reads: the float precisions the target works in.

    A FLOAT32 input to ``QUANTIZE``/``DEQUANTIZE`` is skipped -- an int8 model
    with float I/O only casts at its edges and exercises no float kernel. A
    FLOAT16 input to ``DEQUANTIZE`` counts: widening float16 weights is
    float16 work on the target. Raises like :func:`read_quantized_softmax_ops`
    on a malformed buffer.
    """
    model = _Table(buf, struct.unpack_from("<I", buf, 0)[0])
    opcodes = model.table_vector(_MODEL_OPERATOR_CODES)
    found: set[int] = set()
    for sg in model.table_vector(_MODEL_SUBGRAPHS):
        tensors = sg.table_vector(_SUBGRAPH_TENSORS)
        for op in sg.table_vector(_SUBGRAPH_OPERATORS):
            builtin = _builtin_code(opcodes[op.scalar(_OPERATOR_OPCODE_INDEX, "<I", 0)])
            is_cast = builtin in (BUILTIN_QUANTIZE, BUILTIN_DEQUANTIZE)
            start, length = op.vector(_OPERATOR_INPUTS)
            for i in range(length):
                index = struct.unpack_from("<i", op.buf, start + 4 * i)[0]
                if index < 0:  # -1 marks an absent optional input
                    continue
                tensor_type = tensors[index].scalar(_TENSOR_TYPE, "<b", 0)
                if tensor_type == TENSOR_TYPE_FLOAT16 or (
                    tensor_type == TENSOR_TYPE_FLOAT32 and not is_cast
                ):
                    found.add(tensor_type)
    return found


@dataclass(frozen=True)
class SoftmaxOp:
    """The slice of one quantized Softmax the preflight needs."""

    subgraph_index: int
    op_index: int
    input_tensor: str
    input_type: int
    input_scale: float | None
    #: TFLM zero-initialises builtin data, so an absent options table (or an
    #: options table whose beta was left at the schema default) reaches the
    #: kernel as 0.0 -- reading it any other way here would pass a model the
    #: target aborts on.
    beta: float


def read_quantized_softmax_ops(buf: bytes) -> list[SoftmaxOp]:
    """Every int8/uint8 Softmax in the model, with scale and beta.

    Raises ``struct.error`` / ``IndexError`` on a malformed buffer; callers
    gate on the preflight's existing header check having already accepted the
    file as a TFLite flatbuffer.
    """
    model = _Table(buf, struct.unpack_from("<I", buf, 0)[0])
    opcodes = model.table_vector(_MODEL_OPERATOR_CODES)
    found: list[SoftmaxOp] = []

    for sg_index, sg in enumerate(model.table_vector(_MODEL_SUBGRAPHS)):
        tensors = sg.table_vector(_SUBGRAPH_TENSORS)
        for op_index, op in enumerate(sg.table_vector(_SUBGRAPH_OPERATORS)):
            builtin = _builtin_code(opcodes[op.scalar(_OPERATOR_OPCODE_INDEX, "<I", 0)])
            if builtin != BUILTIN_SOFTMAX:
                continue

            inputs_start, inputs_len = op.vector(_OPERATOR_INPUTS)
            if not inputs_len:
                continue
            tensor_index = struct.unpack_from("<i", op.buf, inputs_start)[0]
            if tensor_index < 0:
                # TFLite uses -1 for an absent optional input; a bare Python
                # index would silently read the LAST tensor instead.
                continue
            tensor = tensors[tensor_index]
            tensor_type = tensor.scalar(_TENSOR_TYPE, "<b", 0)
            if tensor_type not in (TENSOR_TYPE_INT8, TENSOR_TYPE_UINT8):
                continue

            scale: float | None = None
            quant = tensor.table(_TENSOR_QUANTIZATION)
            if quant is not None:
                scale_start, scale_len = quant.vector(_QUANT_SCALE)
                if scale_len:
                    scale = struct.unpack_from("<f", quant.buf, scale_start)[0]

            beta = 0.0
            if op.scalar(_OPERATOR_OPTIONS_TYPE, "<B", 0) == BUILTIN_OPTIONS_SOFTMAX:
                options = op.table(_OPERATOR_OPTIONS)
                if options is not None:
                    beta = options.scalar(_SOFTMAX_BETA, "<f", 0.0)

            name = tensor.string(_TENSOR_NAME)
            found.append(
                SoftmaxOp(
                    subgraph_index=sg_index,
                    op_index=op_index,
                    # Placeholder only when the field is ABSENT; an empty
                    # string is a real (if useless) name and stays '' -- `or`
                    # conflated the two, diverging from litert on 12 of 8,424
                    # fuzz mutants (cosmetic, but the oracle should agree).
                    input_tensor=name if name is not None else f"tensor_{tensor_index}",
                    input_type=tensor_type,
                    input_scale=scale,
                    beta=beta,
                )
            )
    return found
