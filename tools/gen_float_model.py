#!/usr/bin/env python3
"""Generate small floating-point LiteRT models (FP32 + FP16) for HPX profiling.

HPX's fixtures are all int8/int16; helia-rt 1.19.0 / helia-aot 0.19.0 add FP16
and FP32 kernels, so verifying them needs a float ``.tflite``. This tool emits
a tiny KWS-shaped DS-CNN in two forms:

* **FP32** — every weight/activation tensor is ``FLOAT32``.
* **FP16** — float16 post-training quantization: weights stored ``FLOAT16``,
  dequantized to ``FLOAT32`` at runtime (``converter.target_spec.supported_types
  = [tf.float16]``). This is the exact "coerce an FP32 model to FP16" recipe —
  point ``--from-keras`` / ``--from-saved-model`` at a real source model to
  convert it instead of the built-in one.

Requires ``tensorflow`` (the LiteRT converter), which is NOT an HPX dependency.
Run it in a throwaway venv:

    python -m venv /tmp/tfvenv && /tmp/tfvenv/bin/pip install tensorflow-cpu
    /tmp/tfvenv/bin/python tools/gen_float_model.py --out-dir tests/fixtures

The emitted ``.tflite`` files are parsed by ``ai-edge-litert`` the same way HPX
reads any model, so ``hpx analyze`` works on them with no extra tooling.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def _build_kws_dscnn(input_shape: tuple[int, int, int], num_classes: int):
    """A tiny DS-CNN skeleton: conv, depthwise, pointwise, flatten, FC, softmax.

    Every op here has an FP16/FP32 kernel in helia-aot 0.19.0. Spatial
    reduction is done with stride-2 convolutions rather than pooling because
    ``MEAN`` (GlobalAveragePooling) is still int8/int16-only there; softmax
    keeps the Keras default ``beta == 1.0``, which the float kernel requires.
    """
    import tensorflow as tf

    # batch_size=1 keeps every shape static, so Flatten lowers to a single
    # RESHAPE with a constant shape instead of a SHAPE/STRIDED_SLICE/PACK chain.
    inp = tf.keras.Input(shape=input_shape, batch_size=1, name="spectrogram")
    x = tf.keras.layers.Conv2D(8, 3, strides=2, padding="same", activation="relu")(inp)
    x = tf.keras.layers.DepthwiseConv2D(3, strides=2, padding="same", activation="relu")(x)
    x = tf.keras.layers.Conv2D(16, 1, padding="same", activation="relu")(x)
    x = tf.keras.layers.Flatten()(x)
    out = tf.keras.layers.Dense(num_classes, activation="softmax", name="logits")(x)
    model = tf.keras.Model(inp, out, name="kws_dscnn_float")
    # Weights only need to be finite. Note the converter prunes an all-zero
    # Dense bias as a no-op (the conv biases survive), so the FULLY_CONNECTED
    # op in these fixtures carries no bias tensor -- give biases a non-zero
    # initializer if a fixture must exercise the bias path.
    model.compile(optimizer="adam", loss="categorical_crossentropy")
    return model


def _converter_from_source(args):
    """A TFLiteConverter for either the built-in model or a user source."""
    import tensorflow as tf

    if args.from_saved_model:
        return tf.lite.TFLiteConverter.from_saved_model(args.from_saved_model)
    if args.from_keras:
        model = tf.keras.models.load_model(args.from_keras)
        return tf.lite.TFLiteConverter.from_keras_model(model)
    shape = tuple(args.input_shape)
    model = _build_kws_dscnn(shape, args.num_classes)
    return tf.lite.TFLiteConverter.from_keras_model(model)


def _write(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    print(f"  wrote {path}  ({len(data):,} bytes)")


def main() -> None:
    import tensorflow as tf

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path, default=Path("."))
    ap.add_argument("--name", default="kws_float")
    ap.add_argument(
        "--input-shape",
        type=int,
        nargs=3,
        default=[32, 32, 1],
        metavar=("H", "W", "C"),
    )
    ap.add_argument("--num-classes", type=int, default=12)
    ap.add_argument("--from-keras", type=str, default=None, help="source .keras/SavedModel dir")
    ap.add_argument("--from-saved-model", type=str, default=None, help="source SavedModel dir")
    ap.add_argument("--fp32-only", action="store_true")
    ap.add_argument("--fp16-only", action="store_true")
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    if not args.fp16_only:
        conv = _converter_from_source(args)
        print("FP32:")
        _write(args.out_dir / f"{args.name}_fp32.tflite", conv.convert())

    if not args.fp32_only:
        conv = _converter_from_source(args)
        conv.optimizations = [tf.lite.Optimize.DEFAULT]
        conv.target_spec.supported_types = [tf.float16]
        print("FP16 (float16 post-training quantization):")
        _write(args.out_dir / f"{args.name}_fp16.tflite", conv.convert())


if __name__ == "__main__":
    main()
