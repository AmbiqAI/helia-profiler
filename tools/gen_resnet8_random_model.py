"""Generate a deterministic random-weight TFLite equivalent of the ET ResNet-8.

The topology mirrors NeuralSPOT-X's ``resnet8_cmsis_nn.pte`` compute graph:

* float32 32x32 RGB input followed by int8 quantization
* 16-channel stem and residual block
* 32- and 64-channel downsampling residual blocks with 1x1 shortcuts
* 8x8 average pool and a 1x1, 10-class convolution
* int8 dequantization to a float32 1x1x10 output

The flatbuffer is assembled directly from LiteRT's generated schema so model
generation does not require TensorFlow.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import flatbuffers
import numpy as np
from ai_edge_litert import schema_py_generated as schema
from ai_edge_litert.interpreter import Interpreter


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "examples" / "models"
MODEL_PATH = OUTPUT_DIR / "resnet8_random_int8.tflite"
MANIFEST_PATH = OUTPUT_DIR / "resnet8_random_int8.json"
SEED = 8
ACTIVATION_SCALE = 1.0 / 32.0
INPUT_SCALE = 1.0 / 128.0
WEIGHT_SCALE = 0.02


def generate() -> tuple[bytes, list[str], int]:
    """Return the deterministic quantized ResNet-8 flatbuffer and metadata."""
    rng = np.random.default_rng(SEED)
    buffers = [schema.BufferT()]
    tensors: list[schema.TensorT] = []
    operators: list[schema.OperatorT] = []
    operator_names: list[str] = []
    parameter_count = 0

    def data_buffer(values: np.ndarray, dtype: str) -> int:
        buffers.append(schema.BufferT(data=np.asarray(values, dtype=dtype).tobytes()))
        return len(buffers) - 1

    def quantization(
        scales: float | list[float],
        zero_points: int | list[int] = 0,
        *,
        dimension: int = 0,
    ) -> schema.QuantizationParametersT:
        scale_values = [scales] if isinstance(scales, float) else scales
        zero_values = [zero_points] if isinstance(zero_points, int) else zero_points
        return schema.QuantizationParametersT(
            scale=scale_values,
            zeroPoint=zero_values,
            quantizedDimension=dimension,
        )

    def tensor(
        name: str,
        shape: list[int],
        *,
        tensor_type: int = schema.TensorType.INT8,
        scale: float | list[float] | None = ACTIVATION_SCALE,
        zero_point: int | list[int] = 0,
        quantized_dimension: int = 0,
        buffer: int = 0,
    ) -> int:
        tensors.append(
            schema.TensorT(
                shape=shape,
                type=tensor_type,
                buffer=buffer,
                name=name,
                quantization=(
                    None
                    if scale is None
                    else quantization(
                        scale, zero_point, dimension=quantized_dimension
                    )
                ),
            )
        )
        return len(tensors) - 1

    def append_operator(name: str, operator: schema.OperatorT) -> None:
        operator_names.append(name)
        operators.append(operator)

    float_input = tensor(
        "input_float32",
        [1, 32, 32, 3],
        tensor_type=schema.TensorType.FLOAT32,
        scale=None,
    )
    quantized_input = tensor(
        "input_int8", [1, 32, 32, 3], scale=INPUT_SCALE
    )
    append_operator(
        "quantize_input",
        schema.OperatorT(
            opcodeIndex=0,
            inputs=[float_input],
            outputs=[quantized_input],
            builtinOptionsType=schema.BuiltinOptions.QuantizeOptions,
            builtinOptions=schema.QuantizeOptionsT(),
        ),
    )

    def conv2d(
        name: str,
        input_id: int,
        input_shape: list[int],
        output_channels: int,
        kernel_size: int,
        stride: int,
        *,
        relu: bool,
        input_scale: float = ACTIVATION_SCALE,
    ) -> tuple[int, list[int]]:
        nonlocal parameter_count
        _, height, width, input_channels = input_shape
        output_shape = [
            1,
            (height + stride - 1) // stride,
            (width + stride - 1) // stride,
            output_channels,
        ]
        weight_shape = [
            output_channels,
            kernel_size,
            kernel_size,
            input_channels,
        ]
        weights = rng.integers(-8, 9, weight_shape, dtype=np.int8)
        biases = rng.integers(-16, 17, output_channels, dtype=np.int32)
        weight_scales = [WEIGHT_SCALE] * output_channels
        bias_scales = [input_scale * WEIGHT_SCALE] * output_channels
        weights_id = tensor(
            f"{name}_weights",
            weight_shape,
            scale=weight_scales,
            zero_point=[0] * output_channels,
            quantized_dimension=0,
            buffer=data_buffer(weights, "i1"),
        )
        bias_id = tensor(
            f"{name}_bias",
            [output_channels],
            tensor_type=schema.TensorType.INT32,
            scale=bias_scales,
            zero_point=[0] * output_channels,
            quantized_dimension=0,
            buffer=data_buffer(biases, "<i4"),
        )
        output_id = tensor(f"{name}_output", output_shape)
        append_operator(
            name,
            schema.OperatorT(
                opcodeIndex=1,
                inputs=[input_id, weights_id, bias_id],
                outputs=[output_id],
                builtinOptionsType=schema.BuiltinOptions.Conv2DOptions,
                builtinOptions=schema.Conv2DOptionsT(
                    padding=schema.Padding.SAME,
                    strideW=stride,
                    strideH=stride,
                    dilationWFactor=1,
                    dilationHFactor=1,
                    fusedActivationFunction=(
                        schema.ActivationFunctionType.RELU
                        if relu
                        else schema.ActivationFunctionType.NONE
                    ),
                ),
            ),
        )
        parameter_count += weights.size + biases.size
        return output_id, output_shape

    def add(name: str, lhs: int, rhs: int, shape: list[int]) -> int:
        output_id = tensor(f"{name}_output", shape)
        append_operator(
            name,
            schema.OperatorT(
                opcodeIndex=2,
                inputs=[lhs, rhs],
                outputs=[output_id],
                builtinOptionsType=schema.BuiltinOptions.AddOptions,
                builtinOptions=schema.AddOptionsT(
                    fusedActivationFunction=schema.ActivationFunctionType.RELU
                ),
            ),
        )
        return output_id

    stem, shape16 = conv2d(
        "stem_conv3x3",
        quantized_input,
        [1, 32, 32, 3],
        16,
        3,
        1,
        relu=True,
        input_scale=INPUT_SCALE,
    )
    block16_1, _ = conv2d(
        "block16_conv1", stem, shape16, 16, 3, 1, relu=True
    )
    block16_2, _ = conv2d(
        "block16_conv2", block16_1, shape16, 16, 3, 1, relu=False
    )
    stage16 = add("block16_add", block16_2, stem, shape16)

    block32_1, shape32 = conv2d(
        "block32_conv1", stage16, shape16, 32, 3, 2, relu=True
    )
    block32_2, _ = conv2d(
        "block32_conv2", block32_1, shape32, 32, 3, 1, relu=False
    )
    shortcut32, _ = conv2d(
        "block32_shortcut", stage16, shape16, 32, 1, 2, relu=False
    )
    stage32 = add("block32_add", block32_2, shortcut32, shape32)

    block64_1, shape64 = conv2d(
        "block64_conv1", stage32, shape32, 64, 3, 2, relu=True
    )
    block64_2, _ = conv2d(
        "block64_conv2", block64_1, shape64, 64, 3, 1, relu=False
    )
    shortcut64, _ = conv2d(
        "block64_shortcut", stage32, shape32, 64, 1, 2, relu=False
    )
    stage64 = add("block64_add", block64_2, shortcut64, shape64)

    pooled = tensor("global_avg_pool_output", [1, 1, 1, 64])
    append_operator(
        "global_avg_pool_8x8",
        schema.OperatorT(
            opcodeIndex=3,
            inputs=[stage64],
            outputs=[pooled],
            builtinOptionsType=schema.BuiltinOptions.Pool2DOptions,
            builtinOptions=schema.Pool2DOptionsT(
                padding=schema.Padding.VALID,
                strideW=8,
                strideH=8,
                filterWidth=8,
                filterHeight=8,
                fusedActivationFunction=schema.ActivationFunctionType.NONE,
            ),
        ),
    )
    logits_int8, _ = conv2d(
        "classifier_conv1x1",
        pooled,
        [1, 1, 1, 64],
        10,
        1,
        1,
        relu=False,
    )
    float_output = tensor(
        "output_float32",
        [1, 1, 1, 10],
        tensor_type=schema.TensorType.FLOAT32,
        scale=None,
    )
    append_operator(
        "dequantize_output",
        schema.OperatorT(
            opcodeIndex=4,
            inputs=[logits_int8],
            outputs=[float_output],
            builtinOptionsType=schema.BuiltinOptions.DequantizeOptions,
            builtinOptions=schema.DequantizeOptionsT(),
        ),
    )

    opcode_specs = [
        (schema.BuiltinOperator.QUANTIZE, 2),
        (schema.BuiltinOperator.CONV_2D, 3),
        (schema.BuiltinOperator.ADD, 2),
        (schema.BuiltinOperator.AVERAGE_POOL_2D, 2),
        (schema.BuiltinOperator.DEQUANTIZE, 2),
    ]
    model = schema.ModelT(
        version=3,
        operatorCodes=[
            schema.OperatorCodeT(
                deprecatedBuiltinCode=code,
                builtinCode=code,
                version=version,
            )
            for code, version in opcode_specs
        ],
        subgraphs=[
            schema.SubGraphT(
                tensors=tensors,
                inputs=[float_input],
                outputs=[float_output],
                operators=operators,
                name="resnet8_random_int8",
            )
        ],
        description="Deterministic random-weight ResNet-8 matching the ET fixture",
        buffers=buffers,
    )
    builder = flatbuffers.Builder(256 * 1024)
    offset = model.Pack(builder)
    builder.Finish(offset, file_identifier=b"TFL3")
    return bytes(builder.Output()), operator_names, parameter_count


def validate(data: bytes) -> list[float]:
    """Run one host inference and return the flattened finite output."""
    interpreter = Interpreter(model_content=data)
    interpreter.allocate_tensors()
    input_detail = interpreter.get_input_details()[0]
    output_detail = interpreter.get_output_details()[0]
    input_data = np.linspace(-1.0, 1.0, 32 * 32 * 3, dtype=np.float32).reshape(
        1, 32, 32, 3
    )
    interpreter.set_tensor(input_detail["index"], input_data)
    interpreter.invoke()
    output = interpreter.get_tensor(output_detail["index"]).reshape(-1)
    if output.shape != (10,) or not np.all(np.isfinite(output)):
        raise RuntimeError(f"Unexpected output: shape={output.shape}, values={output}")
    return output.tolist()


def main() -> None:
    data, operators, parameter_count = generate()
    output = validate(data)
    digest = hashlib.sha256(data).hexdigest()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.write_bytes(data)
    MANIFEST_PATH.write_text(
        json.dumps(
            {
                "name": "resnet8-random-int8",
                "description": "Random-weight TFLite equivalent of the ET ResNet-8 fixture",
                "seed": SEED,
                "input_shape": [1, 32, 32, 3],
                "input_type": "float32",
                "output_shape": [1, 1, 1, 10],
                "output_type": "float32",
                "parameters": parameter_count,
                "operators": operators,
                "host_validation_output": output,
                "bytes": len(data),
                "sha256": digest,
                "generator": "tools/gen_resnet8_random_model.py",
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Wrote {MODEL_PATH} ({len(data)} bytes, sha256={digest})")
    print(f"Operators: {len(operators)}, parameters: {parameter_count}")
    print(f"Host output: {output}")


if __name__ == "__main__":
    main()
