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
    undefined=jinja2.StrictUndefined,
)


def _sample_pmu_passes() -> list[dict[str, object]]:
    return [
        {
            "name": "Cache",
            "custom": False,
            "event_ids": [],
            "counter_names": [
                "ARM_PMU_CPU_CYCLES",
                "ARM_PMU_INST_RETIRED",
                "ARM_PMU_STALL_FRONTEND",
                "ARM_PMU_STALL_BACKEND",
            ],
            "num_counters": 4,
            "c_enum": "NSX_PMU_PRESET_BASIC_CPU",
            "group": "cpu",
        }
    ]


def _render_tflm(
    transport: str = "rtt",
    arena_region: str = "tcm",
    weights_region: str = "mram",
    has_armv8m_pmu: bool = True,
    resolver_mode: str = "all",
    resolver_registrations: list[str] | None = None,
    resource_variable_count: int = 0,
    perf_mode_symbol: str = "NSX_PERF_LOW",
    perf_mode_mhz: int = 96,
    extreme_mode: bool = False,
    usb_serial_marker: str | None = None,
    window_mode: str = "fixed",
    clean_window_probe: str = "infer",
    clean_iters: int = 3,
    power_only: bool = False,
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
        clean_warmup=1,
        clean_iters=clean_iters,
        power_only=power_only,
        window_mode=window_mode,
        window_target_ms=250,
        window_min=10,
        window_max=200,
        clean_window_probe=clean_window_probe,
        pmu_passes=_sample_pmu_passes(),
        pmu_pass_names=["Cache"],
        power_sync_enabled=False,
        sync_gpio_pin=91,
        transport=transport,
        usb_serial_marker=usb_serial_marker,
        arena_region=arena_region,
        weights_region=weights_region,
        model_size=1024,
        profiling_backends=["dwt", "armv8m-pmu"] if has_armv8m_pmu else ["dwt"],
        has_armv8m_pmu=has_armv8m_pmu,
        perf_mode_symbol=perf_mode_symbol,
        perf_mode_mhz=perf_mode_mhz,
        apollo3_burst=False,
        extreme_mode=extreme_mode,
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
    apollo3_burst: bool = False,
    cmsis_device_header: str = "apollo510.h",
    window_mode: str = "fixed",
    clean_window_probe: str = "infer",
    clean_iters: int = 3,
    power_only: bool = False,
) -> str:
    return _env.get_template("main_aot.cc.j2").render(
        aot_prefix="fake",
        cmsis_device_header=cmsis_device_header,
        aot_op_manifest=[{"id": 0, "op_type": "CONV_2D"}],
        iterations=3,
        warmup=1,
        clean_warmup=1,
        clean_iters=clean_iters,
        power_only=power_only,
        window_mode=window_mode,
        window_target_ms=250,
        window_min=10,
        window_max=200,
        clean_window_probe=clean_window_probe,
        pmu_passes=_sample_pmu_passes(),
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
        apollo3_burst=apollo3_burst,
        printf_linkage="static ",
        heartbeat_enabled=True,
        heartbeat_every_n_ops=4,
        heartbeat_every_ms=0,
        pmu_max_ops=4096,
    )


