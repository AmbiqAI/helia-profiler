"""Tests for the stock-TFLM NSX engine adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.config import load_config
from helia_profiler.engines import EngineType
from helia_profiler.engines.tflm import TFLMAdapter
from helia_profiler.errors import EngineError


def _config(tmp_path: Path, backend: str | None = None):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    engine: dict[str, str] = {"type": "tflm"}
    if backend is not None:
        engine["backend"] = backend
    return load_config(None, {"model": {"path": str(model)}, "engine": engine})


def test_reference_backend_adds_tflm_module(tmp_path: Path):
    artifacts = TFLMAdapter().prepare(_config(tmp_path), tmp_path)

    assert artifacts.engine_type is EngineType.TFLM
    assert artifacts.cmake_vars == {"NSX_TFLITE_MICRO_BACKEND": "reference"}
    assert [(module.name, module.project) for module in artifacts.extra_modules] == [
        ("nsx-tflite-micro", "nsx-tflite-micro")
    ]


def test_cmsis_nn_backend_adds_kernel_module_before_tflm(tmp_path: Path):
    artifacts = TFLMAdapter().prepare(_config(tmp_path, "cmsis_nn"), tmp_path)

    assert artifacts.cmake_vars == {"NSX_TFLITE_MICRO_BACKEND": "cmsis_nn"}
    assert [(module.name, module.project) for module in artifacts.extra_modules] == [
        ("arm-cmsis-nn", "arm-cmsis-nn"),
        ("nsx-tflite-micro", "nsx-tflite-micro"),
    ]


def test_invalid_backend_fails_with_actionable_error(tmp_path: Path):
    with pytest.raises(EngineError, match="Invalid TFLM backend 'helia'"):
        TFLMAdapter().prepare(_config(tmp_path, "helia"), tmp_path)
