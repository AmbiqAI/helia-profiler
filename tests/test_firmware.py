"""Tests for firmware generation, build, and flash."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from helia_profiler.config import load_config
from helia_profiler.errors import FirmwareError
from helia_profiler.firmware import (
    _board_module_name,
    _find_segger_rtt_dir,
    _model_to_header,
    _resolve_module_list,
    _resolve_module_specs,
    build_app,
    flash_app,
    generate_app,
)
from helia_profiler.pipeline import PipelineContext
from helia_profiler.stages.resolve_platform import ResolvePlatformStage
from helia_profiler.stages.plan_memory import PlanMemoryStage
from helia_profiler.stages.prepare_engine import PrepareEngineStage


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
                "nsx-board-apollo510-evb",
                "nsx-cmsis-core",
                "nsx-core",
                "nsx-tooling",
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
                "nsx-ambiq-usb-r5": {"project": unified_project},
                "nsx-cmsis-core": {"project": unified_project},
                "nsx-gpio": {"project": unified_project},
                "nsx-interrupt": {"project": unified_project},
                "nsx-psram": {"project": unified_project},
                "nsx-soc-hal": {"project": unified_project},
                "nsx-cmsis-startup": {"project": unified_project},
                "nsx-core": {"project": unified_project},
                "nsx-usb": {"project": unified_project},
            },
        },
        "apollo510b_evb": {
            "modules": [
                "nsx-ambiqsuite",
                "nsx-ambiq-hal",
                "nsx-ambiq-bsp",
                "nsx-soc-hal",
                "nsx-cmsis-startup",
                "nsx-board-apollo510b-evb",
                "nsx-cmsis-core",
                "nsx-core",
                "nsx-tooling",
            ],
            "project_overrides": {
                unified_project: {
                    "revision": "main",
                    "metadata": "modules/nsx-ambiqsuite/nsx-module.yaml",
                }
            },
            "module_overrides": {
                "nsx-ambiqsuite": {"project": unified_project},
                "nsx-ambiq-hal": {"project": unified_project},
                "nsx-ambiq-bsp": {"project": unified_project},
                "nsx-ambiq-usb": {"project": unified_project},
                "nsx-cmsis-core": {"project": unified_project},
                "nsx-gpio": {"project": unified_project},
                "nsx-interrupt": {"project": unified_project},
                "nsx-psram": {"project": unified_project},
                "nsx-soc-hal": {"project": unified_project},
                "nsx-cmsis-startup": {"project": unified_project},
                "nsx-core": {"project": unified_project},
                "nsx-usb": {"project": unified_project},
            },
        },
        "apollo4p_evb": {
            "modules": [
                "nsx-ambiqsuite-r4",
                "nsx-cmsis-core",
                "nsx-core",
                "nsx-tooling",
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
                "nsx-ambiq-usb-r4": {"project": unified_project},
                "nsx-cmsis-core": {"project": unified_project},
                "nsx-core": {"project": unified_project},
                "nsx-gpio": {"project": unified_project},
                "nsx-interrupt": {"project": unified_project},
                "nsx-psram": {"project": unified_project},
                "nsx-usb": {"project": unified_project},
            },
        },
        "apollo3p_evb": {
            "modules": [
                "nsx-ambiqsuite-r3",
                "nsx-cmsis-core",
                "nsx-core",
                "nsx-tooling",
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
                "nsx-core": {"project": unified_project},
                "nsx-gpio": {"project": unified_project},
                "nsx-interrupt": {"project": unified_project},
                "nsx-psram": {"project": unified_project},
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
        "nsx-pmu-armv8m": "nsx-pmu-armv8m",
    }
    projects = {
        "neuralspotx": {
            "name": "neuralspotx",
            "url": "https://github.com/AmbiqAI/neuralspotx.git",
            "revision": "stable-tag",
            "path": "neuralspotx",
        },
        "nsx-ambiq-sdk": {
            "name": "nsx-ambiq-sdk",
            "url": "https://github.com/AmbiqAI/nsx-ambiq-sdk.git",
            "revision": "r5.3",
            "path": "modules/nsx-ambiq-sdk",
        },
    }

    monkeypatch.setattr(
        "helia_profiler.firmware.nsx_cli.starter_profile",
        lambda board: profiles.get(board),
    )
    monkeypatch.setattr(
        "helia_profiler.firmware.nsx_cli.registry_module_project",
        lambda name: module_projects.get(name),
    )
    monkeypatch.setattr(
        "helia_profiler.firmware.nsx_cli.registry_project",
        lambda name: projects.get(name),
    )


@pytest.fixture(autouse=True)
def fake_segger_rtt_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide the explicit SEGGER_RTT_PATH required by firmware generation."""
    rtt_root = tmp_path / "segger-rtt"
    rtt_dir = rtt_root / "RTT"
    config_dir = rtt_root / "Config"
    rtt_dir.mkdir(parents=True)
    config_dir.mkdir()
    (rtt_dir / "SEGGER_RTT.c").write_text(
        '#include "SEGGER_RTT.h"\n'
        '\n'
        '#if SEGGER_RTT_CPU_CACHE_LINE_SIZE\n'
        '  #if ((defined __GNUC__) || (defined __clang__))\n'
        '    SEGGER_RTT_CB _SEGGER_RTT                                                             __attribute__ ((aligned (SEGGER_RTT_CPU_CACHE_LINE_SIZE)));\n'
        '    static char   _acUpBuffer  [SEGGER_RTT__ROUND_UP_2_CACHE_LINE_SIZE(BUFFER_SIZE_UP)]   __attribute__ ((aligned (SEGGER_RTT_CPU_CACHE_LINE_SIZE)));\n'
        '    static char   _acDownBuffer[SEGGER_RTT__ROUND_UP_2_CACHE_LINE_SIZE(BUFFER_SIZE_DOWN)] __attribute__ ((aligned (SEGGER_RTT_CPU_CACHE_LINE_SIZE)));\n'
        '  #elif (defined __ICCARM__)\n'
        '    #pragma data_alignment=SEGGER_RTT_CPU_CACHE_LINE_SIZE\n'
        '    SEGGER_RTT_CB _SEGGER_RTT;\n'
        '    #pragma data_alignment=SEGGER_RTT_CPU_CACHE_LINE_SIZE\n'
        '    static char   _acUpBuffer  [SEGGER_RTT__ROUND_UP_2_CACHE_LINE_SIZE(BUFFER_SIZE_UP)];\n'
        '    #pragma data_alignment=SEGGER_RTT_CPU_CACHE_LINE_SIZE\n'
        '    static char   _acDownBuffer[SEGGER_RTT__ROUND_UP_2_CACHE_LINE_SIZE(BUFFER_SIZE_DOWN)];\n'
        '  #else\n'
        '    #error "Don\'t know how to place _SEGGER_RTT, _acUpBuffer, _acDownBuffer cache-line aligned"\n'
        '  #endif\n'
        '#else\n'
        '  SEGGER_RTT_PUT_CB_SECTION(SEGGER_RTT_CB_ALIGN(SEGGER_RTT_CB _SEGGER_RTT));\n'
        '  SEGGER_RTT_PUT_BUFFER_SECTION(SEGGER_RTT_BUFFER_ALIGN(static char _acUpBuffer  [BUFFER_SIZE_UP]));\n'
        '  SEGGER_RTT_PUT_BUFFER_SECTION(SEGGER_RTT_BUFFER_ALIGN(static char _acDownBuffer[BUFFER_SIZE_DOWN]));\n'
        '#endif\n'
    )
    (rtt_dir / "SEGGER_RTT.h").write_text("// fake RTT header\n")
    (rtt_dir / "SEGGER_RTT_ConfDefaults.h").write_text("// fake RTT conf defaults\n")
    (config_dir / "SEGGER_RTT_Conf.h").write_text("// fake RTT config\n")
    monkeypatch.setenv("SEGGER_RTT_PATH", str(rtt_root))


