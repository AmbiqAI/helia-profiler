#!/usr/bin/env python3
"""Cast an FP32 LiteRT model to a *true* all-FP16 model.

Standard "float16 quantization" (the LiteRT converter's
``supported_types=[tf.float16]`` and ai-edge-quantizer's FLOAT16 recipe) stores
only the **weights** as ``FLOAT16`` and inserts ``DEQUANTIZE`` ops, so every
activation -- and all arithmetic -- stays ``FLOAT32``. That never exercises
half-precision kernels.

This tool instead rewrites the whole graph: every ``FLOAT32`` tensor becomes
``FLOAT16`` and every ``FLOAT32`` constant buffer is re-encoded as float16.
Graph structure, shapes, and non-float tensors (e.g. ``INT32`` shape constants)
are untouched, so the result runs the *same* ops on FLOAT16 inputs, weights,
and outputs -- what heliaRT / heliaAOT's ``float16_t`` kernels consume on the
Cortex-M55.

Feed it a plain FP32 export with no ``DEQUANTIZE`` ops (not an already
float16-quantized model). Needs the ``analysis`` extra:

    uv run --extra analysis python tools/cast_fp16.py model_fp32.tflite model_fp16.tflite
"""

from __future__ import annotations

import argparse
from pathlib import Path

import flatbuffers
import numpy as np
from ai_edge_litert import schema_py_generated as schema

FLOAT32 = schema.TensorType.FLOAT32
FLOAT16 = schema.TensorType.FLOAT16
DEQUANTIZE = schema.BuiltinOperator.DEQUANTIZE


def cast_model(data: bytes) -> tuple[bytes, dict[str, int]]:
    """Return the FP16-cast flatbuffer and a summary of what changed.

    Raises ``ValueError`` for a model that already carries ``DEQUANTIZE`` ops.
    """
    model = schema.ModelT.InitFromObj(schema.Model.GetRootAsModel(data, 0))

    for opcode in model.operatorCodes:
        # builtinCode supersedes the int8 deprecatedBuiltinCode, which
        # saturates at 127; the larger of the two is always the real code.
        if max(opcode.builtinCode, opcode.deprecatedBuiltinCode) == DEQUANTIZE:
            raise ValueError(
                "model already carries DEQUANTIZE ops (float16-quantized weights); "
                "start from a plain FP32 export instead"
            )

    tensors_cast = 0
    cast_buffers: set[int] = set()
    for subgraph in model.subgraphs:
        for tensor in subgraph.tensors:
            if tensor.type != FLOAT32:
                continue
            tensor.type = FLOAT16
            tensors_cast += 1
            buf = model.buffers[tensor.buffer]
            if tensor.buffer in cast_buffers or buf.data is None or len(buf.data) == 0:
                continue
            f32 = np.asarray(buf.data, dtype=np.uint8).view(np.float32)
            buf.data = f32.astype(np.float16).view(np.uint8)
            cast_buffers.add(tensor.buffer)

    builder = flatbuffers.Builder(len(data))
    builder.Finish(model.Pack(builder), file_identifier=b"TFL3")
    return bytes(builder.Output()), {
        "tensors_cast": tensors_cast,
        "buffers_cast": len(cast_buffers),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src", type=Path, help="plain FP32 .tflite")
    ap.add_argument("dst", type=Path, help="output all-FP16 .tflite")
    args = ap.parse_args()

    try:
        out, summary = cast_model(args.src.read_bytes())
    except ValueError as exc:
        raise SystemExit(f"{args.src}: {exc}") from exc
    args.dst.write_bytes(out)
    print(
        f"wrote {args.dst} ({len(out):,} bytes): "
        f"{summary['tensors_cast']} tensors FLOAT32->FLOAT16, "
        f"{summary['buffers_cast']} weight buffers re-encoded as float16"
    )


if __name__ == "__main__":
    main()
