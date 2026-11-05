"""int16 FullyConnected with per-channel weights: reader and LiteRT skip."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from helia_profiler.modelcost._tflite_reader import has_int16_per_channel_fc

FIXTURE = Path(__file__).parent / "fixtures" / "fc_int16_per_channel.tflite"
TOOL = Path(__file__).parent.parent / "tools" / "gen_fc_per_channel_fixture.py"


def _generator():
    pytest.importorskip("ai_edge_litert.schema_py_generated")
    spec = importlib.util.spec_from_file_location("gen_fc_per_channel_fixture", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fixture_matches_its_generator():
    assert _generator().generate() == FIXTURE.read_bytes()


def test_reader_flags_the_fixture():
    assert has_int16_per_channel_fc(FIXTURE.read_bytes())


def test_reader_passes_int8_input():
    generator = _generator()
    from ai_edge_litert.schema_py_generated import TensorType

    assert not has_int16_per_channel_fc(generator.generate(input_type=TensorType.INT8))


def test_reader_passes_per_tensor_weights():
    assert not has_int16_per_channel_fc(_generator().generate(scales=1))


@pytest.mark.parametrize(
    "fixture", ["kws_ref_model.tflite", "softmax_scale_unsupported.tflite", "kws_float_fp32.tflite"]
)
def test_reader_passes_other_fixtures(fixture):
    assert not has_int16_per_channel_fc((FIXTURE.parent / fixture).read_bytes())