def _make_fake_rtt_root(path: Path) -> Path:
    rtt_dir = path / "RTT"
    config_dir = path / "Config"
    rtt_dir.mkdir(parents=True)
    config_dir.mkdir()
    (rtt_dir / "SEGGER_RTT.c").write_text("// fake RTT source\n")
    (rtt_dir / "SEGGER_RTT.h").write_text("// fake RTT header\n")
    (config_dir / "SEGGER_RTT_Conf.h").write_text("// fake RTT config\n")
    return path


def test_find_segger_rtt_dir_prefers_explicit_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    explicit = _make_fake_rtt_root(tmp_path / "explicit")
    auto = _make_fake_rtt_root(tmp_path / "auto")
    monkeypatch.setenv("SEGGER_RTT_PATH", str(explicit))
    monkeypatch.setattr("helia_profiler.firmware._segger_rtt_candidates", lambda: (auto,))

    assert _find_segger_rtt_dir() == explicit


def test_find_segger_rtt_dir_expands_explicit_env_user_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = tmp_path / "home"
    home.mkdir()
    explicit = _make_fake_rtt_root(home / "explicit")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SEGGER_RTT_PATH", "~/explicit")

    assert _find_segger_rtt_dir() == explicit.resolve()


def test_find_segger_rtt_dir_auto_detects_common_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    candidate = _make_fake_rtt_root(tmp_path / "examples" / "quickstart" / "RTT")
    monkeypatch.delenv("SEGGER_RTT_PATH", raising=False)
    monkeypatch.setattr("helia_profiler.firmware._segger_rtt_candidates", lambda: (candidate,))

    assert _find_segger_rtt_dir() == candidate.resolve()


