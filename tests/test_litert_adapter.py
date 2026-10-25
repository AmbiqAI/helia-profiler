from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from helia_profiler.config import load_config
from helia_profiler.engines import EngineType
from helia_profiler.engines.litert import LiteRtAdapter
from helia_profiler.errors import ConfigError, EngineError
from helia_profiler.results import NsxModuleRef


def _config(tmp_path: Path, backend: str | None = None):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    engine: dict[str, str] = {"type": "litert"}
    if backend is not None:
        engine["backend"] = backend
    return load_config(None, {"model": {"path": str(model)}, "engine": engine})


def test_reference_backend_adds_litert_module(tmp_path: Path):
    artifacts = LiteRtAdapter().prepare(_config(tmp_path), tmp_path)

    assert artifacts.engine_type is EngineType.LITERT
    assert artifacts.cmake_vars == {"NSX_TFLITE_MICRO_BACKEND": "reference"}
    assert [(module.name, module.project) for module in artifacts.extra_modules] == [
        ("nsx-tflite-micro", "nsx-tflite-micro")
    ]


def test_cmsis_nn_backend_adds_kernel_module_before_litert(tmp_path: Path):
    artifacts = LiteRtAdapter().prepare(_config(tmp_path, "cmsis_nn"), tmp_path)

    assert artifacts.cmake_vars == {"NSX_TFLITE_MICRO_BACKEND": "cmsis_nn"}
    assert [(module.name, module.project) for module in artifacts.extra_modules] == [
        ("arm-cmsis-nn", "arm-cmsis-nn"),
        ("nsx-tflite-micro", "nsx-tflite-micro"),
    ]


def test_invalid_backend_fails_with_actionable_error(tmp_path: Path):
    with pytest.raises(EngineError, match="Invalid LiteRT backend 'helia'"):
        LiteRtAdapter().prepare(_config(tmp_path, "helia"), tmp_path)


@pytest.mark.parametrize("backend", ["reference", "cmsis_nn"])
def test_unread_config_file_is_rejected_before_preparation(tmp_path: Path, backend: str):
    config = load_config(
        None,
        {
            "model": {"path": "model.tflite"},
            "engine": {
                "type": "litert",
                "backend": backend,
                "config_path": str(tmp_path / "unread.yml"),
            },
        },
    )
    work_dir = tmp_path / "work"
    with pytest.raises(EngineError, match="LiteRT does not read engine.config_path") as excinfo:
        LiteRtAdapter().prepare(config, work_dir)
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
                "engine": {"type": "litert", "config": {"backend": "cmsis_nn"}},
            },
        )


@pytest.mark.parametrize("case", ["valid", "unread_file", "wrong_stack", "wrong_backend"])
def test_prepared_settings_before_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
):
    engine = {"type": "litert", "backend": "reference" if case == "wrong_backend" else "cmsis_nn"}
    if case != "valid":
        engine["config_path"] = str(tmp_path / "unread.yml")
    config = load_config(None, {"model": {"path": "model.tflite"}, "engine": engine})
    runtime = SimpleNamespace(
        record=SimpleNamespace(stack="helia-rt" if case == "wrong_stack" else "upstream")
    )
    module = NsxModuleRef.registry("prepared-upstream", "prepared-upstream")
    stage = Mock(return_value=module)
    monkeypatch.setattr("helia_profiler.fixture_runtime.prepare_runtime_module", stage)
    work_dir = tmp_path / "work"
    if case == "valid":
        artifacts = LiteRtAdapter().prepare_runtime(config, work_dir, runtime)
        stage.assert_called_once_with(runtime, work_dir)
        assert artifacts.extra_modules == [module]
        assert artifacts.engine_header == "tensorflow/lite/micro/micro_interpreter.h"
        assert artifacts.cmake_vars == {}
    else:
        message = "config_path" if case == "unread_file" else "Prepared LiteRT requires"
        with pytest.raises(EngineError, match=message):
            LiteRtAdapter().prepare_runtime(config, work_dir, runtime)
        stage.assert_not_called()
        assert not work_dir.exists()
