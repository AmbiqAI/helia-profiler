"""Generate tests/fixtures/softmax_scale_unsupported.tflite (#57).

A minimal, valid TFLite flatbuffer with three quantized Softmax ops, built so
every branch of ``evaluation/_tflite_reader.py`` is load-bearing in a test
environment WITHOUT ai-edge-litert:

* op 0 -- int8, input scale 4.305568790385905e-09 (the exact failing value
  from issue #57; ``beta * scale * 2**26 = 0.2889418303966522``, the
  multiplier the issue quotes), beta=1.0. UNSUPPORTED.
* op 1 -- int8, the same tiny scale, beta=1e9. Supported ONLY if the scanner
  reads beta from the op: a scanner that hardcodes beta=1 flags it and fails
  the tests.
* op 2 -- uint8, healthy scale 0.05. Covers the second quantized type.
* op 3 -- Softmax with ``inputs=[-1]`` (TFLite's absent-optional-input
  marker). The scanner must SKIP it: a bare Python index would silently read
  the LAST tensor instead, reporting an op that does not exist.

The single OperatorCode stores SOFTMAX only in the DEPRECATED builtin field
(``builtin_code`` left at its 0 default), exercising the pre-v3a fallback.

Regenerate with::

    uv run --extra analysis python tools/gen_softmax_preflight_fixture.py

``tests/test_softmax_preflight.py`` asserts the committed fixture is
byte-identical to ``generate()`` (same convention as
``tools/gen_example_model.py``), so a drifted regeneration fails CI's
analysis-tests job rather than silently changing what the tests test.
"""

from __future__ import annotations

from pathlib import Path

FIXTURE = Path(__file__).parent.parent / "tests" / "fixtures" / "softmax_scale_unsupported.tflite"

FAILING_SCALE = 4.305568790385905e-09


def generate() -> bytes:
    import flatbuffers
    from ai_edge_litert import schema_py_generated as s

    b = flatbuffers.Builder(1024)
    names = [
        b.CreateString(n)
        for n in (
            "softmax_input",
            "softmax_output",
            "beta_rescued_input",
            "beta_rescued_output",
            "uint8_input",
            "uint8_output",
        )
    ]

    def quant(scale: float):
        s.QuantizationParametersStartScaleVector(b, 1)
        b.PrependFloat32(scale)
        scales = b.EndVector()
        s.QuantizationParametersStartZeroPointVector(b, 1)
        b.PrependInt64(0)
        zps = b.EndVector()
        s.QuantizationParametersStart(b)
        s.QuantizationParametersAddScale(b, scales)
        s.QuantizationParametersAddZeroPoint(b, zps)
        return s.QuantizationParametersEnd(b)

    def tensor(name, ttype, q):
        s.TensorStartShapeVector(b, 2)
        b.PrependInt32(10)
        b.PrependInt32(1)
        shape = b.EndVector()
        s.TensorStart(b)
        s.TensorAddShape(b, shape)
        s.TensorAddType(b, ttype)
        s.TensorAddName(b, name)
        s.TensorAddQuantization(b, q)
        return s.TensorEnd(b)

    quants = [quant(x) for x in (FAILING_SCALE, 1 / 256, FAILING_SCALE, 1 / 256, 0.05, 1 / 256)]
    types = [s.TensorType.INT8] * 4 + [s.TensorType.UINT8] * 2
    tensors = [tensor(names[i], types[i], quants[i]) for i in range(6)]

    def softmax_opts(beta: float):
        s.SoftmaxOptionsStart(b)
        s.SoftmaxOptionsAddBeta(b, beta)
        return s.SoftmaxOptionsEnd(b)

    opts = [softmax_opts(x) for x in (1.0, 1e9, 1.0)]

    def operator(i_in, i_out, opt):
        s.OperatorStartInputsVector(b, 1)
        b.PrependInt32(i_in)
        ins = b.EndVector()
        s.OperatorStartOutputsVector(b, 1)
        b.PrependInt32(i_out)
        outs = b.EndVector()
        s.OperatorStart(b)
        s.OperatorAddOpcodeIndex(b, 0)
        s.OperatorAddInputs(b, ins)
        s.OperatorAddOutputs(b, outs)
        s.OperatorAddBuiltinOptionsType(b, s.BuiltinOptions.SoftmaxOptions)
        s.OperatorAddBuiltinOptions(b, opt)
        return s.OperatorEnd(b)

    ops = [
        operator(0, 1, opts[0]),
        operator(2, 3, opts[1]),
        operator(4, 5, opts[2]),
        operator(-1, 1, opts[0]),  # absent optional input
    ]

    s.SubGraphStartTensorsVector(b, 6)
    for t in reversed(tensors):
        b.PrependUOffsetTRelative(t)
    tvec = b.EndVector()
    s.SubGraphStartInputsVector(b, 1)
    b.PrependInt32(0)
    sg_in = b.EndVector()
    s.SubGraphStartOutputsVector(b, 1)
    b.PrependInt32(1)
    sg_out = b.EndVector()
    s.SubGraphStartOperatorsVector(b, 4)
    for o in reversed(ops):
        b.PrependUOffsetTRelative(o)
    ovec = b.EndVector()
    s.SubGraphStart(b)
    s.SubGraphAddTensors(b, tvec)
    s.SubGraphAddInputs(b, sg_in)
    s.SubGraphAddOutputs(b, sg_out)
    s.SubGraphAddOperators(b, ovec)
    sg = s.SubGraphEnd(b)

    s.OperatorCodeStart(b)
    s.OperatorCodeAddDeprecatedBuiltinCode(b, s.BuiltinOperator.SOFTMAX)
    oc = s.OperatorCodeEnd(b)

    s.ModelStartOperatorCodesVector(b, 1)
    b.PrependUOffsetTRelative(oc)
    ocs = b.EndVector()
    s.ModelStartSubgraphsVector(b, 1)
    b.PrependUOffsetTRelative(sg)
    sgs = b.EndVector()
    s.BufferStart(b)
    buf0 = s.BufferEnd(b)
    s.ModelStartBuffersVector(b, 1)
    b.PrependUOffsetTRelative(buf0)
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