def test_find_segger_rtt_dir_rejects_invalid_explicit_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    invalid = tmp_path / "not-rtt"
    invalid.mkdir()
    auto = _make_fake_rtt_root(tmp_path / "auto")
    monkeypatch.setenv("SEGGER_RTT_PATH", str(invalid))
    monkeypatch.setattr("helia_profiler.firmware._segger_rtt_candidates", lambda: (auto,))

    with pytest.raises(FirmwareError, match="SEGGER_RTT_PATH"):
        _find_segger_rtt_dir()


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
    def test_apollo510_profile_modules(self):
        modules = _resolve_module_list("apollo510_evb")
        assert "nsx-ambiqsuite-r5" in modules
        assert "nsx-ambiq-hal-r5" in modules
        assert "nsx-ambiq-bsp-r5" in modules
        assert "nsx-core" in modules
        assert "nsx-pmu-armv8m" in modules
        assert "nsx-board-apollo510-evb" in modules
        assert "nsx-harness" not in modules
        assert "nsx-utils" not in modules

    def test_apollo4_profile_modules(self):
        modules = _resolve_module_list("apollo4p_evb")
        assert "nsx-ambiqsuite-r4" in modules
        assert "nsx-board-apollo4p-evb" in modules
        assert "nsx-pmu-armv8m" not in modules

    def test_apollo3_profile_modules(self):
        modules = _resolve_module_list("apollo3p_evb")
        assert "nsx-ambiqsuite-r3" in modules

    def test_r5_sdk_modules_resolve_to_monorepo_project(self):
        modules = _resolve_module_specs("apollo510_evb")
        by_name = {module.name: module for module in modules}
        assert by_name["nsx-ambiqsuite-r5"].project == "nsx-ambiq-sdk"
        assert by_name["nsx-ambiq-hal-r5"].project == "nsx-ambiq-sdk"
        assert by_name["nsx-ambiq-bsp-r5"].project == "nsx-ambiq-sdk"

    def test_common_modules_resolve_to_monorepo_project(self):
        # Modules hpx still consumes directly from the SDK monorepo must be
        # owned by the unified nsx-ambiq-sdk project rather than legacy
        # standalone same-name projects.
        modules = _resolve_module_specs("apollo510_evb")
        by_name = {module.name: module for module in modules}
        for name in (
            "nsx-cmsis-core",
            "nsx-soc-hal",
            "nsx-cmsis-startup",
            "nsx-core",
        ):
            assert by_name[name].project == "nsx-ambiq-sdk", name

    def test_armv8m_pmu_module_stays_standalone_on_apollo510(self):
        modules = _resolve_module_specs("apollo510_evb")
        by_name = {module.name: module for module in modules}
        assert by_name["nsx-pmu-armv8m"].project == "nsx-pmu-armv8m"

    def test_board_and_tooling_modules_resolve_to_neuralspotx(self):
        modules = _resolve_module_specs("apollo510_evb")
        by_name = {module.name: module for module in modules}
        assert by_name["nsx-board-apollo510-evb"].project == "neuralspotx"
        assert by_name["nsx-tooling"].project == "neuralspotx"

    def test_power_and_perf_are_not_required_modules(self):
        modules = _resolve_module_list("apollo510_evb")
        assert "nsx-power" not in modules
        assert "nsx-perf" not in modules

    def test_custom_board_can_reuse_builtin_starter_profile(self):
        modules = _resolve_module_specs("apollo510_lab", profile_board="apollo510_evb")
        by_name = {module.name: module for module in modules}
        assert by_name["nsx-board-apollo510-evb"].project == "neuralspotx"
        assert by_name["nsx-core"].project == "nsx-ambiq-sdk"

    def test_armv8m_pmu_module_uses_config_registry_for_custom_board_soc(self):
        """The PMU-module fallback must resolve the custom board's *own* SoC
        (via ``registry=``), not silently fall through to the built-in
        ``profile_board``'s SoC.

        ``apollo3p`` has no ``armv8m-pmu`` backend while the reused
        ``apollo510_evb`` starter profile's board does. Without threading the
        config's platform registry through, ``get_soc_for_board`` cannot
        resolve the custom board name at all and falls back to
        ``profile_board``'s (apollo510) SoC — wrongly appending
        ``nsx-pmu-armv8m`` for an AP3-family board.
        """
        from helia_profiler.config import load_config

        config = load_config(
            None,
            {
                "model": {"path": "m.tflite"},
                "engine": {"type": "helia-rt"},
                "target": {
                    "board": "apollo3p_custom_board",
                    "custom_socs": {
                        "apollo3p_custom": {"based_on": "apollo3p"},
                    },
                    "custom_boards": {
                        "apollo3p_custom_board": {
                            "soc": "apollo3p_custom",
                            "channel": "dev",
                            "starter_profile_board": "apollo510_evb",
                        }
                    },
                },
            },
        )

        # With the config's platform registry threaded through, the custom
        # board's own (AP3) SoC is used and the PMU module is *not* added.
        modules_with_registry = _resolve_module_specs(
            "apollo3p_custom_board",
            profile_board="apollo510_evb",
            registry=config.platform_registry,
        )
        names_with_registry = {m.name for m in modules_with_registry}
        assert "nsx-pmu-armv8m" not in names_with_registry

        # Without a registry, the custom board name cannot resolve at all and
        # the fallback wrongly picks up the reused apollo510_evb SoC's PMU
        # requirement.
        modules_without_registry = _resolve_module_specs(
            "apollo3p_custom_board", profile_board="apollo510_evb"
        )
        names_without_registry = {m.name for m in modules_without_registry}
        assert "nsx-pmu-armv8m" in names_without_registry


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
        assert (heliart_mod / "nsx" / "CMakeLists.txt").exists()
        heliart_alias = app_dir / "modules" / "nsx-helia-rt"
        assert heliart_alias.is_dir()
        assert (heliart_alias / "nsx-module.yaml").exists()
        assert (heliart_alias / "CMakeLists.txt").exists()
        assert (heliart_alias / "nsx" / "CMakeLists.txt").exists()

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

    def test_source_build_installs_heliart_under_module_name(
        self,
        tmp_path: Path,
        fake_source_tree: Path,
        fake_cmsis_nn: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("CMSIS_NN_PATH", str(fake_cmsis_nn))

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x1c\x00\x00\x00TFL3" + b"\x00" * 100)
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {
                    "type": "helia-rt",
                    "config": {"source_path": str(fake_source_tree)},
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
        app_dir = generate_app(ctx)

        heliart_module = app_dir / "modules" / "helia-rt"
        heliart_alias = app_dir / "modules" / "nsx-helia-rt"
        assert (heliart_module / "nsx-module.yaml").is_file()
        assert (heliart_module / "CMakeLists.txt").is_file()
        assert (heliart_module / "nsx" / "CMakeLists.txt").is_file()
        assert (heliart_alias / "nsx-module.yaml").is_file()
        assert (heliart_alias / "CMakeLists.txt").is_file()
        assert (heliart_alias / "nsx" / "CMakeLists.txt").is_file()

    def test_ap4_generation_avoids_armv8m_pmu_module_and_link_target(
        self, tmp_path: Path, fake_dist: Path
    ):
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
        profiler_h = (app_dir / "src" / "hpx_pmu_profiler.h").read_text()
        cmake = (app_dir / "CMakeLists.txt").read_text()
        assert "HPX_START" in main_cc
        assert "HPX_END" in main_cc
        assert "HpxPmuProfiler" in main_cc
        assert "ns_ambiqsuite_harness.h" not in main_cc
        assert "am_util_delay_ms" not in main_cc
        assert '#include "nsx_core.h"' in main_cc
        assert '#include "nsx_system.h"' in main_cc
        assert '#include "nsx_core.h"' in profiler_h
        assert "#if !defined(NSX_TRY) && defined(NS_TRY)" in main_cc
        assert "NSX_TRY(nsx_system_init(&sys_cfg)" in main_cc
        assert "nsx_system_development" not in main_cc
        assert "nsx::presets" not in cmake

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
        assert "kSyncGpioPin      = 42" in main_cc
        assert "hpx_sync_window_begin" in main_cc
        assert "hpx_sync_window_end" in main_cc
        assert "nsx_gpio_init" in main_cc
        assert "nsx_gpio_write" in main_cc
        assert "am_hal_gpio_" not in main_cc
        assert "nsx-gpio" in nsx_yml
        assert "nsx-interrupt" in nsx_yml
        assert "nsx::gpio" in cmake

    def test_rtt_generation_flushes_cache_after_buffer_config(self, tmp_path: Path, fake_dist: Path):
        ctx = _make_ctx(tmp_path, fake_dist)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        main_cc = (app_dir / "src" / "main.cc").read_text()
        cmake = (app_dir / "CMakeLists.txt").read_text()
        assert '#define HPX_CACHE_FLUSH() ((void)nsx_cache_flush())' in main_cc
        assert '#define HPX_CACHE_PUBLISH_WRITES() ((void)nsx_cache_publish_writes())' in main_cc
        assert '#define HPX_CACHE_INVALIDATE_OBSERVED() ((void)nsx_cache_invalidate_observed_data())' in main_cc
        assert '#define HPX_CLEAN_DCACHE() HPX_CACHE_PUBLISH_WRITES()' in main_cc
        assert '#define HPX_INVAL_DCACHE() HPX_CACHE_INVALIDATE_OBSERVED()' in main_cc
        assert '#include "am_hal_cachectrl.h"' not in main_cc
        assert 'SEGGER_RTT_ConfigUpBuffer(0, "HPX", NULL, 0,' in main_cc
        assert "HPX_CLEAN_DCACHE();" in main_cc.split('SEGGER_RTT_ConfigUpBuffer(0, "HPX", NULL, 0,', 1)[1]
        assert "static void hpx_rtt_write_lossless(const char *buf, unsigned len)" in main_cc
        assert "SEGGER_RTT_Write(0, buf, len);\n    HPX_CLEAN_DCACHE();" in main_cc
        assert "SEGGER_RTT_Write(0, line_buf, (unsigned)n);\n            HPX_CLEAN_DCACHE();" in main_cc
        assert "BUFFER_SIZE_UP=32768" in cmake

    def test_apollo4_rtt_generation_avoids_ap5_dcache_calls(self, tmp_path: Path, fake_dist: Path):
        ctx = _make_ctx(tmp_path, fake_dist)
        object.__setattr__(ctx.config.target, "board", "apollo4p_evb")
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        main_cc = (app_dir / "src" / "main.cc").read_text()
        assert '#define HPX_CACHE_FLUSH() ((void)nsx_cache_flush())' in main_cc
        assert '#define HPX_CACHE_PUBLISH_WRITES() ((void)nsx_cache_publish_writes())' in main_cc
        assert "#if NSX_CACHE_HAS_INVALIDATE_OBSERVED" in main_cc
        assert "#define HPX_CLEAN_DCACHE() HPX_CACHE_PUBLISH_WRITES()" in main_cc
        assert "#define HPX_INVAL_DCACHE() HPX_CACHE_INVALIDATE_OBSERVED()" in main_cc

    def test_atfe_rtt_generation_uses_smaller_buffer(self, tmp_path: Path, fake_dist: Path):
        ctx = _make_ctx(tmp_path, fake_dist)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        object.__setattr__(ctx.config.target, "toolchain", "atfe")
        object.__setattr__(ctx.config.target, "transport", "rtt")
        app_dir = generate_app(ctx)

        cmake = (app_dir / "CMakeLists.txt").read_text()
        assert "BUFFER_SIZE_UP=12288" in cmake

    def test_rtt_generation_places_segger_buffers_by_cache_family(
        self, tmp_path: Path, fake_dist: Path
    ):
        rtt_root = Path(os.environ["SEGGER_RTT_PATH"])
        (rtt_root / "RTT" / "SEGGER_RTT.c").write_text(
            '#include "SEGGER_RTT.h"\n'
            '\n'
            '#if SEGGER_RTT_CPU_CACHE_LINE_SIZE\n'
            '  #if ((defined __GNUC__) || (defined __clang__))\n'
            '    SEGGER_RTT_CB _SEGGER_RTT                                                             __attribute__ ((aligned (SEGGER_RTT_CPU_CACHE_LINE_SIZE)));\n'
            '    static char   _acUpBuffer  [SEGGER_RTT__ROUND_UP_2_CACHE_LINE_SIZE(BUFFER_SIZE_UP)]   __attribute__ ((aligned (SEGGER_RTT_CPU_CACHE_LINE_SIZE)));\n'
            '    static char   _acDownBuffer[SEGGER_RTT__ROUND_UP_2_CACHE_LINE_SIZE(BUFFER_SIZE_DOWN)] __attribute__ ((aligned (SEGGER_RTT_CPU_CACHE_LINE_SIZE)));\n'
            '  #elif (defined __ICCARM__)\n'
            '    #pragma data_alignment=SEGGER_RTT_CPU_CACHE_LINE_SIZE\n'
            '    SEGGER_RTT_CB _SEGGER_RTT;\n'
            '    #pragma data_alignment=SEGGER_RTT_CPU_CACHE_LINE_SIZE\n'
            '    static char   _acUpBuffer  [SEGGER_RTT__ROUND_UP_2_CACHE_LINE_SIZE(BUFFER_SIZE_UP)];\n'
            '    #pragma data_alignment=SEGGER_RTT_CPU_CACHE_LINE_SIZE\n'
            '    static char   _acDownBuffer[SEGGER_RTT__ROUND_UP_2_CACHE_LINE_SIZE(BUFFER_SIZE_DOWN)];\n'
            '  #else\n'
            '    #error "Don\'t know how to place _SEGGER_RTT, _acUpBuffer, _acDownBuffer cache-line aligned"\n'
            '  #endif\n'
            '#else\n'
            '  SEGGER_RTT_PUT_CB_SECTION(SEGGER_RTT_CB_ALIGN(SEGGER_RTT_CB _SEGGER_RTT));\n'
            '  SEGGER_RTT_PUT_BUFFER_SECTION(SEGGER_RTT_BUFFER_ALIGN(static char _acUpBuffer  [BUFFER_SIZE_UP]));\n'
            '  SEGGER_RTT_PUT_BUFFER_SECTION(SEGGER_RTT_BUFFER_ALIGN(static char _acDownBuffer[BUFFER_SIZE_DOWN]));\n'
            '#endif\n'
        )

        ctx = _make_ctx(tmp_path, fake_dist)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        # The SEGGER source is copied verbatim — placement is driven entirely by
        # the generated config header. Cache-coherent Cortex-M55 parts keep the
        # buffers in non-cached TCM (no SEGGER_RTT_SECTION override) so SWD reads
        # stay coherent; cacheless Cortex-M4 parts fall through to .sram_bss.
        rtt_c = (app_dir / "src" / "rtt" / "SEGGER_RTT.c").read_text()
        assert "SEGGER_RTT_PUT_CB_SECTION(" in rtt_c
        assert "SEGGER_RTT_PUT_BUFFER_SECTION(" in rtt_c

        conf = (app_dir / "src" / "rtt" / "Config" / "SEGGER_RTT_Conf.h").read_text()
        assert '#include "nsx_mem.h"' in conf
        # M55 / Apollo5 family is gated to non-cached TCM (.bss default).
        assert "defined(AM_PART_APOLLO510)" in conf
        assert "defined(AM_PART_APOLLO330P)" in conf
        # Cacheless parts still relocate the buffers into shared SRAM.
        assert "#elif NSX_MEM__HAS_SRAM_BSS" in conf
        assert "#define SEGGER_RTT_SECTION NSX_MEM__SEC_SRAM_BSS" in conf

    def test_rtt_generation_raises_when_segger_layout_is_unexpected(
        self, tmp_path: Path, fake_dist: Path
    ):
        rtt_root = Path(os.environ["SEGGER_RTT_PATH"])
        (rtt_root / "RTT" / "SEGGER_RTT.c").write_text(
            '#include "SEGGER_RTT.h"\n'
            'static int not_the_expected_layout = 1;\n'
        )

        ctx = _make_ctx(tmp_path, fake_dist)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)

        with pytest.raises(FirmwareError, match="Failed to patch SEGGER_RTT.c"):
            generate_app(ctx)

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
    def test_non_frozen_updates_lock_before_sync(
        self, tmp_path: Path, fake_dist: Path, monkeypatch
    ):
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
        monkeypatch.setattr(
            "helia_profiler.firmware.nsx_cli.configure", lambda *args, **kwargs: None
        )
        monkeypatch.setattr("helia_profiler.firmware.nsx_cli.build", lambda *args, **kwargs: None)

        out_build_dir, out_binary = build_app(ctx)

        assert lock_calls == [
            {"update": True, "timeout_s": ctx.config.timeouts.configure_s, "verbose": 0}
        ]
        assert sync_calls == [{"timeout_s": ctx.config.timeouts.configure_s, "verbose": 0}]
        assert out_build_dir == build_dir
        assert out_binary == binary


class TestFlashApp:
    def test_prefers_resolved_jlink_serial(self, tmp_path: Path, fake_dist: Path, monkeypatch):
        ctx = _make_ctx(tmp_path, fake_dist)
        ResolvePlatformStage().run(ctx)
        ctx.firmware_dir = tmp_path / "app"
        ctx.firmware_dir.mkdir(parents=True)
        ctx.resolved_jlink_serial = "1160002204"

        captured: dict[str, object] = {}

        def fake_flash(*args, **kwargs):
            captured.update(kwargs)

        monkeypatch.setattr("helia_profiler.firmware.nsx_cli.flash", fake_flash)

        flash_app(ctx)

        assert captured["jlink_serial"] == "1160002204"

    def test_falls_back_to_configured_jlink_serial(
        self, tmp_path: Path, fake_dist: Path, monkeypatch
    ):
        ctx = _make_ctx(tmp_path, fake_dist)
        ResolvePlatformStage().run(ctx)
        ctx.firmware_dir = tmp_path / "app"
        ctx.firmware_dir.mkdir(parents=True)
        object.__setattr__(ctx.config.target, "jlink_serial", "0011223344")

        captured: dict[str, object] = {}

        def fake_flash(*args, **kwargs):
            captured.update(kwargs)

        monkeypatch.setattr("helia_profiler.firmware.nsx_cli.flash", fake_flash)

        flash_app(ctx)

        assert captured["jlink_serial"] == "0011223344"

    def test_frozen_skips_lock_and_uses_frozen_sync(
        self, tmp_path: Path, fake_dist: Path, monkeypatch
    ):
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

        monkeypatch.setattr(
            "helia_profiler.firmware.nsx_cli.lock",
            lambda *args, **kwargs: lock_calls.append((args, kwargs)),
        )
        monkeypatch.setattr(
            "helia_profiler.firmware.nsx_cli.sync",
            lambda *args, **kwargs: sync_calls.append(kwargs),
        )
        monkeypatch.setattr(
            "helia_profiler.firmware.nsx_cli.configure", lambda *args, **kwargs: None
        )
        monkeypatch.setattr("helia_profiler.firmware.nsx_cli.build", lambda *args, **kwargs: None)

        out_build_dir, out_binary = build_app(ctx)

        assert lock_calls == []
        assert sync_calls == [
            {"frozen": True, "timeout_s": ctx.config.timeouts.configure_s, "verbose": 0}
        ]
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
        self,
        tmp_path: Path,
        fake_dist: Path,
        build_overrides: dict,
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

    def test_default_build_uses_main_for_nsx_and_unified_sdk(self, tmp_path: Path, fake_dist: Path):
        ctx = self._make_ctx_with_overrides(tmp_path, fake_dist, {})
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        nsx_yml = (app_dir / "nsx.yml").read_text()
        specs = _resolve_module_specs("apollo510_evb")
        sdk_module_count = sum(1 for spec in specs if spec.project == "nsx-ambiq-sdk")
        nsx_module_count = sum(1 for spec in specs if spec.project == "neuralspotx")
        assert nsx_yml.count("project: nsx-ambiq-sdk\n  ref: main") == sdk_module_count
        assert nsx_yml.count("project: neuralspotx\n  ref: main") == nsx_module_count

    def test_preview_board_defaults_to_preview_channel(self, tmp_path: Path, fake_dist: Path):
        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x1c\x00\x00\x00TFL3" + b"\x00" * 100)
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt", "config": {"dist_path": str(fake_dist)}},
                "target": {"board": "apollo4p_evb"},
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

    def test_usb_cdc_adds_provider_usb_module(self, tmp_path: Path, fake_dist: Path):
        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x1c\x00\x00\x00TFL3" + b"\x00" * 100)
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt", "config": {"dist_path": str(fake_dist)}},
                "target": {"board": "apollo510_evb", "transport": "usb_cdc"},
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
        assert "- name: nsx-ambiq-usb-r5" in nsx_yml
        assert "- name: nsx-usb" in nsx_yml

        manifest = yaml.safe_load(nsx_yml)
        registry = manifest["module_registry"]
        assert registry["modules"]["nsx-ambiq-usb-r5"]["project"] == "nsx-ambiq-sdk"
        assert registry["modules"]["nsx-usb"]["project"] == "nsx-ambiq-sdk"

    def test_usb_cdc_adds_suffixless_provider_usb_module(self, tmp_path: Path, fake_dist: Path):
        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x1c\x00\x00\x00TFL3" + b"\x00" * 100)
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt", "config": {"dist_path": str(fake_dist)}},
                "target": {"board": "apollo510b_evb", "transport": "usb_cdc"},
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
        assert "- name: nsx-ambiq-usb" in nsx_yml
        assert "- name: nsx-usb" in nsx_yml

        manifest = yaml.safe_load(nsx_yml)
        registry = manifest["module_registry"]
        assert registry["modules"]["nsx-ambiq-usb"]["project"] == "nsx-ambiq-sdk"
        assert registry["modules"]["nsx-usb"]["project"] == "nsx-ambiq-sdk"

    def test_psram_modules_resolve_through_profile_overrides(self, tmp_path: Path, fake_dist: Path):
        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x1c\x00\x00\x00TFL3" + b"\x00" * 100)
        config = load_config(
            None,
            {
                "model": {"path": str(model), "model_location": "psram"},
                "engine": {"type": "helia-rt", "config": {"dist_path": str(fake_dist)}},
                "target": {"board": "apollo510_evb", "transport": "rtt"},
                "work_dir": str(tmp_path / "work"),
            },
        )
        work_dir = tmp_path / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        ctx = PipelineContext(config=config, work_dir=work_dir)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        manifest = yaml.safe_load((app_dir / "nsx.yml").read_text())
        registry = manifest["module_registry"]
        assert registry["modules"]["nsx-interrupt"]["project"] == "nsx-ambiq-sdk"
        assert registry["modules"]["nsx-psram"]["project"] == "nsx-ambiq-sdk"

    def test_power_sync_modules_resolve_through_profile_overrides(self, tmp_path: Path, fake_dist: Path):
        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x1c\x00\x00\x00TFL3" + b"\x00" * 100)
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt", "config": {"dist_path": str(fake_dist)}},
                "target": {"board": "apollo510_evb", "transport": "rtt"},
                "power": {"enabled": True, "mode": "external"},
                "work_dir": str(tmp_path / "work"),
            },
        )
        work_dir = tmp_path / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        ctx = PipelineContext(config=config, work_dir=work_dir)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        manifest = yaml.safe_load((app_dir / "nsx.yml").read_text())
        registry = manifest["module_registry"]
        assert registry["modules"]["nsx-gpio"]["project"] == "nsx-ambiq-sdk"
        assert registry["modules"]["nsx-interrupt"]["project"] == "nsx-ambiq-sdk"

    def test_version_override_in_nsx_yml(self, tmp_path: Path, fake_dist: Path):
        ctx = self._make_ctx_with_overrides(
            tmp_path,
            fake_dist,
            {"nsx_modules": {"nsx-core": {"version": "2.0.0"}}},
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
            1 for spec in _resolve_module_specs("apollo510_evb") if spec.project == "nsx-ambiq-sdk"
        )
        assert nsx_yml.count('version: "2.0.0"') == sdk_module_count
        assert "project: neuralspotx\n  ref: main" in nsx_yml

    def test_ref_override_in_nsx_yml(self, tmp_path: Path, fake_dist: Path):
        ctx = self._make_ctx_with_overrides(
            tmp_path,
            fake_dist,
            {"nsx_modules": {"nsx-core": {"ref": "feat/new-soc"}}},
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
            1 for spec in _resolve_module_specs("apollo510_evb") if spec.project == "nsx-ambiq-sdk"
        )
        assert nsx_yml.count("ref: feat/new-soc") == sdk_module_count
        assert "project: neuralspotx\n  ref: main" in nsx_yml

    def test_ref_override_aligns_module_registry_revisions(self, tmp_path: Path, fake_dist: Path):
        # Regression: a project ref override must also re-point the per-module
        # `module_registry.modules.<name>.revision` entries. The starter profile
        # pins those to `main`; NSX's lock resolution honours the module-level
        # revision over the project revision, so a stale `main` here drags the
        # whole SDK monorepo back to `main` (and vendors the wrong commit).
        ctx = self._make_ctx_with_overrides(
            tmp_path,
            fake_dist,
            {"nsx_modules": {"nsx-core": {"ref": "feat/new-soc"}}},
        )
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        registry = yaml.safe_load((app_dir / "nsx.yml").read_text())["module_registry"]
        assert registry["projects"]["nsx-ambiq-sdk"]["revision"] == "feat/new-soc"
        sdk_modules = {
            name
            for name, entry in registry["modules"].items()
            if entry.get("project") == "nsx-ambiq-sdk"
        }
        assert sdk_modules, "expected nsx-ambiq-sdk modules in the registry"
        for name in sdk_modules:
            assert registry["modules"][name]["revision"] == "feat/new-soc", name

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
        assert registry["projects"]["nsx-ambiq-sdk"]["revision"] == "main"
        assert registry["projects"]["neuralspotx"]["revision"] == "main"
        assert "nsx-pmu-armv8m" not in registry.get("modules", {})

    def test_path_override_installs_local_module(self, tmp_path: Path, fake_dist: Path):
        # Create a fake local module with nsx-module.yaml
        local_module = tmp_path / "my-nsx-core"
        local_module.mkdir()
        (local_module / "nsx-module.yaml").write_text(
            "schema_version: 1\nmodule:\n  name: nsx-core\n"
        )
        (local_module / "CMakeLists.txt").write_text("# custom nsx-core cmake\n")

        ctx = self._make_ctx_with_overrides(
            tmp_path,
            fake_dist,
            {"nsx_modules": {"nsx-core": {"path": str(local_module)}}},
        )
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        app_dir = generate_app(ctx)

        # Module should be installed as local
        installed = app_dir / "modules" / "nsx-core"
        assert installed.is_dir()
        assert (installed / "nsx-module.yaml").is_file()
        assert (installed / "CMakeLists.txt").read_text() == "# custom nsx-core cmake\n"

        # nsx.yml should mark it as a vendored (local) module under schema v2
        nsx_yml = (app_dir / "nsx.yml").read_text()
        assert "vendored: true" in nsx_yml

    def test_path_override_missing_yaml_raises(self, tmp_path: Path, fake_dist: Path):
        from helia_profiler.errors import FirmwareError

        bad_dir = tmp_path / "bad-module"
        bad_dir.mkdir()
        # No nsx-module.yaml

        ctx = self._make_ctx_with_overrides(
            tmp_path,
            fake_dist,
            {"nsx_modules": {"nsx-core": {"path": str(bad_dir)}}},
        )
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)

        with pytest.raises(FirmwareError, match="nsx-module.yaml"):
            generate_app(ctx)

    def test_unmatched_override_logs_warning(
        self,
        tmp_path: Path,
        fake_dist: Path,
        caplog,
    ):
        """Override for a module not in the build should emit a warning."""
        import logging

        ctx = self._make_ctx_with_overrides(
            tmp_path,
            fake_dist,
            {"nsx_modules": {"nsx-nonexistent-module": {"ref": "main"}}},
        )
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)

        with caplog.at_level(logging.WARNING):
            generate_app(ctx)

        assert any("nsx-nonexistent-module" in rec.message for rec in caplog.records)
