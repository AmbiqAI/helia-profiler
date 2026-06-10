"""Tests for the pure MAC / OPS math helpers in model_analysis.

The ``analyze_model`` entry point reads a flatbuffer, which requires a
real tflite model.  The per-op math below is pure and easy to verify,
which is where the bulk of the estimation bugs would hide.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.model_analysis import (
    _conv2d_macs,
    _depthwise_conv2d_macs,
    _elementwise_ops,
    _fully_connected_macs,
    _transpose_conv_macs,
    analyze_model,
    is_available,
)


class TestConv2DMacs:
    def test_standard_nhwc(self):
        # input [1,8,8,3]  weights [C_out=16, K_h=3, K_w=3, C_in=3]
        # output [1,8,8,16]  -> 3*3*3*16*8*8*1 = 27_648
        macs = _conv2d_macs(
            input_shape=[1, 8, 8, 3],
            weight_shape=[16, 3, 3, 3],
            output_shape=[1, 8, 8, 16],
            stride_h=1,
            stride_w=1,
            dilation_h=1,
            dilation_w=1,
            has_bias=True,
        )
        assert macs == 27_648

    def test_stride_2_halves_output(self):
        macs = _conv2d_macs(
            input_shape=[1, 8, 8, 3],
            weight_shape=[16, 3, 3, 3],
            output_shape=[1, 4, 4, 16],
            stride_h=2,
            stride_w=2,
            dilation_h=1,
            dilation_w=1,
            has_bias=False,
        )
        # 3*3*3*16*4*4 = 6_912
        assert macs == 6_912

    def test_bad_weight_shape_returns_zero(self):
        macs = _conv2d_macs(
            input_shape=[1, 8, 8, 3],
            weight_shape=[16, 3, 3],  # 3-D — malformed
            output_shape=[1, 8, 8, 16],
            stride_h=1,
            stride_w=1,
            dilation_h=1,
            dilation_w=1,
            has_bias=True,
        )
        assert macs == 0


class TestDepthwiseMacs:
    def test_basic(self):
        # input [1,8,8,3]  weights [1,3,3,3]  depth_mult=1
        # output [1,8,8,3]  -> 3*3*3*1*8*8 = 1_728
        macs = _depthwise_conv2d_macs(
            input_shape=[1, 8, 8, 3],
            weight_shape=[1, 3, 3, 3],
            output_shape=[1, 8, 8, 3],
            depth_multiplier=1,
            has_bias=True,
        )
        assert macs == 1_728

    def test_depth_multiplier_scales(self):
        macs = _depthwise_conv2d_macs(
            input_shape=[1, 8, 8, 3],
            weight_shape=[1, 3, 3, 6],
            output_shape=[1, 8, 8, 6],
            depth_multiplier=2,
            has_bias=False,
        )
        # 3*3*3*2*8*8 = 3_456
        assert macs == 3_456


class TestFullyConnected:
    def test_matrix_multiply(self):
        # input [1, 128]  weights [64, 128] -> 128 * 64 = 8_192
        macs = _fully_connected_macs(
            input_shape=[1, 128],
            weight_shape=[64, 128],
            has_bias=True,
        )
        assert macs == 8_192

    def test_batched(self):
        # input [4, 128] -> 4 * 128 * 64 = 32_768
        macs = _fully_connected_macs(
            input_shape=[4, 128],
            weight_shape=[64, 128],
            has_bias=False,
        )
        assert macs == 32_768

    def test_bad_weight_shape_returns_zero(self):
        assert _fully_connected_macs([1, 128], [64], True) == 0


class TestTransposeConv:
    def test_mirror_of_conv2d(self):
        macs = _transpose_conv_macs(
            weight_shape=[16, 3, 3, 3],
            output_shape=[1, 8, 8, 16],
        )
        assert macs == 27_648


class TestElementwise:
    def test_product_of_dims(self):
        assert _elementwise_ops([1, 8, 8, 16]) == 1024
        assert _elementwise_ops([4, 16, 16, 8]) == 8192

    def test_empty_shape(self):
        assert _elementwise_ops([]) == 0


@pytest.mark.skipif(not is_available(), reason="ai-edge-litert not installed")
def test_quickstart_kws_model_reports_real_builtin_ops():
    model_path = Path(__file__).resolve().parents[1] / "examples" / "quickstart" / "kws_model.tflite"

    analysis = analyze_model(model_path)

    assert analysis is not None
    ops = {layer.op for layer in analysis.layers}
    assert "CONV_2D" in ops
    assert "SOFTMAX" in ops
    assert ops != {"ADD"}
