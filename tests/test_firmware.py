"""Tests for firmware generation, build, and flash."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from helia_profiler.config import load_config
from helia_profiler.errors import FirmwareError
from helia_profiler.firmware import (
    _board_module_name,
    _model_to_header,
    _resolve_module_list,
    _resolve_module_specs,
    build_app,
    generate_app,
)
from helia_profiler.pipeline import PipelineContext
from helia_profiler.stages.s01_resolve_platform import ResolvePlatformStage
from helia_profiler.stages.s02b_plan_memory import PlanMemoryStage
from helia_profiler.stages.s02_prepare_engine import PrepareEngineStage


def _fake_starter_profiles() -> dict[str, dict]:
    unified_project = "nsx-ambiq-sdk"
    return {
        "apollo510_evb": {
            "modules": [
                "nsx-ambiqsuite-r5",
                "nsx-ambiq-hal-r5",
                "nsx-ambiq-bsp-r5",
                "nsx-soc-hal",
                "nsx-cmsis-startup",
                "nsx-core",
                "nsx-tooling",
                "nsx-board-apollo510-evb",
            ],
            "project_overrides": {
                unified_project: {
                    "revision": "r5.3",
                    "metadata": "modules/nsx-ambiqsuite-r5/nsx-module.yaml",
                }
            },
            "module_overrides": {
                "nsx-ambiqsuite-r5": {"project": unified_project},
                "nsx-ambiq-hal-r5": {"project": unified_project},
                "nsx-ambiq-bsp-r5": {"project": unified_project},
                "nsx-cmsis-core": {"project": unified_project},
                "nsx-gpio": {"project": unified_project},
                "nsx-interrupt": {"project": unified_project},
                "nsx-soc-hal": {"project": unified_project},
                "nsx-cmsis-startup": {"project": unified_project},
                "nsx-core": {"project": unified_project},
            },
        },
        "apollo4p_evb": {
            "modules": [
                "nsx-ambiqsuite-r4",
                "nsx-board-apollo4p-evb",
            ],
            "project_overrides": {
                unified_project: {
                    "revision": "r4.0",
                    "metadata": "modules/nsx-ambiqsuite-r4/nsx-module.yaml",
                }
            },
            "module_overrides": {
                "nsx-ambiqsuite-r4": {"project": unified_project},
                "nsx-cmsis-core": {"project": unified_project},
            },
        },
        "apollo3p_evb": {
            "modules": [
                "nsx-ambiqsuite-r3",
                "nsx-board-apollo3p-evb",
            ],
            "project_overrides": {
                unified_project: {
                    "revision": "r3.0",
                    "metadata": "modules/nsx-ambiqsuite-r3/nsx-module.yaml",
                }
            },
            "module_overrides": {
                "nsx-ambiqsuite-r3": {"project": unified_project},
                "nsx-cmsis-core": {"project": unified_project},
            },
        },
        "atomiq110_fpga_turbo": {
            "modules": [
                "nsx-ambiqsuite-r6",
                "nsx-ambiq-hal-r6",
                "nsx-ambiq-bsp-r6",
                "nsx-soc-hal",
                "nsx-cmsis-startup",
                "nsx-core",
                "nsx-tooling",
                "nsx-board-atomiq110-fpga-turbo",
            ],
            "project_overrides": {
                "nsx-ambiq-sdk-r6": {
                    "revision": "r6.0",
                    "metadata": "modules/nsx-ambiqsuite-r6/nsx-module.yaml",
                }
            },
            "module_overrides": {
                "nsx-ambiqsuite-r6": {"project": "nsx-ambiq-sdk-r6"},
                "nsx-ambiq-hal-r6": {"project": "nsx-ambiq-sdk-r6"},
                "nsx-ambiq-bsp-r6": {"project": "nsx-ambiq-sdk-r6"},
                "nsx-cmsis-core": {"project": "nsx-ambiq-sdk-r6"},
                "nsx-soc-hal": {"project": "nsx-ambiq-sdk-r6"},
                "nsx-cmsis-startup": {"project": "nsx-ambiq-sdk-r6"},
                "nsx-core": {"project": "nsx-ambiq-sdk-r6"},
            },
        },
    }


@pytest.fixture(autouse=True)
def fake_nsx_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    profiles = _fake_starter_profiles()
    module_projects = {
        "nsx-tooling": "neuralspotx",
        "nsx-board-apollo510-evb": "neuralspotx",
        "nsx-board-apollo4p-evb": "neuralspotx",
        "nsx-board-apollo3p-evb": "neuralspotx",
        "nsx-board-atomiq110-fpga-turbo": "neuralspotx",
        "nsx-pmu-armv8m": "nsx-pmu-armv8m",
    }

    monkeypatch.setattr(
        "helia_profiler.firmware.nsx_cli.starter_profile",
        lambda board: profiles.get(board),
    )
    monkeypatch.setattr(
        "helia_profiler.firmware.nsx_cli.registry_module_project",
        lambda name: module_projects.get(name),
    )


@pytest.fixture(autouse=True)
def fake_segger_rtt_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide the explicit SEGGER_RTT_PATH required by firmware generation."""
    rtt_root = tmp_path / "segger-rtt"
    rtt_dir = rtt_root / "RTT"
    config_dir = rtt_root / "Config"
    rtt_dir.mkdir(parents=True)
    config_dir.mkdir()
    (rtt_dir / "SEGGER_RTT.c").write_text("// fake RTT source\n")
    (rtt_dir / "SEGGER_RTT.h").write_text("// fake RTT header\n")
    (rtt_dir / "SEGGER_RTT_ConfDefaults.h").write_text("// fake RTT conf defaults\n")
    (config_dir / "SEGGER_RTT_Conf.h").write_text("// fake RTT config\n")
    monkeypatch.setenv("SEGGER_RTT_PATH", str(rtt_root))


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
        assert "nsx-harness" not in modules
        assert "nsx-utils" not in modules

    def test_r4_tier(self):
        modules = _resolve_module_list("apollo4p_evb", "r4")
        assert "nsx-ambiqsuite-r4" in modules
        assert "nsx-board-apollo4p-evb" in modules
        assert "nsx-pmu-armv8m" not in modules

    def test_r3_tier(self):
        modules = _resolve_module_list("apollo3p_evb", "r3")
        assert "nsx-ambiqsuite-r3" in modules

    def test_bad_tier_raises(self):
        from helia_profiler.errors import FirmwareError

        with pytest.raises(FirmwareError, match="Unknown SDK tier"):
            _resolve_module_list("board", "r99")

    def test_r5_sdk_modules_resolve_to_monorepo_project(self):
        modules = _resolve_module_specs("apollo510_evb", "r5")
        by_name = {module.name: module for module in modules}
        assert by_name["nsx-ambiqsuite-r5"].project == "nsx-ambiq-sdk"
        assert by_name["nsx-ambiq-hal-r5"].project == "nsx-ambiq-sdk"
        assert by_name["nsx-ambiq-bsp-r5"].project == "nsx-ambiq-sdk"

    def test_common_modules_resolve_to_monorepo_project(self):
        # Modules hpx still consumes directly from the SDK monorepo must be
        # owned by the unified nsx-ambiq-sdk project rather than legacy
        # standalone same-name projects.
        modules = _resolve_module_specs("apollo510_evb", "r5")
        by_name = {module.name: module for module in modules}
        for name in (
            "nsx-cmsis-core",
            "nsx-soc-hal",
            "nsx-cmsis-startup",
            "nsx-core",
        ):
            assert by_name[name].project == "nsx-ambiq-sdk", name

    def test_armv8m_pmu_module_stays_standalone_on_r5(self):
        modules = _resolve_module_specs("apollo510_evb", "r5")
        by_name = {module.name: module for module in modules}
        assert by_name["nsx-pmu-armv8m"].project == "nsx-pmu-armv8m"

    def test_board_and_tooling_modules_resolve_to_neuralspotx(self):
        modules = _resolve_module_specs("atomiq110_fpga_turbo", "r6")
        by_name = {module.name: module for module in modules}
        assert by_name["nsx-board-atomiq110-fpga-turbo"].project == "neuralspotx"
        assert by_name["nsx-tooling"].project == "neuralspotx"

    def test_r6_unmigrated_pmu_falls_back_to_standalone(self):
        # The r6 monorepo does not yet vendor nsx-pmu-armv8m, so ownership must
        # fall back to the standalone project rather than over-pin it onto
        # nsx-ambiq-sdk-r6.
        modules = _resolve_module_specs("atomiq110_fpga_turbo", "r6")
        by_name = {module.name: module for module in modules}
        assert by_name["nsx-pmu-armv8m"].project == "nsx-pmu-armv8m"
        # Modules the r6 monorepo does vendor stay on the monorepo project.
        assert by_name["nsx-core"].project == "nsx-ambiq-sdk-r6"
        assert by_name["nsx-soc-hal"].project == "nsx-ambiq-sdk-r6"

    def test_power_and_perf_are_not_required_modules(self):
        modules = _resolve_module_list("apollo510_evb", "r5")
        assert "nsx-power" not in modules
        assert "nsx-perf" not in modules


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

        heliart_mod = app_dir / "modules" / "helia-rt"
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

    def test_ap4_generation_avoids_armv8m_pmu_module_and_link_target(self, tmp_path: Path, fake_dist: Path):
        ctx = _make_ctx(tmp_path, fake_dist, board="apollo4p_evb")
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        modules_cmake = (app_dir / "cmake" / "nsx" / "modules.cmake").read_text()
        cmake = (app_dir / "CMakeLists.txt").read_text()
        profiler_h = (app_dir / "src" / "hpx_pmu_profiler.h").read_text()
        assert "nsx-pmu-armv8m" not in modules_cmake
        assert "nsx::pmu_armv8m" not in cmake
        assert "nsx_pmu_utils.h" not in profiler_h

    def test_ap4_generation_rejects_unsupported_mve_group(self, tmp_path: Path, fake_dist: Path):
        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x1c\x00\x00\x00TFL3" + b"\x00" * 100)
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt", "config": {"dist_path": str(fake_dist)}},
                "target": {"board": "apollo4p_evb"},
                "profiling": {"pmu_counters": {"mve": "default"}},
                "work_dir": str(tmp_path / "work"),
            },
        )
        work_dir = tmp_path / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        ctx = PipelineContext(config=config, work_dir=work_dir)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)

        with pytest.raises(FirmwareError, match="not supported"):
            generate_app(ctx)

    def test_main_cc_contains_profiler(self, tmp_path: Path, fake_dist: Path):
        ctx = _make_ctx(tmp_path, fake_dist)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        main_cc = (app_dir / "src" / "main.cc").read_text()
        assert "HPX_START" in main_cc
        assert "HPX_END" in main_cc
        assert "HpxPmuProfiler" in main_cc
        assert "ns_ambiqsuite_harness.h" not in main_cc
        assert "am_util_delay_ms" not in main_cc

    def test_cmakelists_links_heliart(self, tmp_path: Path, fake_dist: Path):
        ctx = _make_ctx(tmp_path, fake_dist)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        cmake = (app_dir / "CMakeLists.txt").read_text()
        assert "nsx::helia_rt" in cmake

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
        nsx_yml = (app_dir / "nsx.yml").read_text()
        cmake = (app_dir / "CMakeLists.txt").read_text()
        assert "kPowerSyncEnabled = true" in main_cc
        assert "kSyncGpioPin = 42" in main_cc
        assert "sync_gpio_high" in main_cc
        assert "sync_gpio_low" in main_cc
        assert "nsx_gpio_init" in main_cc
        assert "nsx_gpio_write" in main_cc
        assert "am_hal_gpio_" not in main_cc
        assert "nsx-gpio" in nsx_yml
        assert "nsx-interrupt" in nsx_yml
        assert "nsx::gpio" in cmake

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
        nsx_yml = yaml.safe_load((app_dir / "nsx.yml").read_text())
        cmake = (app_dir / "CMakeLists.txt").read_text()
        module_names = {module["name"] for module in nsx_yml["modules"]}
        assert "kPowerSyncEnabled = false" in main_cc
        assert "nsx_gpio_init" not in main_cc
        assert "nsx-gpio" not in module_names
        assert "nsx-interrupt" not in module_names
        assert "nsx::gpio" not in cmake

    def test_weights_psram_override_skips_model_header_and_links_psram_module(
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
        assert "nsx_psram.h" in main_cc
        assert "nsx::psram" in cmake


class TestBuildApp:
    def test_non_frozen_updates_lock_before_sync(self, tmp_path: Path, fake_dist: Path, monkeypatch):
        ctx = _make_ctx(tmp_path, fake_dist)
        ResolvePlatformStage().run(ctx)
        app_dir = tmp_path / "app"
        build_dir = app_dir / "build" / "apollo510_evb"
        build_dir.mkdir(parents=True, exist_ok=True)
        binary = build_dir / "hpx_profiler.bin"
        binary.write_bytes(b"bin")
        object.__setattr__(ctx, "firmware_dir", app_dir)

        lock_calls: list[dict] = []
        sync_calls: list[dict] = []

        monkeypatch.setattr(
            "helia_profiler.firmware.nsx_cli.lock",
            lambda *args, **kwargs: lock_calls.append(kwargs),
        )
        monkeypatch.setattr(
            "helia_profiler.firmware.nsx_cli.sync",
            lambda *args, **kwargs: sync_calls.append(kwargs),
        )
        monkeypatch.setattr("helia_profiler.firmware.nsx_cli.configure", lambda *args, **kwargs: None)
        monkeypatch.setattr("helia_profiler.firmware.nsx_cli.build", lambda *args, **kwargs: None)

        out_build_dir, out_binary = build_app(ctx)

        assert lock_calls == [{"update": True, "timeout_s": ctx.config.timeouts.configure_s, "verbose": 0}]
        assert sync_calls == [{"timeout_s": ctx.config.timeouts.configure_s, "verbose": 0}]
        assert out_build_dir == build_dir
        assert out_binary == binary

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
        assert sync_calls == [{"frozen": True, "timeout_s": ctx.config.timeouts.configure_s, "verbose": 0}]
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


class TestNsxModuleOverrides:
    """Tests for the build.nsx_modules override mechanism."""

    def _make_ctx_with_overrides(
        self, tmp_path: Path, fake_dist: Path, build_overrides: dict,
    ) -> PipelineContext:
        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x1c\x00\x00\x00TFL3" + b"\x00" * 100)
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt", "config": {"dist_path": str(fake_dist)}},
                "target": {"board": "apollo510_evb"},
                "work_dir": str(tmp_path / "work"),
                "build": build_overrides,
            },
        )
        work_dir = tmp_path / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        return PipelineContext(config=config, work_dir=work_dir)

    def test_channel_override_in_nsx_yml(self, tmp_path: Path, fake_dist: Path):
        ctx = self._make_ctx_with_overrides(tmp_path, fake_dist, {"channel": "dev"})
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        nsx_yml = (app_dir / "nsx.yml").read_text()
        assert "channel: dev" in nsx_yml

    def test_default_channel_uses_board_channel(self, tmp_path: Path, fake_dist: Path):
        ctx = self._make_ctx_with_overrides(tmp_path, fake_dist, {})
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        nsx_yml = (app_dir / "nsx.yml").read_text()
        assert "channel: stable" in nsx_yml

    def test_preview_board_defaults_to_preview_channel(self, tmp_path: Path, fake_dist: Path):
        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x1c\x00\x00\x00TFL3" + b"\x00" * 100)
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt", "config": {"dist_path": str(fake_dist)}},
                "target": {"board": "atomiq110_fpga_turbo"},
                "work_dir": str(tmp_path / "work"),
            },
        )
        work_dir = tmp_path / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        ctx = PipelineContext(config=config, work_dir=work_dir)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        nsx_yml = (app_dir / "nsx.yml").read_text()
        assert "channel: preview" in nsx_yml

    def test_version_override_in_nsx_yml(self, tmp_path: Path, fake_dist: Path):
        ctx = self._make_ctx_with_overrides(
            tmp_path, fake_dist,
            {"nsx_modules": {"nsx-ambiqsuite-r5": {"version": "2.0.0"}}},
        )
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        nsx_yml = (app_dir / "nsx.yml").read_text()
        assert 'version: "2.0.0"' in nsx_yml
        assert "project: nsx-ambiq-sdk" in nsx_yml
        # A version override targets the whole owning project, so every module
        # vendored by nsx-ambiq-sdk receives it.
        sdk_module_count = sum(
            1
            for spec in _resolve_module_specs("apollo510_evb", "r5")
            if spec.project == "nsx-ambiq-sdk"
        )
        assert nsx_yml.count('version: "2.0.0"') == sdk_module_count

    def test_ref_override_in_nsx_yml(self, tmp_path: Path, fake_dist: Path):
        ctx = self._make_ctx_with_overrides(
            tmp_path, fake_dist,
            {"nsx_modules": {"nsx-ambiq-hal-r5": {"ref": "feat/new-soc"}}},
        )
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        nsx_yml = (app_dir / "nsx.yml").read_text()
        assert "ref: feat/new-soc" in nsx_yml
        assert "project: nsx-ambiq-sdk" in nsx_yml
        # A ref override targets the whole owning project, so every module
        # vendored by nsx-ambiq-sdk receives it.
        sdk_module_count = sum(
            1
            for spec in _resolve_module_specs("apollo510_evb", "r5")
            if spec.project == "nsx-ambiq-sdk"
        )
        assert nsx_yml.count("ref: feat/new-soc") == sdk_module_count

    def test_module_registry_emitted_in_nsx_yml(self, tmp_path: Path, fake_dist: Path):
        # The generated manifest must carry the profile's module_registry so the
        # app's effective registry agrees with the per-module project pins and a
        # real `nsx lock` passes alignment validation.
        ctx = self._make_ctx_with_overrides(tmp_path, fake_dist, {})
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        nsx_yml = yaml.safe_load((app_dir / "nsx.yml").read_text())
        registry = nsx_yml["module_registry"]
        assert registry["projects"]["nsx-ambiq-sdk"]["revision"]
        assert "nsx-pmu-armv8m" not in registry.get("modules", {})

    def test_path_override_installs_local_module(self, tmp_path: Path, fake_dist: Path):
        # Create a fake local module with nsx-module.yaml
        local_bsp = tmp_path / "my-bsp"
        local_bsp.mkdir()
        (local_bsp / "nsx-module.yaml").write_text("schema_version: 1\nmodule:\n  name: nsx-ambiq-bsp-r5\n")
        (local_bsp / "CMakeLists.txt").write_text("# custom BSP cmake\n")

        ctx = self._make_ctx_with_overrides(
            tmp_path, fake_dist,
            {"nsx_modules": {"nsx-ambiq-bsp-r5": {"path": str(local_bsp)}}},
        )
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        # Module should be installed as local
        installed = app_dir / "modules" / "nsx-ambiq-bsp-r5"
        assert installed.is_dir()
        assert (installed / "nsx-module.yaml").is_file()
        assert (installed / "CMakeLists.txt").read_text() == "# custom BSP cmake\n"

        # nsx.yml should mark it as local
        nsx_yml = (app_dir / "nsx.yml").read_text()
        # The module entry should have local: true
        assert "local: true" in nsx_yml

    def test_path_override_missing_yaml_raises(self, tmp_path: Path, fake_dist: Path):
        from helia_profiler.errors import FirmwareError

        bad_dir = tmp_path / "bad-module"
        bad_dir.mkdir()
        # No nsx-module.yaml

        ctx = self._make_ctx_with_overrides(
            tmp_path, fake_dist,
            {"nsx_modules": {"nsx-ambiq-bsp-r5": {"path": str(bad_dir)}}},
        )
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)

        with pytest.raises(FirmwareError, match="nsx-module.yaml"):
            generate_app(ctx)

    def test_unmatched_override_logs_warning(
        self, tmp_path: Path, fake_dist: Path, caplog,
    ):
        """Override for a module not in the build should emit a warning."""
        import logging

        ctx = self._make_ctx_with_overrides(
            tmp_path, fake_dist,
            {"nsx_modules": {"nsx-nonexistent-module": {"ref": "main"}}},
        )
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)

        with caplog.at_level(logging.WARNING):
            generate_app(ctx)

        assert any(
            "nsx-nonexistent-module" in rec.message for rec in caplog.records
        )
