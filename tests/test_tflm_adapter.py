from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.config import load_config
from helia_profiler.engines import EngineType
from helia_profiler.engines.tflm import TFLMAdapter
from helia_profiler.errors import ConfigError, EngineError


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


@pytest.mark.parametrize("backend", ["reference", "cmsis_nn"])
def test_unread_config_file_is_rejected_before_preparation(tmp_path: Path, backend: str):
    config = load_config(
        None,
        {
            "model": {"path": "model.tflite"},
            "engine": {
                "type": "tflm",
                "backend": backend,
                "config_path": str(tmp_path / "unread.yml"),
            },
        },
    )
    work_dir = tmp_path / "work"
    with pytest.raises(EngineError, match="TFLM does not read engine.config_path") as excinfo:
        TFLMAdapter().prepare(config, work_dir)
    assert "Remove engine.config_path" in (excinfo.value.hint or "")
    assert not work_dir.exists()


def test_nested_backend_is_rejected_with_top_level_hint(tmp_path: Path):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")

    with pytest.raises(
        ConfigError, match="engine.config.backend is not supported; use engine.backend instead"
    ):
        load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "tflm", "config": {"backend": "cmsis_nn"}},
            },
        )
