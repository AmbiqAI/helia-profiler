#!/usr/bin/env python3
"""Cast an FP32 LiteRT model to a *true* all-FP16 model.

Standard "float16 quantization" (the LiteRT converter's
``supported_types=[tf.float16]`` and ai-edge-quantizer's FLOAT16 recipe) stores
only the **weights** as ``FLOAT16`` and inserts ``DEQUANTIZE`` ops so every
activation — and all arithmetic — stays ``FLOAT32``. That never exercises
half-precision kernels.

This tool instead rewrites the whole graph: every ``FLOAT32`` tensor type
becomes ``FLOAT16`` and every ``FLOAT32`` constant buffer is re-encoded as
``float16``. Graph structure, shapes, and every non-float tensor (e.g. ``INT32``
shape constants) are untouched, so the result runs the *same* ops on FLOAT16
inputs, weights, and outputs — what helia-rt / helia-aot's FP16 kernels
(``float16_t``) actually consume on the Cortex-M55.

Feed it an FP32 model with no ``DEQUANTIZE`` ops (i.e. a plain FP32 export, not
an already float16-quantized one). Depends only on ``ai-edge-litert`` +
``numpy``, both HPX dependencies.

    python tools/cast_fp16.py model_fp32.tflite model_fp16_true.tflite
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from ai_edge_litert import schema_py_generated as schema
import flatbuffers

FLOAT32 = schema.TensorType.FLOAT32
FLOAT16 = schema.TensorType.FLOAT16
DEQUANTIZE = schema.BuiltinOperator.DEQUANTIZE


def cast_model(data: bytes) -> tuple[bytes, dict[str, int]]:
    """Return the FP16-cast flatbuffer and a summary of what changed."""
    model = schema.ModelT.InitFromObj(schema.Model.GetRootAsModel(data, 0))

    for opcode in model.operatorCodes:
        code = max(opcode.builtinCode, opcode.deprecatedBuiltinCode)
        if code == DEQUANTIZE:
            raise SystemExit(
                "model already carries DEQUANTIZE ops (float16-quantized weights); "
                "start from a plain FP32 export instead."
            )

    tensors_cast = 0
    buffers_cast = 0
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
            f32 = np.frombuffer(np.asarray(buf.data, dtype=np.uint8).tobytes(), dtype=np.float32)
            buf.data = np.frombuffer(f32.astype(np.float16).tobytes(), dtype=np.uint8)
            cast_buffers.add(tensor.buffer)
            buffers_cast += 1

    builder = flatbuffers.Builder(len(data))
    builder.Finish(model.Pack(builder), file_identifier=b"TFL3")
    return bytes(builder.Output()), {"tensors_cast": tensors_cast, "buffers_cast": buffers_cast}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src", type=Path, help="plain FP32 .tflite")
    ap.add_argument("dst", type=Path, help="output all-FP16 .tflite")
    args = ap.parse_args()

    out, summary = cast_model(args.src.read_bytes())
    args.dst.write_bytes(out)
    print(
        f"wrote {args.dst} ({len(out):,} bytes): "
        f"{summary['tensors_cast']} tensors FLOAT32->FLOAT16, "
        f"{summary['buffers_cast']} weight buffers re-encoded as float16"
    )


if __name__ == "__main__":
    main()
