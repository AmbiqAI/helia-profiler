"""The engine is ``litert`` everywhere; the old ``tflm`` id is refused, not aliased."""

from __future__ import annotations

import pytest

import helia_profiler as hpx
from helia_profiler.config import load_config
from helia_profiler.engines import EngineType
from helia_profiler.errors import ConfigError
from helia_profiler.identity import canonical_engine
from helia_profiler.runtime_records import runtime


def test_the_engine_type_is_litert():
    assert EngineType("litert") is EngineType.LITERT
    with pytest.raises(ValueError):
        EngineType("tflm")


def test_identity_refuses_the_old_engine_id():
    assert canonical_engine("litert").value == "litert"
    with pytest.raises(ValueError):
        canonical_engine("tflm")


def test_a_config_naming_tflm_is_refused():
    model = hpx.examples.tiny_cnn()
    load_config(None, {"model": {"path": model}, "engine": {"type": "litert"}})
    with pytest.raises(ConfigError, match="litert"):
        load_config(None, {"model": {"path": model}, "engine": {"type": "tflm"}})


def test_the_runtime_record_is_litert():
    assert runtime("litert") is not None
    assert runtime("tflm") is None
