"""Tests for firmware generation, build, and flash."""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.config import load_config
from helia_profiler.firmware import (
    _board_module_name,
    _model_to_header,
    _resolve_module_list,
    build_app,
    generate_app,
)
from helia_profiler.pipeline import PipelineContext
from helia_profiler.stages.s01_resolve_platform import ResolvePlatformStage
from helia_profiler.stages.s02b_plan_memory import PlanMemoryStage
from helia_profiler.stages.s02_prepare_engine import PrepareEngineStage


def _make_ctx(
    tmp_path: Path,
    fake_dist: Path,
    *,
    engine: str = "helia-rt",
    board: str = "apollo510_evb",
) -> PipelineContext:
    model = tmp_path / "model.tflite"
    # Minimal valid-looking TFLite flatbuffer (just bytes for testing)
    model.write_bytes(b"\x1c\x00\x00\x00TFL3" + b"\x00" * 100)
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": engine, "config": {"dist_path": str(fake_dist)}},
            "target": {"board": board},
            "work_dir": str(tmp_path / "work"),
        },
    )
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    return PipelineContext(config=config, work_dir=work_dir)


class TestBoardModuleName:
    def test_apollo510_evb(self):
        assert _board_module_name("apollo510_evb") == "nsx-board-apollo510-evb"

    def test_apollo3p_evb(self):
        assert _board_module_name("apollo3p_evb") == "nsx-board-apollo3p-evb"


class TestResolveModuleList:
    def test_r5_tier(self):
        modules = _resolve_module_list("apollo510_evb", "r5")
        assert "nsx-ambiqsuite-r5" in modules
        assert "nsx-ambiq-hal-r5" in modules
        assert "nsx-ambiq-bsp-r5" in modules
        assert "nsx-core" in modules
        assert "nsx-pmu-armv8m" in modules
        assert "nsx-board-apollo510-evb" in modules

    def test_r4_tier(self):
        modules = _resolve_module_list("apollo4p_evb", "r4")
        assert "nsx-ambiqsuite-r4" in modules
        assert "nsx-board-apollo4p-evb" in modules

    def test_r3_tier(self):
        modules = _resolve_module_list("apollo3p_evb", "r3")
        assert "nsx-ambiqsuite-r3" in modules

    def test_bad_tier_raises(self):
        from helia_profiler.errors import FirmwareError

        with pytest.raises(FirmwareError, match="Unknown SDK tier"):
            _resolve_module_list("board", "r99")


class TestModelToHeader:
    def test_basic(self, tmp_path: Path):
        model = tmp_path / "test.tflite"
        model.write_bytes(bytes(range(24)))
        header = _model_to_header(model)
        assert "model_data[]" in header
        assert "model_data_len = 24" in header
        assert "0x00" in header
        assert "0x17" in header

    def test_alignment(self, tmp_path: Path):
        model = tmp_path / "test.tflite"
        model.write_bytes(b"\xab\xcd")
        header = _model_to_header(model)
        assert "alignas(16)" in header


