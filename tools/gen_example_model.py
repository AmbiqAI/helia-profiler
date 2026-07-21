"""Generate the deterministic tiny CNN distributed with heliaPROFILER.

Run with the analysis extra so LiteRT's generated schema is available::

    uv run --extra analysis python tools/gen_example_model.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import flatbuffers
import numpy as np
from ai_edge_litert import schema_py_generated as schema

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "src" / "helia_profiler" / "data" / "models"
MODEL_PATH = OUTPUT_DIR / "tiny_cnn.tflite"
MANIFEST_PATH = OUTPUT_DIR / "tiny_cnn.json"
SEED = 0
BATCH_SIZE = 1


def generate() -> bytes:
    """Return a deterministic sequential quantized int8 TFLite model."""
    rng = np.random.default_rng(SEED)
    buffers = [schema.BufferT()]
    tensors: list[schema.TensorT] = []

    def data_buffer(values: np.ndarray, dtype: str) -> int:
        data = np.asarray(values, dtype=dtype).tobytes()
        buffers.append(schema.BufferT(data=data))
        return len(buffers) - 1

    def quantization(scale: float, zero_point: int = 0) -> schema.QuantizationParametersT:
        return schema.QuantizationParametersT(
            scale=[scale],
            zeroPoint=[zero_point],
            quantizedDimension=0,
        )

    def tensor(
        name: str,
        shape: list[int],
        *,
        tensor_type: int = schema.TensorType.INT8,
        scale: float = 0.1,
        zero_point: int = 0,
        buffer: int = 0,
        include_quantization: bool = True,
    ) -> int:
        tensors.append(
            schema.TensorT(
                shape=shape,
                type=tensor_type,
                buffer=buffer,
                name=name,
                quantization=(
                    quantization(scale, zero_point) if include_quantization else None
                ),
            )
        )
        return len(tensors) - 1

    input_id = tensor("input", [BATCH_SIZE, 8, 8, 1])
    conv_3x3_weights = tensor(
        "conv_3x3_weights",
        [4, 3, 3, 1],
        scale=0.02,
        buffer=data_buffer(rng.integers(-8, 9, (4, 3, 3, 1)), "i1"),
    )
    conv_3x3_bias = tensor(
        "conv_3x3_bias",
        [4],
        tensor_type=schema.TensorType.INT32,
        scale=0.002,
        buffer=data_buffer(rng.integers(-16, 17, 4), "<i4"),
    )
    conv_3x3_output = tensor("conv_3x3_output", [BATCH_SIZE, 8, 8, 4])
    pooled = tensor("pooled", [BATCH_SIZE, 4, 4, 4])
    conv_1x1_weights = tensor(
        "conv_1x1_weights",
        [3, 1, 1, 4],
        scale=0.02,
        buffer=data_buffer(rng.integers(-8, 9, (3, 1, 1, 4)), "i1"),
    )
    conv_1x1_bias = tensor(
        "conv_1x1_bias",
        [3],
        tensor_type=schema.TensorType.INT32,
        scale=0.002,
        buffer=data_buffer(rng.integers(-16, 17, 3), "<i4"),
    )
    conv_1x1_output = tensor("conv_1x1_output", [BATCH_SIZE, 4, 4, 3])
    reshape_shape = tensor(
        "reshape_shape",
        [2],
        tensor_type=schema.TensorType.INT32,
        buffer=data_buffer(np.asarray([BATCH_SIZE, 48]), "<i4"),
        include_quantization=False,
    )
    flattened = tensor("flattened", [BATCH_SIZE, 48])
    dense_weights = tensor(
        "dense_weights",
        [4, 48],
        scale=0.02,
        buffer=data_buffer(rng.integers(-8, 9, (4, 48)), "i1"),
    )
    dense_bias = tensor(
        "dense_bias",
        [4],
        tensor_type=schema.TensorType.INT32,
        scale=0.002,
        buffer=data_buffer(rng.integers(-16, 17, 4), "<i4"),
    )
    logits = tensor("logits", [BATCH_SIZE, 4])
    output = tensor(
        "probabilities", [BATCH_SIZE, 4], scale=1 / 256, zero_point=-128
    )

    operators = [
        schema.OperatorT(
            opcodeIndex=0,
            inputs=[input_id, conv_3x3_weights, conv_3x3_bias],
            outputs=[conv_3x3_output],
            builtinOptionsType=schema.BuiltinOptions.Conv2DOptions,
            builtinOptions=schema.Conv2DOptionsT(
                padding=schema.Padding.SAME,
                strideW=1,
                strideH=1,
                fusedActivationFunction=schema.ActivationFunctionType.RELU,
            ),
        ),
        schema.OperatorT(
            opcodeIndex=1,
            inputs=[conv_3x3_output],
            outputs=[pooled],
            builtinOptionsType=schema.BuiltinOptions.Pool2DOptions,
            builtinOptions=schema.Pool2DOptionsT(
                padding=schema.Padding.VALID,
                strideW=2,
                strideH=2,
                filterWidth=2,
                filterHeight=2,
                fusedActivationFunction=schema.ActivationFunctionType.NONE,
            ),
        ),
        schema.OperatorT(
            opcodeIndex=0,
            inputs=[pooled, conv_1x1_weights, conv_1x1_bias],
            outputs=[conv_1x1_output],
            builtinOptionsType=schema.BuiltinOptions.Conv2DOptions,
            builtinOptions=schema.Conv2DOptionsT(
                padding=schema.Padding.SAME,
                strideW=1,
                strideH=1,
                fusedActivationFunction=schema.ActivationFunctionType.RELU,
            ),
        ),
        schema.OperatorT(
            opcodeIndex=2,
            inputs=[conv_1x1_output, reshape_shape],
            outputs=[flattened],
            builtinOptionsType=schema.BuiltinOptions.ReshapeOptions,
            builtinOptions=schema.ReshapeOptionsT(newShape=[BATCH_SIZE, 48]),
        ),
        schema.OperatorT(
            opcodeIndex=3,
            inputs=[flattened, dense_weights, dense_bias],
            outputs=[logits],
            builtinOptionsType=schema.BuiltinOptions.FullyConnectedOptions,
            builtinOptions=schema.FullyConnectedOptionsT(
                fusedActivationFunction=schema.ActivationFunctionType.NONE
            ),
        ),
        schema.OperatorT(
            opcodeIndex=4,
            inputs=[logits],
            outputs=[output],
            builtinOptionsType=schema.BuiltinOptions.SoftmaxOptions,
            builtinOptions=schema.SoftmaxOptionsT(beta=1.0),
        ),
    ]
    opcodes = [
        schema.OperatorCodeT(
            deprecatedBuiltinCode=schema.BuiltinOperator.CONV_2D,
            builtinCode=schema.BuiltinOperator.CONV_2D,
            version=3,
        ),
        schema.OperatorCodeT(
            deprecatedBuiltinCode=schema.BuiltinOperator.AVERAGE_POOL_2D,
            builtinCode=schema.BuiltinOperator.AVERAGE_POOL_2D,
            version=2,
        ),
        schema.OperatorCodeT(
            deprecatedBuiltinCode=schema.BuiltinOperator.RESHAPE,
            builtinCode=schema.BuiltinOperator.RESHAPE,
            version=1,
        ),
        schema.OperatorCodeT(
            deprecatedBuiltinCode=schema.BuiltinOperator.FULLY_CONNECTED,
            builtinCode=schema.BuiltinOperator.FULLY_CONNECTED,
            version=4,
        ),
        schema.OperatorCodeT(
            deprecatedBuiltinCode=schema.BuiltinOperator.SOFTMAX,
            builtinCode=schema.BuiltinOperator.SOFTMAX,
            version=2,
        ),
    ]
    model = schema.ModelT(
        version=3,
        operatorCodes=opcodes,
        subgraphs=[
            schema.SubGraphT(
                tensors=tensors,
                inputs=[input_id],
                outputs=[output],
                operators=operators,
                name="tiny_cnn",
            )
        ],
        description="HPX deterministic tiny CNN example",
        buffers=buffers,
    )

    builder = flatbuffers.Builder(4096)
    offset = model.Pack(builder)
    builder.Finish(offset, file_identifier=b"TFL3")
    return bytes(builder.Output())


def main() -> None:
    data = generate()
    digest = hashlib.sha256(data).hexdigest()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.write_bytes(data)
    MANIFEST_PATH.write_text(
        json.dumps(
            {
                "name": "tiny-cnn",
                "description": "Deterministic sequential quantized int8 CNN",
                "seed": SEED,
                "batch_size": BATCH_SIZE,
                "input_shape": [BATCH_SIZE, 8, 8, 1],
                "output_shape": [BATCH_SIZE, 4],
                "operators": [
                    "CONV_2D",
                    "AVERAGE_POOL_2D",
                    "CONV_2D",
                    "RESHAPE",
                    "FULLY_CONNECTED",
                    "SOFTMAX",
                ],
                "bytes": len(data),
                "sha256": digest,
                "generator": "tools/gen_example_model.py",
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Wrote {MODEL_PATH} ({len(data)} bytes, sha256={digest})")


if __name__ == "__main__":
    main()
