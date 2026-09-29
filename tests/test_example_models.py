"""Tests for packaged deterministic example models."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

import helia_profiler as hpx
from helia_profiler import examples


def test_example_model_materializes_valid_tflite(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path))

    path = hpx.examples.tiny_cnn()
    data = path.read_bytes()

    assert path == tmp_path / "models" / "tiny-cnn" / "4419dfff1e15" / "tiny_cnn.tflite"
    assert data[4:8] == b"TFL3"
    assert len(data) == 2480
    assert hashlib.sha256(data).hexdigest() == (
        "4419dfff1e15e9479ad90b81c7997c4a2db274c87f6433e06cd34698108ac26f"
    )
    assert hpx.examples.tiny_cnn() == path


def test_example_model_manifest_matches_binary() -> None:
    package_root = Path(examples.__file__).parent / "data" / "models"
    manifest = json.loads((package_root / "tiny_cnn.json").read_text())
    data = (package_root / "tiny_cnn.tflite").read_bytes()

    assert manifest["name"] == "tiny-cnn"
    assert manifest["seed"] == 0
    assert manifest["batch_size"] == 1
    assert manifest["input_shape"][0] == 1
    assert manifest["output_shape"][0] == 1
    assert manifest["operators"] == [
        "CONV_2D",
        "AVERAGE_POOL_2D",
        "CONV_2D",
        "RESHAPE",
        "FULLY_CONNECTED",
        "SOFTMAX",
    ]
    assert manifest["bytes"] == len(data)
    assert manifest["sha256"] == hashlib.sha256(data).hexdigest()


def test_example_model_generator_is_deterministic() -> None:
    pytest.importorskip("flatbuffers")
    pytest.importorskip("ai_edge_litert")
    generator_path = Path(__file__).resolve().parent.parent / "tools" / "gen_example_model.py"
    spec = importlib.util.spec_from_file_location("hpx_example_model_generator", generator_path)
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)

    packaged = Path(examples.__file__).parent / "data" / "models" / "tiny_cnn.tflite"

    assert generator.generate() == packaged.read_bytes()


def test_example_model_has_fixed_batch_size_one() -> None:
    pytest.importorskip("ai_edge_litert")
    from ai_edge_litert.interpreter import Interpreter

    interpreter = Interpreter(model_path=str(hpx.examples.tiny_cnn()))
    interpreter.allocate_tensors()

    for detail in (*interpreter.get_input_details(), *interpreter.get_output_details()):
        assert detail["shape"][0] == 1
        assert detail["shape_signature"][0] == 1


def test_ambiq_vela_ini_materializes_and_parses(tmp_path: Path, monkeypatch) -> None:
    import configparser

    monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path))

    path = hpx.examples.ambiq_vela_ini()

    assert path.is_file()
    assert hpx.examples.ambiq_vela_ini() == path
    parser = configparser.ConfigParser()
    assert parser.read(path)
    for section in (
        "System_Config.Ambiq_ULP_SRAM",
        "System_Config.Ambiq_LP_SRAM",
        "System_Config.Ambiq_HP_SRAM",
        "Memory_Mode.Sram_Only",
        "Memory_Mode.Shared_Sram",
    ):
        assert section in parser.sections()
    # Every NPU clock tier runs against the fixed 250 MHz memory fabric on
    # both ports: Sram (arena) and Dram/Axi1 (Shared_Sram constants).
    for section in parser.sections():
        if not section.startswith("System_Config."):
            continue
        cfg = parser[section]
        for scale in ("Sram_clock_scale", "Dram_clock_scale"):
            fabric = float(cfg["core_clock"]) * float(cfg[scale])
            assert fabric == 250e6, (section, scale)


def test_ambiq_vela_ini_quickstart_copy_matches_packaged() -> None:
    packaged = Path(examples.__file__).parent / "data" / "vela" / "ambiq_vela.ini"
    quickstart = (
        Path(__file__).resolve().parent.parent / "examples" / "quickstart" / "ambiq_vela.ini"
    )

    assert quickstart.read_bytes() == packaged.read_bytes()
