"""Generate tests/fixtures/fc_int16_per_channel.tflite.

A minimal TFLite flatbuffer with one FullyConnected: int16 input and
output, int8 weights with two per-channel scales. Upstream TFLM's CMSIS-NN
FullyConnected rejects that pairing in Prepare, so LiteRT validation cases
skip it (``validation.matrix.case_validity``).

``input_type`` and ``scales`` let tests build the variants the reader must
pass (int8 input, per-tensor weights) without committing more fixtures.

Regenerate with::

    uv run --extra analysis python tools/gen_fc_per_channel_fixture.py

``tests/test_fc_per_channel.py`` asserts the committed fixture is
byte-identical to ``generate()``.
"""

from __future__ import annotations

from pathlib import Path

FIXTURE = Path(__file__).parent.parent / "tests" / "fixtures" / "fc_int16_per_channel.tflite"


def generate(input_type: int | None = None, scales: int = 2) -> bytes:
    import flatbuffers
    from ai_edge_litert import schema_py_generated as s

    if input_type is None:
        input_type = s.TensorType.INT16
    b = flatbuffers.Builder(1024)
    names = [b.CreateString(n) for n in ("fc_input", "fc_weights", "fc_output")]

    def quant(count: int):
        s.QuantizationParametersStartScaleVector(b, count)
        for _ in range(count):
            b.PrependFloat32(1 / 256)
        scale = b.EndVector()
        s.QuantizationParametersStartZeroPointVector(b, count)
        for _ in range(count):
            b.PrependInt64(0)
        zero_point = b.EndVector()
        s.QuantizationParametersStart(b)
        s.QuantizationParametersAddScale(b, scale)
        s.QuantizationParametersAddZeroPoint(b, zero_point)
        return s.QuantizationParametersEnd(b)

    def tensor(name, ttype, dims, q, buffer=0):
        s.TensorStartShapeVector(b, len(dims))
        for d in reversed(dims):
            b.PrependInt32(d)
        shape = b.EndVector()
        s.TensorStart(b)
        s.TensorAddShape(b, shape)
        s.TensorAddType(b, ttype)
        s.TensorAddName(b, name)
        s.TensorAddQuantization(b, q)
        s.TensorAddBuffer(b, buffer)
        return s.TensorEnd(b)

    tensors = [
        tensor(names[0], input_type, (1, 4), quant(1)),
        tensor(names[1], s.TensorType.INT8, (2, 4), quant(scales), buffer=1),
        tensor(names[2], input_type, (1, 2), quant(1)),
    ]

    s.OperatorStartInputsVector(b, 3)
    # Prepended: inputs read [0, 1, -1].
    for index in (-1, 1, 0):
        b.PrependInt32(index)
    ins = b.EndVector()
    s.OperatorStartOutputsVector(b, 1)
    b.PrependInt32(2)
    outs = b.EndVector()
    s.OperatorStart(b)
    s.OperatorAddOpcodeIndex(b, 0)
    s.OperatorAddInputs(b, ins)
    s.OperatorAddOutputs(b, outs)
    op = s.OperatorEnd(b)

    s.SubGraphStartTensorsVector(b, len(tensors))
    for t in reversed(tensors):
        b.PrependUOffsetTRelative(t)
    tvec = b.EndVector()
    s.SubGraphStartInputsVector(b, 1)
    b.PrependInt32(0)
    sg_in = b.EndVector()
    s.SubGraphStartOutputsVector(b, 1)
    b.PrependInt32(2)
    sg_out = b.EndVector()
    s.SubGraphStartOperatorsVector(b, 1)
    b.PrependUOffsetTRelative(op)
    ovec = b.EndVector()
    s.SubGraphStart(b)
    s.SubGraphAddTensors(b, tvec)
    s.SubGraphAddInputs(b, sg_in)
    s.SubGraphAddOutputs(b, sg_out)
    s.SubGraphAddOperators(b, ovec)
    sg = s.SubGraphEnd(b)

    s.OperatorCodeStart(b)
    s.OperatorCodeAddBuiltinCode(b, s.BuiltinOperator.FULLY_CONNECTED)
    s.OperatorCodeAddDeprecatedBuiltinCode(b, s.BuiltinOperator.FULLY_CONNECTED)
    oc = s.OperatorCodeEnd(b)

    weights = b.CreateByteVector(bytes(8))
    s.BufferStart(b)
    empty = s.BufferEnd(b)
    s.BufferStart(b)
    s.BufferAddData(b, weights)
    filled = s.BufferEnd(b)

    s.ModelStartOperatorCodesVector(b, 1)
    b.PrependUOffsetTRelative(oc)
    ocs = b.EndVector()
    s.ModelStartSubgraphsVector(b, 1)
    b.PrependUOffsetTRelative(sg)
    sgs = b.EndVector()
    s.ModelStartBuffersVector(b, 2)
    b.PrependUOffsetTRelative(filled)
    b.PrependUOffsetTRelative(empty)
    bufs = b.EndVector()

    s.ModelStart(b)
    s.ModelAddVersion(b, 3)
    s.ModelAddOperatorCodes(b, ocs)
    s.ModelAddSubgraphs(b, sgs)
    s.ModelAddBuffers(b, bufs)
    b.Finish(s.ModelEnd(b), file_identifier=b"TFL3")
    return bytes(b.Output())


if __name__ == "__main__":
    data = generate()
    FIXTURE.write_bytes(data)
    print(f"wrote {FIXTURE} ({len(data)} bytes)")
