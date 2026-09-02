#!/usr/bin/env python3
"""Generate small floating-point LiteRT models for HPX profiling.

HPX's fixtures were all int8/int16; heliaRT 1.19.0 / heliaAOT 0.19.0 add FP16
and FP32 kernels, so verifying them needs float ``.tflite`` files. This tool
emits a tiny KWS-shaped DS-CNN in two forms:

* ``{name}_fp32.tflite`` -- every tensor ``FLOAT32``.
* ``{name}_fp16_weights.tflite`` -- the converter's float16 post-training
  quantization: weights stored ``FLOAT16`` and ``DEQUANTIZE``d to ``FLOAT32``
  activations at runtime. This is what "coerce an FP32 model to FP16" produces
  in LiteRT and ai-edge-quantizer, and it does NOT exercise fp16 kernels.

For a *true* all-``FLOAT16`` graph, run ``tools/cast_fp16.py`` on the FP32
output. Point ``--from-keras`` / ``--from-saved-model`` at a real source model
to convert it instead of the built-in one.

Requires ``tensorflow`` (the LiteRT converter), which is NOT an HPX dependency.
Run it in a throwaway venv:

    python -m venv /tmp/tfvenv && /tmp/tfvenv/bin/pip install tensorflow-cpu
    /tmp/tfvenv/bin/python tools/gen_float_model.py --out-dir tests/fixtures
"""

from __future__ import annotations

import argparse
from pathlib import Path


def _tensorflow():
    try:
        import tensorflow as tf
    except ImportError as exc:  # pragma: no cover - environment guard
        raise SystemExit(
            "tensorflow is required (see the module docstring for a throwaway venv)"
        ) from exc
    return tf


def _build_kws_dscnn(tf, input_shape: tuple[int, int, int], num_classes: int):
    """A tiny DS-CNN skeleton: conv, depthwise, pointwise, flatten, FC, softmax.

    Every op here has an FP16/FP32 kernel in helia-aot 0.19.0. Spatial
    reduction uses stride-2 convolutions because ``MEAN`` (GlobalAveragePooling)
    is still int8/int16-only there; softmax keeps the Keras default
    ``beta == 1.0``, which the float kernel requires.

    Weights only need to be finite. The converter prunes an all-zero Dense
    bias as a no-op (conv biases survive), so the FULLY_CONNECTED op in these
    fixtures carries no bias tensor; use a non-zero ``bias_initializer`` if a
    fixture must exercise the bias path.
    """
    # batch_size=1 keeps every shape static, so Flatten lowers to a single
    # RESHAPE with a constant shape instead of a SHAPE/STRIDED_SLICE/PACK chain.
    inp = tf.keras.Input(shape=input_shape, batch_size=1, name="spectrogram")
    x = tf.keras.layers.Conv2D(8, 3, strides=2, padding="same", activation="relu")(inp)
    x = tf.keras.layers.DepthwiseConv2D(3, strides=2, padding="same", activation="relu")(x)
    x = tf.keras.layers.Conv2D(16, 1, padding="same", activation="relu")(x)
    x = tf.keras.layers.Flatten()(x)
    out = tf.keras.layers.Dense(num_classes, activation="softmax", name="logits")(x)
    return tf.keras.Model(inp, out, name="kws_dscnn_float")


def _converter(tf, args):
    """A TFLiteConverter for the built-in model or a user-supplied source."""
    if args.from_saved_model:
        return tf.lite.TFLiteConverter.from_saved_model(args.from_saved_model)
    if args.from_keras:
        return tf.lite.TFLiteConverter.from_keras_model(tf.keras.models.load_model(args.from_keras))
    model = _build_kws_dscnn(tf, tuple(args.input_shape), args.num_classes)
    return tf.lite.TFLiteConverter.from_keras_model(model)


def _write(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    print(f"  wrote {path}  ({len(data):,} bytes)")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out-dir", type=Path, default=Path("."))
    ap.add_argument("--name", default="kws_float")
    ap.add_argument(
        "--input-shape", type=int, nargs=3, default=[32, 32, 1], metavar=("H", "W", "C")
    )
    ap.add_argument("--num-classes", type=int, default=12)
    source = ap.add_mutually_exclusive_group()
    source.add_argument("--from-keras", help="source .keras file")
    source.add_argument("--from-saved-model", help="source SavedModel directory")
    only = ap.add_mutually_exclusive_group()
    only.add_argument("--fp32-only", action="store_true")
    only.add_argument("--fp16-weights-only", action="store_true")
    args = ap.parse_args()

    tf = _tensorflow()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    if not args.fp16_weights_only:
        print("FP32:")
        _write(args.out_dir / f"{args.name}_fp32.tflite", _converter(tf, args).convert())

    if not args.fp32_only:
        conv = _converter(tf, args)
        conv.optimizations = [tf.lite.Optimize.DEFAULT]
        conv.target_spec.supported_types = [tf.float16]
        print("FP16 weights (float16 post-training quantization):")
        _write(args.out_dir / f"{args.name}_fp16_weights.tflite", conv.convert())


if __name__ == "__main__":
    main()
