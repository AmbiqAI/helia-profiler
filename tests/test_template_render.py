"""Template rendering smoke tests — ensure main.cc.j2 / main_aot.cc.j2
render successfully across the transport + engine matrix after the
dedup refactor introduced shared Jinja partials.

These tests do not compile the output; they verify that:
  * every expected shared block appears exactly once
  * transport-gated includes / helpers appear only when requested
  * linkage (static vs extern hpx_printf) is engine-specific
"""

from __future__ import annotations

import jinja2
import pytest

_env = jinja2.Environment(
    loader=jinja2.PackageLoader("helia_profiler.firmware", "templates"),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


def _render_tflm(
    transport: str = "rtt",
    model_location: str = "mram",
    arena_region: str = "tcm",
    weights_region: str = "mram",
    has_armv8m_pmu: bool = True,
    resolver_mode: str = "all",
    resolver_registrations: list[str] | None = None,
    resource_variable_count: int = 0,
    perf_mode_symbol: str = "NSX_PERF_LOW",
    perf_mode_mhz: int = 96,
) -> str:
    registrations = resolver_registrations or ["r.AddConv2D();", "r.AddSoftmax();"]
    return _env.get_template("main.cc.j2").render(
        engine_header="tensorflow/lite/micro/micro_interpreter.h",
        cmsis_device_header="apollo510.h",
        arena_size=65_536,
        resolver_mode=resolver_mode,
        resolver_max_ops=len(registrations),
        resolver_registrations=registrations,
        resource_variable_count=resource_variable_count,
        iterations=3,
        warmup=1,
        pmu_passes=[{"name": "Cache", "counters": ["ARM_PMU_CPU_CYCLES"]}],
        pmu_pass_names=["Cache"],
        power_sync_enabled=False,
        sync_gpio_pin=91,
        transport=transport,
        model_location=model_location,
        arena_region=arena_region,
        weights_region=weights_region,
        model_size=1024,
        profiling_backends=["dwt", "armv8m-pmu"] if has_armv8m_pmu else ["dwt"],
        has_armv8m_pmu=has_armv8m_pmu,
        perf_mode_symbol=perf_mode_symbol,
        perf_mode_mhz=perf_mode_mhz,
        printf_linkage="",
        heartbeat_enabled=True,
        heartbeat_every_n_ops=4,
        heartbeat_every_ms=0,
    )


def _render_aot(
    transport: str = "rtt",
    arena_region: str = "tcm",
    weights_region: str = "mram",
    arena_regions: list[dict[str, object]] | None = None,
    has_armv8m_pmu: bool = True,
    perf_mode_symbol: str = "NSX_PERF_LOW",
    perf_mode_mhz: int = 96,
) -> str:
    return _env.get_template("main_aot.cc.j2").render(
        aot_prefix="fake",
        cmsis_device_header="apollo510.h",
        aot_op_manifest=[{"index": 0, "op_name": "CONV_2D"}],
        iterations=3,
        warmup=1,
        pmu_passes=[{"name": "Cache", "counters": ["ARM_PMU_CPU_CYCLES"]}],
        pmu_pass_names=["Cache"],
        power_sync_enabled=False,
        sync_gpio_pin=91,
        transport=transport,
        arena_region=arena_region,
        weights_region=weights_region,
        arena_regions=arena_regions or [],
        allocate_arenas=False,
        extreme_mode=False,
        profiling_backends=["dwt", "armv8m-pmu"] if has_armv8m_pmu else ["dwt"],
        has_armv8m_pmu=has_armv8m_pmu,
        perf_mode_symbol=perf_mode_symbol,
        perf_mode_mhz=perf_mode_mhz,
        printf_linkage="static ",
        heartbeat_enabled=True,
        heartbeat_every_n_ops=4,
        heartbeat_every_ms=0,
    )


class TestMainCcRender:
    @pytest.mark.parametrize("transport", ["rtt", "usb_cdc", "stdio"])
    def test_renders_without_error(self, transport: str):
        out = _render_tflm(transport=transport)
        assert "hpx_printf" in out
        assert "sync_gpio_init" in out
        assert "dwt_init" in out

    def test_tflm_hpx_printf_is_extern_linkage(self):
        out = _render_tflm(transport="rtt")
        # void hpx_printf with no "static " prefix (extern so hpx_pmu_profiler.cc
        # can link to it).
        assert "void hpx_printf(" in out
        assert "static void hpx_printf(" not in out

    def test_rtt_transport_includes_drain_helper(self):
        out = _render_tflm(transport="rtt")
        assert "SEGGER_RTT_Write" in out
        assert "hpx_rtt_drain" in out

    def test_auto_resolver_mode_embeds_selected_registrations(self):
        out = _render_tflm(
            transport="rtt",
            resolver_mode="auto",
            resolver_registrations=["r.AddConv2D();", "r.AddFullyConnected();"],
        )
        assert "Auto mode narrows registrations" in out
        assert "r.AddConv2D();" in out
        assert "r.AddFullyConnected();" in out
        assert "r.AddSoftmax();" not in out

    def test_resource_variable_models_render_resource_variable_runtime(self):
        out = _render_tflm(
            transport="rtt",
            resource_variable_count=2,
        )
        assert "#include \"tensorflow/lite/micro/micro_allocator.h\"" in out
        assert "#include \"tensorflow/lite/micro/micro_resource_variable.h\"" in out
        assert "kNumResourceVariables = 2" in out
        assert "MicroResourceVariables::Create(allocator, kNumResourceVariables)" in out

    def test_clock_mode_renders_selected_perf_mode(self):
        out = _render_tflm(transport="rtt", perf_mode_symbol="NSX_PERF_HIGH", perf_mode_mhz=250)
        assert "sys_cfg.perf_mode = NSX_PERF_HIGH;  // 250 MHz" in out

    def test_aot_clock_mode_renders_selected_perf_mode(self):
        out = _render_aot(transport="rtt", perf_mode_symbol="NSX_PERF_HIGH", perf_mode_mhz=250)
        assert "sys_cfg.perf_mode = NSX_PERF_HIGH;  // 250 MHz" in out

    def test_usb_transport_includes_timer_helpers(self):
        out = _render_tflm(transport="usb_cdc")
        assert "usb_timer_pause" in out
        assert "usb_timer_resume" in out
        assert "nsx_usb_send" in out

    def test_rtt_transport_excludes_usb_timer(self):
        out = _render_tflm(transport="rtt")
        assert "usb_timer_pause" not in out
        assert "nsx_usb_send" not in out

    def test_shared_blocks_appear_exactly_once(self):
        out = _render_tflm(transport="rtt")
        # After dedup, each shared helper must render once (not twice).
        assert out.count("static inline void dwt_init(void)") == 1
        assert out.count("static inline void sync_gpio_init(void)") == 1

    def test_external_power_sync_uses_nsx_gpio(self):
        out = _env.get_template("main.cc.j2").render(
            engine_header="tensorflow/lite/micro/micro_interpreter.h",
            cmsis_device_header="apollo510.h",
            arena_size=65_536,
            iterations=3,
            warmup=1,
            pmu_passes=[{"name": "Cache", "counters": ["ARM_PMU_CPU_CYCLES"]}],
            pmu_pass_names=["Cache"],
            power_sync_enabled=True,
            sync_gpio_pin=42,
            transport="rtt",
            model_location="mram",
            arena_region="tcm",
            weights_region="mram",
            model_size=1024,
            printf_linkage="",
            heartbeat_enabled=True,
            heartbeat_every_n_ops=4,
            heartbeat_every_ms=0,
        )
        assert '#include "nsx_gpio.h"' in out
        assert "nsx_gpio_init" in out
        assert "nsx_gpio_write" in out
        assert "am_hal_gpio_" not in out
        # nsx-core now owns ns_core_initialized(); the firmware must not
        # redefine it (that would be a duplicate symbol at link time).
        assert "ns_core_initialized" not in out

    def test_psram_model_location_skips_model_data_header(self):
        out = _render_tflm(transport="rtt", model_location="psram", weights_region="psram")
        assert "#include \"model_data.h\"" not in out
        assert "nsx_psram.h" in out

    def test_psram_weights_override_skips_model_data_header(self):
        out = _render_tflm(transport="rtt", model_location="auto", weights_region="psram")
        assert "#include \"model_data.h\"" not in out
        assert "nsx_psram.h" in out

    def test_dwt_only_render_avoids_armv8m_pmu_headers(self):
        out = _render_tflm(transport="rtt", has_armv8m_pmu=False)
        assert "nsx_pmu_utils.h" not in out
        assert "g_profiler.Init(0);" in out


class TestMainAotCcRender:
    @pytest.mark.parametrize("transport", ["rtt", "usb_cdc", "stdio"])
    def test_renders_without_error(self, transport: str):
        out = _render_aot(transport=transport)
        assert "fake_model_invoke" in out or "fake_model" in out
        assert "sync_gpio_init" in out
        assert "dwt_init" in out

    def test_aot_hpx_printf_is_static(self):
        out = _render_aot(transport="rtt")
        assert "static void hpx_printf(" in out

    def test_usb_transport_includes_timer_helpers(self):
        out = _render_aot(transport="usb_cdc")
        assert "usb_timer_pause" in out
        assert "nsx_usb_send" in out

    def test_rtt_transport_includes_drain(self):
        out = _render_aot(transport="rtt")
        assert "hpx_rtt_drain" in out

    def test_shared_blocks_appear_exactly_once(self):
        out = _render_aot(transport="rtt")
        assert out.count("static inline void dwt_init(void)") == 1
        assert out.count("static inline void sync_gpio_init(void)") == 1

    def test_psram_arena_regions_use_nsx_psram_api(self):
        out = _render_aot(
            transport="rtt",
            arena_region="psram",
            arena_regions=[
                {
                    "region_id": 0,
                    "placement": "psram",
                    "alignment": 64,
                    "size": 4096,
                    "blob_filename": None,
                }
            ],
        )
        assert "nsx_psram.h" in out
        assert "nsx_psram_default_config(&psram_cfg);" in out

    def test_aot_op_manifest_embedded(self):
        out = _render_aot(transport="rtt")
        assert "CONV_2D" in out

    def test_dwt_only_aot_render_avoids_armv8m_pmu_api(self):
        out = _render_aot(transport="rtt", has_armv8m_pmu=False)
        assert "nsx_pmu_utils.h" not in out
        assert "nsx_pmu_map.h" not in out
        assert "ARM_PMU_CPU_CYCLES" in out
        assert "nsx_pmu_reset_counters" not in out
        assert "g_op_start_cyccnt" in out