class TestGenerateApp:
    def test_creates_app_structure(self, tmp_path: Path, fake_dist: Path):
        ctx = _make_ctx(tmp_path, fake_dist)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        assert app_dir.is_dir()
        assert (app_dir / "CMakeLists.txt").exists()
        assert (app_dir / "nsx.yml").exists()
        assert (app_dir / "cmake" / "nsx" / "modules.cmake").exists()
        assert (app_dir / "src" / "main.cc").exists()
        assert (app_dir / "src" / "model_data.h").exists()
        assert (app_dir / "src" / "hpx_pmu_profiler.h").exists()
        assert (app_dir / "src" / "hpx_pmu_profiler.cc").exists()

    def test_heliart_wrapper_module_copied(self, tmp_path: Path, fake_dist: Path):
        ctx = _make_ctx(tmp_path, fake_dist)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        heliart_mod = app_dir / "modules" / "nsx-heliart"
        assert heliart_mod.is_dir()
        assert (heliart_mod / "nsx-module.yaml").exists()
        assert (heliart_mod / "CMakeLists.txt").exists()

    def test_nsx_yml_contains_board(self, tmp_path: Path, fake_dist: Path):
        ctx = _make_ctx(tmp_path, fake_dist)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        nsx_yml = (app_dir / "nsx.yml").read_text()
        assert "apollo510_evb" in nsx_yml
        assert "apollo510" in nsx_yml

    def test_modules_cmake_contains_modules(self, tmp_path: Path, fake_dist: Path):
        ctx = _make_ctx(tmp_path, fake_dist)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        modules_cmake = (app_dir / "cmake" / "nsx" / "modules.cmake").read_text()
        assert "nsx-core" in modules_cmake
        assert "nsx-pmu-armv8m" in modules_cmake

    def test_main_cc_contains_profiler(self, tmp_path: Path, fake_dist: Path):
        ctx = _make_ctx(tmp_path, fake_dist)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        main_cc = (app_dir / "src" / "main.cc").read_text()
        assert "HPX_START" in main_cc
        assert "HPX_END" in main_cc
        assert "HpxPmuProfiler" in main_cc

    def test_cmakelists_links_heliart(self, tmp_path: Path, fake_dist: Path):
        ctx = _make_ctx(tmp_path, fake_dist)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        cmake = (app_dir / "CMakeLists.txt").read_text()
        assert "nsx::heliart" in cmake

    def test_idempotent(self, tmp_path: Path, fake_dist: Path):
        ctx = _make_ctx(tmp_path, fake_dist)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir1 = generate_app(ctx)
        app_dir2 = generate_app(ctx)
        assert app_dir1 == app_dir2
        assert (app_dir2 / "src" / "main.cc").exists()

    def test_model_data_embedded(self, tmp_path: Path, fake_dist: Path):
        ctx = _make_ctx(tmp_path, fake_dist)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        model_h = (app_dir / "src" / "model_data.h").read_text()
        assert "model_data[]" in model_h
        assert "model_data_len" in model_h

    def test_gpio_sync_disabled_by_default(self, tmp_path: Path, fake_dist: Path):
        ctx = _make_ctx(tmp_path, fake_dist)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        main_cc = (app_dir / "src" / "main.cc").read_text()
        assert "kPowerSyncEnabled = false" in main_cc

    def test_gpio_sync_enabled_with_power(self, tmp_path: Path, fake_dist: Path):
        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x1c\x00\x00\x00TFL3" + b"\x00" * 100)
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt", "config": {"dist_path": str(fake_dist)}},
                "target": {"board": "apollo510_evb"},
                "power": {"enabled": True, "mode": "external", "sync_gpio_pin": 42},
                "work_dir": str(tmp_path / "work"),
            },
        )
        work_dir = tmp_path / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        ctx = PipelineContext(config=config, work_dir=work_dir)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        main_cc = (app_dir / "src" / "main.cc").read_text()
        assert "kPowerSyncEnabled = true" in main_cc
        assert "kSyncGpioPin = 42" in main_cc
        assert "sync_gpio_high" in main_cc
        assert "sync_gpio_low" in main_cc

    def test_gpio_sync_not_enabled_for_internal(self, tmp_path: Path, fake_dist: Path):
        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x1c\x00\x00\x00TFL3" + b"\x00" * 100)
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt", "config": {"dist_path": str(fake_dist)}},
                "target": {"board": "apollo510_evb"},
                "power": {"enabled": True, "mode": "internal"},
                "work_dir": str(tmp_path / "work"),
            },
        )
        work_dir = tmp_path / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        ctx = PipelineContext(config=config, work_dir=work_dir)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        main_cc = (app_dir / "src" / "main.cc").read_text()
        assert "kPowerSyncEnabled = false" in main_cc

    def test_weights_psram_override_skips_model_header_and_links_peripherals(
        self, tmp_path: Path, fake_dist: Path
    ):
        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x1c\x00\x00\x00TFL3" + b"\x00" * 100)
        config = load_config(
            None,
            {
                "model": {
                    "path": str(model),
                },
                "engine": {
                    "type": "helia-rt",
                    "config": {
                        "dist_path": str(fake_dist),
                        "runtime_weights_location": "psram",
                    },
                },
                "target": {"board": "apollo510_evb"},
                "work_dir": str(tmp_path / "work"),
            },
        )
        work_dir = tmp_path / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        ctx = PipelineContext(config=config, work_dir=work_dir)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        PlanMemoryStage().run(ctx)
        app_dir = generate_app(ctx)

        main_cc = (app_dir / "src" / "main.cc").read_text()
        cmake = (app_dir / "CMakeLists.txt").read_text()
        assert (app_dir / "src" / "model_data.h").exists() is False
        assert "ns_peripherals_psram.h" in main_cc
        assert "nsx::peripherals" in cmake


class TestBuildApp:
    def test_frozen_skips_lock_and_uses_frozen_sync(self, tmp_path: Path, fake_dist: Path, monkeypatch):
        ctx = _make_ctx(tmp_path, fake_dist)
        ResolvePlatformStage().run(ctx)
        app_dir = tmp_path / "app"
        build_dir = app_dir / "build" / "apollo510_evb"
        build_dir.mkdir(parents=True, exist_ok=True)
        binary = build_dir / "hpx_profiler.bin"
        binary.write_bytes(b"bin")
        object.__setattr__(ctx, "firmware_dir", app_dir)
        object.__setattr__(ctx.config, "frozen", True)

        lock_calls: list[tuple] = []
        sync_calls: list[dict] = []

        monkeypatch.setattr("helia_profiler.firmware.nsx_cli.lock", lambda *args, **kwargs: lock_calls.append((args, kwargs)))
        monkeypatch.setattr(
            "helia_profiler.firmware.nsx_cli.sync",
            lambda *args, **kwargs: sync_calls.append(kwargs),
        )
        monkeypatch.setattr("helia_profiler.firmware.nsx_cli.configure", lambda *args, **kwargs: None)
        monkeypatch.setattr("helia_profiler.firmware.nsx_cli.build", lambda *args, **kwargs: None)

        out_build_dir, out_binary = build_app(ctx)

        assert lock_calls == []
        assert sync_calls == [{"frozen": True, "timeout_s": ctx.config.timeouts.configure_s}]
        assert out_build_dir == build_dir
        assert out_binary == binary


class TestKwsModel:
    """Tests using the real KWS reference model."""

    def test_kws_model_to_header(self, kws_model: Path):
        header = _model_to_header(kws_model)
        assert "model_data[]" in header
        assert "model_data_len = 53936" in header
        assert "kws_ref_model.tflite" in header

    def test_kws_firmware_generation(self, tmp_path: Path, kws_model: Path, fake_dist: Path):
        config = load_config(
            None,
            {
                "model": {"path": str(kws_model)},
                "engine": {"type": "helia-rt", "config": {"dist_path": str(fake_dist)}},
                "target": {"board": "apollo510_evb"},
                "work_dir": str(tmp_path / "work"),
            },
        )
        work_dir = tmp_path / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        ctx = PipelineContext(config=config, work_dir=work_dir)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        assert (app_dir / "src" / "model_data.h").exists()
        model_h = (app_dir / "src" / "model_data.h").read_text()
        assert "model_data_len = 53936" in model_h

        # Verify main.cc references the profiler
        main_cc = (app_dir / "src" / "main.cc").read_text()
        assert "MicroMutableOpResolver" in main_cc
        assert "get_resolver" in main_cc