class TestMainCcRender:
    @pytest.mark.parametrize("transport", ["rtt", "usb_cdc", "swo", "stdio"])
    def test_renders_without_error(self, transport: str):
        out = _render_tflm(transport=transport)
        assert "hpx_printf" in out
        assert "hpx_sync_init" in out
        assert "dwt_init" in out

    def test_tflm_hpx_printf_is_extern_linkage(self):
        out = _render_tflm(transport="rtt")
        # void hpx_printf with no "static " prefix (extern so hpx_pmu_profiler.cc
        # can link to it).
        assert "void hpx_printf(" in out
        assert "static void hpx_printf(" not in out

    def test_power_only_routes_recoverable_errors_to_terminal_finalizer(self):
        out = _render_tflm(transport="rtt", power_only=True)
        assert 'hpx_power_terminal_fail("schema", 2U);' in out
        assert 'hpx_power_terminal_fail("resolver", 3U);' in out
        assert 'hpx_power_terminal_fail("allocate", 4U);' in out
        assert out.index("hpx_sync_window_end();") < out.rindex(
            "hpx_power_terminal_report("
        )

    def test_power_only_uart_enables_transport_after_gate(self):
        out = _render_tflm(transport="uart", power_only=True)
        assert "nsx_uart_printf_enable();" in out
        assert out.index("hpx_sync_window_end();") < out.rindex(
            "hpx_power_terminal_report("
        )
        assert "sys_cfg.debug.transport = NSX_DEBUG_NONE;" in out

    def test_power_only_swo_enables_transport_after_gate(self):
        out = _render_tflm(transport="swo", power_only=True)
        assert "nsx_itm_printf_enable();" in out
        assert out.index("hpx_sync_window_end();") < out.rindex(
            "hpx_power_terminal_report("
        )
        assert "sys_cfg.debug.transport = NSX_DEBUG_NONE;" in out

    def test_power_only_usb_initializes_transport_after_gate(self):
        out = _render_tflm(transport="usb_cdc", power_only=True)
        assert "nsx_usb_init(&g_usb_cfg)" in out
        assert out.index("hpx_sync_window_end();") < out.rindex(
            "hpx_power_terminal_report("
        )
        assert "sys_cfg.debug.transport = NSX_DEBUG_NONE;" in out

    def test_rtt_transport_includes_drain_helper(self):
        out = _render_tflm(transport="rtt")
        assert "SEGGER_RTT_Write" in out
        assert "hpx_rtt_drain" in out

    def test_rtt_transport_switches_to_blocking_for_csv_and_end(self):
        out = _render_tflm(transport="rtt")
        # Lossless mode-switch helpers must be defined and used.
        assert "hpx_rtt_set_blocking" in out
        assert "hpx_rtt_set_nonblocking" in out
        # Lossless writes are done by our own cache-coherent writer, not by
        # SEGGER's BLOCK_IF_FIFO_FULL (which deadlocks reading stale RdOff on
        # cached M55 over SWD).
        assert "hpx_rtt_write_lossless" in out
        assert "SEGGER_RTT_MODE_BLOCK_IF_FIFO_FULL" not in out
        # Lossless mode is engaged around the CSV dump and restored afterwards.
        assert out.count("hpx_rtt_set_blocking();") >= 2  # per-iter dump + HPX_END
        assert out.count("hpx_rtt_set_nonblocking();") >= 1

    def test_non_rtt_transport_omits_blocking_switch(self):
        for transport in ("usb_cdc", "swo", "stdio"):
            out = _render_tflm(transport=transport)
            assert "hpx_rtt_set_blocking" not in out
            assert "hpx_rtt_set_nonblocking" not in out

    def test_swo_emits_sync_preamble_before_start(self):
        # SWO has no back-pressure, so the firmware keeps the ITM link warm with
        # a disposable HPX_READY sync preamble until the host is draining, then
        # prints the real header.  This closes the attach race that dropped the
        # HPX_START sentinel.
        out = _render_tflm(transport="swo")
        # Split on the actual sentinel emission (not the explanatory comments
        # that also mention HPX_START).
        preamble = out.split('hpx_printf("\\n--- HPX_START ---\\n")', 1)[0]
        assert "for (int hpx_sync_i = 0; hpx_sync_i < HPX_SWO_SYNC_PREAMBLE_LINES" in preamble
        assert 'hpx_printf("HPX_READY\\n");' in preamble
        assert "nsx_delay_us(HPX_SWO_SYNC_GAP_US);" in preamble

    def test_non_swo_transport_omits_sync_preamble(self):
        # The sync preamble loop is for the lossy ITM/SWO path; RTT (back-
        # pressure) and USB CDC (host-ready DTR signal) have dedicated branches
        # and must not run it.  stdio shares the SWO else-branch by design.
        for transport in ("rtt", "usb_cdc"):
            out = _render_tflm(transport=transport)
            assert "hpx_sync_i < HPX_SWO_SYNC_PREAMBLE_LINES" not in out

    def test_all_exits_route_through_hpx_park(self):
        # Every terminal exit (error paths + HPX_END) must call hpx_park() so the
        # final diagnostic is delivered; no raw __WFI() spin loops should remain
        # in the main entry point.
        for transport in ("rtt", "usb_cdc", "swo", "stdio"):
            out = _render_tflm(transport=transport)
            assert "void hpx_park(void)" in out
            assert "while (1) { __WFI(); }" not in out
            # schema_mismatch + missing_ops + alloc_tensors_failed + HPX_END
            # (psram exit only renders when a region is in PSRAM).
            assert out.count("hpx_park();") >= 4

    def test_rtt_park_drains_before_wfi(self):
        # On RTT the park helper must publish + drain (core still spinning) before
        # entering WFI, because the TCM-resident ring is unreadable to the J-Link
        # once the core sleeps. This is what lets failure messages escape.
        out = _render_tflm(transport="rtt")
        park = out.split("void hpx_park(void)", 1)[1].split("}", 1)[0]
        assert "hpx_rtt_set_blocking();" in park
        assert "hpx_rtt_drain(HPX_RTT_FAIL_DRAIN_MS)" in park
        assert "__WFI();" in park
        assert "HPX_RTT_FAIL_DRAIN_MS" in out

    def test_non_rtt_park_is_plain_wfi(self):
        # Non-RTT transports send synchronously, so park has no drain — just WFI.
        for transport in ("usb_cdc", "swo", "stdio"):
            out = _render_tflm(transport=transport)
            park = out.split("void hpx_park(void)", 1)[1].split("}", 1)[0]
            assert "__WFI();" in park
            assert "hpx_rtt_drain" not in park
            assert "HPX_RTT_FAIL_DRAIN_MS" not in out

    def test_aot_exits_route_through_hpx_park(self):
        # The AOT entry point must also park on every exit (model_init failure +
        # HPX_END at minimum) and drain RTT before WFI.
        out = _render_aot(transport="rtt")
        assert "void hpx_park(void)" in out
        assert "while (1) { __WFI(); }" not in out
        assert out.count("hpx_park();") >= 2
        park = out.split("void hpx_park(void)", 1)[1].split("}", 1)[0]
        assert "hpx_rtt_drain(HPX_RTT_FAIL_DRAIN_MS)" in park

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
        assert '#include "tensorflow/lite/micro/micro_allocator.h"' in out
        assert '#include "tensorflow/lite/micro/micro_resource_variable.h"' in out
        assert "kNumResourceVariables = 2" in out
        assert "MicroResourceVariables::Create(allocator, kNumResourceVariables)" in out

    def test_clock_mode_renders_selected_perf_mode(self):
        out = _render_tflm(transport="rtt", perf_mode_symbol="NSX_PERF_HIGH", perf_mode_mhz=250)
        assert "sys_cfg.perf_mode = NSX_PERF_HIGH;  // 250 MHz" in out

    def test_aot_clock_mode_renders_selected_perf_mode(self):
        out = _render_aot(transport="rtt", perf_mode_symbol="NSX_PERF_HIGH", perf_mode_mhz=250)
        assert "sys_cfg.perf_mode = NSX_PERF_HIGH;  // 250 MHz" in out

    def test_apollo3_burst_enabled_emits_burst_block(self):
        out = _render_tflm(
            transport="rtt", perf_mode_symbol="NSX_PERF_HIGH", perf_mode_mhz=96
        )
        # No burst when the flag is off (default in helper).
        assert "am_hal_burst_mode_enable" not in out
        out = _env.get_template("main.cc.j2").render(
            engine_header="tensorflow/lite/micro/micro_interpreter.h",
            cmsis_device_header="apollo3p.h",
            arena_size=65_536,
            iterations=3,
            warmup=1,
            clean_warmup=1,
            clean_iters=3,
            pmu_passes=_sample_pmu_passes(),
            pmu_pass_names=["Cache"],
            power_sync_enabled=False,
            sync_gpio_pin=91,
            transport="rtt",
            arena_region="tcm",
            weights_region="mram",
            model_size=1024,
            resolver_mode="all",
            resolver_max_ops=2,
            resolver_registrations=["r.AddConv2D();", "r.AddSoftmax();"],
            resource_variable_count=0,
            extreme_mode=False,
            profiling_backends=["dwt"],
            has_armv8m_pmu=False,
            perf_mode_symbol="NSX_PERF_HIGH",
            perf_mode_mhz=96,
            apollo3_burst=True,
            printf_linkage="",
            heartbeat_enabled=True,
            heartbeat_every_n_ops=4,
            heartbeat_every_ms=0,
        )
        assert "am_hal_burst_mode_initialize" in out
        assert "am_hal_burst_mode_enable" in out
        assert "SystemCoreClock = 96U * 1000000U" in out
        assert "HPX_BURST_ENGAGED" in out

    def test_aot_apollo3_burst_enabled_emits_burst_block(self):
        out = _render_aot(transport="rtt", apollo3_burst=False)
        assert "am_hal_burst_mode_enable" not in out
        out = _render_aot(
            transport="rtt",
            apollo3_burst=True,
            cmsis_device_header="apollo3p.h",
            has_armv8m_pmu=False,
            perf_mode_symbol="NSX_PERF_HIGH",
            perf_mode_mhz=96,
        )
        assert "am_hal_burst_mode_initialize" in out
        assert "am_hal_burst_mode_enable" in out
        assert "SystemCoreClock = 96U * 1000000U" in out
        assert "HPX_BURST_ENGAGED" in out

    def test_newlib_syscalls_present_for_m4_absent_for_m55(self):
        # newlib _sbrk/_exit retargets are required to link on Cortex-M4
        # (DWT-only) but must be skipped on Armv8-M (M55).  Both engine
        # templates must agree, or AOT-vs-heliaRT drift reintroduces the
        # Apollo3 link failure (undefined _sbrk/_exit).
        for render in (_render_tflm, _render_aot):
            m4 = render(has_armv8m_pmu=False)
            m55 = render(has_armv8m_pmu=True)
            assert "_sbrk" in m4 and "_exit" in m4
            assert "_sbrk" not in m55 and "_exit" not in m55

    def test_systemcoreclock_set_from_resolved_clock_non_burst(self):
        # Non-AP3-burst targets must pin SystemCoreClock to the resolved clock
        # because NSX leaves the CMSIS global at the 96 MHz reset default.
        tflm = _render_tflm(transport="rtt", perf_mode_symbol="NSX_PERF_HIGH", perf_mode_mhz=250)
        assert "SystemCoreClock = 250U * 1000000U" in tflm
        aot = _render_aot(transport="rtt", perf_mode_symbol="NSX_PERF_HIGH", perf_mode_mhz=192)
        assert "SystemCoreClock = 192U * 1000000U" in aot

    def test_usb_transport_includes_timer_helpers(self):
        out = _render_tflm(transport="usb_cdc")
        assert "usb_timer_pause" in out
        assert "usb_timer_resume" in out
        assert "nsx_usb_send" in out
        assert 'NSX_TRY(nsx_usb_init(&g_usb_cfg), "USB CDC init failed\\n");' in out

    def test_usb_serial_marker_stamps_descriptor(self):
        out = _render_tflm(transport="usb_cdc", usb_serial_marker="HPX-1160001350")
        assert "g_hpx_usb_desc" in out
        assert '.serial  = "HPX-1160001350",' in out
        assert ".device_desc   = &g_hpx_usb_desc," in out

    def test_usb_without_marker_omits_descriptor(self):
        out = _render_tflm(transport="usb_cdc", usb_serial_marker=None)
        assert "g_hpx_usb_desc" not in out
        assert ".device_desc" not in out


    def test_rtt_transport_excludes_usb_timer(self):
        out = _render_tflm(transport="rtt")
        assert "usb_timer_pause" not in out
        assert "nsx_usb_send" not in out

    def test_swo_transport_uses_itm_output(self):
        out = _render_tflm(transport="swo")
        assert "sys_cfg.debug.transport = NSX_DEBUG_ITM;" in out
        assert "nsx_itm_printf_enable();" in out
        assert 'nsx_printf("%s", line_buf);' in out
        assert "ITM->PORT[0].u8" not in out

    def test_shared_blocks_appear_exactly_once(self):
        out = _render_tflm(transport="rtt")
        # After dedup, each shared helper must render once (not twice).
        assert out.count("static inline void dwt_init(void)") == 1
        assert out.count("static inline void hpx_sync_init(void)") == 1

    def test_external_power_sync_uses_nsx_gpio(self):
        out = _env.get_template("main.cc.j2").render(
            engine_header="tensorflow/lite/micro/micro_interpreter.h",
            cmsis_device_header="apollo510.h",
            arena_size=65_536,
            iterations=3,
            warmup=1,
            pmu_passes=_sample_pmu_passes(),
            pmu_pass_names=["Cache"],
            power_sync_enabled=True,
            sync_gpio_pin=42,
            transport="rtt",
            arena_region="tcm",
            weights_region="mram",
            model_size=1024,
            resolver_mode="all",
            resolver_max_ops=2,
            resolver_registrations=["r.AddConv2D();", "r.AddSoftmax();"],
            resource_variable_count=0,
            extreme_mode=False,
            profiling_backends=["dwt", "armv8m-pmu"],
            has_armv8m_pmu=True,
            perf_mode_symbol="NSX_PERF_LOW",
            perf_mode_mhz=96,
            apollo3_burst=False,
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

    def test_psram_weights_skip_model_data_header(self):
        out = _render_tflm(transport="rtt", weights_region="psram")
        assert '#include "model_data.h"' not in out
        assert "nsx_psram.h" in out

    def test_psram_weights_override_skips_model_data_header(self):
        out = _render_tflm(transport="rtt", weights_region="psram")
        assert '#include "model_data.h"' not in out
        assert "nsx_psram.h" in out

    def test_dwt_only_render_avoids_armv8m_pmu_headers(self):
        out = _render_tflm(transport="rtt", has_armv8m_pmu=False)
        assert "nsx_pmu_utils.h" not in out
        assert "g_profiler.Init(0);" in out


class TestMainAotCcRender:
    @pytest.mark.parametrize("transport", ["rtt", "usb_cdc", "swo", "stdio"])
    def test_renders_without_error(self, transport: str):
        out = _render_aot(transport=transport)
        assert "fake_model_invoke" in out or "fake_model" in out
        assert "hpx_sync_init" in out
        assert "dwt_init" in out

    def test_aot_hpx_printf_is_static(self):
        out = _render_aot(transport="rtt")
        assert "static void hpx_printf(" in out

    def test_power_only_routes_recoverable_errors_to_terminal_finalizer(self):
        out = _render_aot(
            transport="rtt",
            power_only=True,
            arena_regions=[
                {
                    "region_id": 0,
                    "placement": "tcm",
                    "alignment": 64,
                    "size": 4096,
                    "blob_filename": None,
                }
            ],
        )
        assert 'hpx_power_terminal_fail("bind_arena", 5U);' in out
        assert 'hpx_power_terminal_fail("model_init", 6U);' in out
        assert out.index("hpx_sync_window_end();") < out.rindex(
            "hpx_power_terminal_report("
        )

    def test_usb_transport_includes_timer_helpers(self):
        out = _render_aot(transport="usb_cdc")
        assert "usb_timer_pause" in out
        assert "nsx_usb_send" in out
        assert 'NSX_TRY(nsx_usb_init(&g_usb_cfg), "USB CDC init failed\\n");' in out

    def test_rtt_transport_includes_drain(self):
        out = _render_aot(transport="rtt")
        assert "hpx_rtt_drain" in out

    def test_aot_rtt_transport_switches_to_blocking_for_csv_and_end(self):
        out = _render_aot(transport="rtt")
        assert "hpx_rtt_set_blocking" in out
        assert "hpx_rtt_set_nonblocking" in out
        assert "hpx_rtt_write_lossless" in out
        assert "SEGGER_RTT_MODE_BLOCK_IF_FIFO_FULL" not in out
        assert out.count("hpx_rtt_set_blocking();") >= 2
        assert out.count("hpx_rtt_set_nonblocking();") >= 1

    def test_aot_non_rtt_transport_omits_blocking_switch(self):
        for transport in ("usb_cdc", "swo", "stdio"):
            out = _render_aot(transport=transport)
            assert "hpx_rtt_set_blocking" not in out
            assert "hpx_rtt_set_nonblocking" not in out

    def test_aot_swo_transport_uses_itm_output(self):
        out = _render_aot(transport="swo")
        assert "sys_cfg.debug.transport = NSX_DEBUG_ITM;" in out
        assert "nsx_itm_printf_enable();" in out
        assert 'nsx_printf("%s", line_buf);' in out
        assert "ITM->PORT[0].u8" not in out

    def test_shared_blocks_appear_exactly_once(self):
        out = _render_aot(transport="rtt")
        assert out.count("static inline void dwt_init(void)") == 1
        assert out.count("static inline void hpx_sync_init(void)") == 1

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

    def test_aot_emits_clean_inference_pass(self):
        """AOT must emit HPX_CLEAN_INFER_* (parity with the TFLM template)."""
        out = _render_aot(transport="rtt")
        assert "HPX_CLEAN_INFER_COUNT" in out
        assert "HPX_CLEAN_INFER_AVG_CYCLES" in out
        assert "phase=clean_window_begin" in out

    def test_aot_gpio_sync_brackets_clean_pass_not_instrumented(self):
        """GPIO sync brackets the clean (power) window, not the per-layer pass."""
        out = _render_aot(transport="rtt")
        # window_begin precedes the clean DWT-timed loop and clean_cycles math.
        hi = out.index("hpx_sync_window_begin();")
        lo = out.index("hpx_sync_window_end();")
        assert hi < out.index("clean_cycles +=") < lo
        # The instrumented profiled loop no longer toggles the sync GPIO.
        assert out.count("hpx_sync_window_begin();") == 1
        assert out.count("hpx_sync_window_end();") == 1

    def test_fixed_window_mode_uses_literal_clean_iters(self):
        """Default (fixed) mode hardcodes the clean iteration count, no runtime math."""
        for render in (_render_tflm, _render_aot):
            out = render(transport="rtt")
            assert "const int clean_iters_n = 3;" in out
            assert "clean_warm_cyc" not in out
            assert "target_cyc" not in out
            # Fixed mode announces the window as pure state with est_ms=0
            # (no runtime warm measurement to estimate from).
            assert "phase=clean_window_begin iters=%d est_ms=0" in out

    def test_auto_window_mode_computes_clean_iters_at_runtime(self):
        """Auto mode measures warm cycles and clamps N to fill the target window."""
        for render in (_render_tflm, _render_aot):
            out = render(transport="rtt", window_mode="auto")
            # Runtime adaptive computation present, no compile-time literal.
            assert "const int clean_iters_n = 3;" not in out
            assert "uint32_t clean_warm_cyc = 0U;" in out
            assert "((uint64_t)SystemCoreClock / 1000ULL) * (uint64_t)250U" in out
            assert "if (n < 10ULL) n = 10ULL;" in out
            assert "if (n > 200ULL) n = 200ULL;" in out
            # Robustness: warm several times and keep the MAX reading so a
            # transient DWT->CYCCNT freeze (J-Link DEMCR/DWT reset on attach)
            # cannot under-size the window; fall back to window_min, not max.
            assert "if (wc > clean_warm_cyc) clean_warm_cyc = wc;" in out
            assert "int clean_iters_n = 10;" in out
            assert "int clean_iters_n = 200;" not in out
            # The gated loop still iterates over the computed count.
            assert "for (int iter = 0; iter < clean_iters_n; iter++)" in out
            # Auto mode announces the window with a runtime duration estimate
            # (iters * warm cycles / clock) so the host can widen its deadline.
            assert "phase=clean_window_begin iters=%d est_ms=%llu" in out
            assert "clean_est_ms = ((uint64_t)clean_iters_n * (uint64_t)clean_warm_cyc)" in out
            assert out.index("hpx_sync_ready();") < out.index("hpx_sync_wait_go();")
            assert out.index("hpx_sync_ready();") > out.index("clean_warm_cyc")

    def test_power_only_fixed_count_override(self):
        for render in (_render_tflm, _render_aot):
            out = render(
                transport="rtt",
                power_only=True,
                window_mode="fixed",
                clean_iters=2247,
            )
            assert "const int clean_iters_n = 2247;" in out
            assert "uint32_t clean_warm_cyc = 0U;" not in out

    def test_busy_loop_probe_replaces_clean_window_body(self):
        tflm_out = _render_tflm(
            transport="rtt", window_mode="auto", clean_window_probe="busy_loop"
        )
        assert 'HPX_CLEAN_WINDOW_PROBE=busy_loop' in tflm_out
        # The busy-loop bound is calibrated via DWT BEFORE the PMU/debug
        # domain is disabled, then the gated window itself runs a plain
        # bounded counter loop with no live DWT reads — DWT lives in the
        # same debug power domain that gets disabled, so a live
        # "while (DWT->CYCCNT - t0 < target)" loop as the exit condition
        # would hang forever once that domain is off (regression found
        # 2026-07-03: real firmware hang on hardware).
        assert 'for (volatile uint32_t bi = 0; bi < busy_loop_iters; bi++)' in tflm_out
        assert 'while ((uint32_t)(DWT->CYCCNT - t0) < (uint32_t)clean_probe_target_cyc)' not in tflm_out
        assert 'clean_count = 1;' in tflm_out

        aot_out = _render_aot(
            transport="rtt", window_mode="auto", clean_window_probe="busy_loop"
        )
        assert 'HPX_CLEAN_WINDOW_PROBE=busy_loop' in aot_out
        assert 'for (volatile uint32_t bi = 0; bi < busy_loop_iters; bi++)' in aot_out
        assert 'while ((uint32_t)(DWT->CYCCNT - t0) < (uint32_t)clean_probe_target_cyc)' not in aot_out
        assert 'clean_count = 1;' in aot_out
        assert 'am_hal_debug_disable();' in tflm_out
        assert 'am_hal_debug_disable();' in aot_out

    def test_armv8m_infer_probe_keeps_debug_domain_up_for_clean_timing(self):
        tflm_out = _render_tflm(transport="rtt", has_armv8m_pmu=True)
        aot_out = _render_aot(transport="rtt", has_armv8m_pmu=True)

        assert 'uint32_t t0 = DWT->CYCCNT;' in tflm_out
        assert 'clean_cycles += (uint32_t)(DWT->CYCCNT - t0);' in tflm_out
        assert 'am_hal_debug_disable();' not in tflm_out

        assert 'uint32_t t0 = DWT->CYCCNT;' in aot_out
        assert 'clean_cycles += (uint32_t)(DWT->CYCCNT - t0);' in aot_out
        assert 'am_hal_debug_disable();' not in aot_out

    def test_dwt_only_aot_render_avoids_armv8m_pmu_api(self):
        out = _render_aot(transport="rtt", has_armv8m_pmu=False)
        assert "nsx_pmu_utils.h" not in out
        assert "nsx_pmu_map.h" not in out
        assert "ARM_PMU_CPU_CYCLES" in out
        assert "nsx_pmu_reset_counters" not in out
        assert "g_op_start_cyccnt" in out

    def test_hpx_pmu_profiler_kmax_layers_from_pmu_max_ops(self):
        """kMaxLayers is templated from the target SoC's pmu_max_ops, not a
        hardcoded constant -- this static array's footprint (~24 bytes/entry)
        must fit inside the real, sometimes much smaller, TCM budget of the
        target board (2026-07 finding: apollo330P's real 240 KB TCM vs
        apollo510's ~496 KB; a hardcoded 4096 alone reserved ~96 KB on
        apollo330P, over a third of its actual budget).
        """
        template = _env.get_template("hpx_pmu_profiler.h.j2")

        small = template.render(
            cmsis_device_header="apollo330P.h",
            profiling_backends=["dwt", "armv8m-pmu"],
            has_armv8m_pmu=True,
            pmu_max_ops=512,
        )
        assert "kMaxLayers = 512;" in small

        large = template.render(
            cmsis_device_header="apollo510.h",
            profiling_backends=["dwt", "armv8m-pmu"],
            has_armv8m_pmu=True,
            pmu_max_ops=4096,
        )
        assert "kMaxLayers = 4096;" in large
