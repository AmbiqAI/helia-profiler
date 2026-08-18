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
    # Only read for power_only renders (SocCapabilities.power_window_timer);
    # these smoke tests leave clean_window_timer at its "dwt" template default,
    # so the matching power-binary default keeps them on the DWT paths unless a
    # case opts into STIMER explicitly.
    power_window_timer: str = "dwt",
    psram_clock_hz: int = 48_000_000,
    **extra_vars: object,
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
        power_window_timer=power_window_timer,
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
        psram_clock_hz=psram_clock_hz,
        **extra_vars,
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
    # See _render_tflm.
    power_window_timer: str = "dwt",
    psram_clock_hz: int = 48_000_000,
    **extra_vars: object,
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
        power_window_timer=power_window_timer,
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
        psram_clock_hz=psram_clock_hz,
        **extra_vars,
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
        out = _render_tflm(
            transport="rtt",
            weights_region="psram",
            psram_clock_hz=125_000_000,
        )
        assert '#include "model_data.h"' not in out
        assert "nsx_psram.h" in out
        assert "psram_cfg.clock_hz = 125000000U;" in out
        assert "nsx_psram_get_info(&psram_info)" in out
        assert "HPX_PSRAM_RXDQS_DELAY" in out

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
                    "blob_filename": "weights.bin",
                }
            ],
            psram_clock_hz=125_000_000,
        )
        assert "nsx_psram.h" in out
        assert "nsx_psram_default_config(&psram_cfg);" in out
        assert "psram_cfg.clock_hz = 125000000U;" in out
        assert "nsx_psram_write(" in out
        assert "hpx_arena_psram_offset_0" in out

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
        # The busy-loop bound is calibrated against STIMER, then the gated
        # window itself runs a plain bounded counter loop with no live clock
        # reads at all — DWT lives in the debug power domain this probe
        # disables, so a live "while (DWT->CYCCNT - t0 < target)" loop as the
        # exit condition would hang forever once that domain is off
        # (regression found 2026-07-03: real firmware hang on hardware).
        assert 'for (volatile uint32_t bi = 0; bi < busy_loop_iters; bi++)' in tflm_out
        assert 'clean_count = 1;' in tflm_out

        aot_out = _render_aot(
            transport="rtt", window_mode="auto", clean_window_probe="busy_loop"
        )
        assert 'HPX_CLEAN_WINDOW_PROBE=busy_loop' in aot_out
        assert 'for (volatile uint32_t bi = 0; bi < busy_loop_iters; bi++)' in aot_out
        assert 'clean_count = 1;' in aot_out
        assert 'am_hal_debug_disable();' in tflm_out
        assert 'am_hal_debug_disable();' in aot_out

        # Pin the "no live DWT read in the window" rule against the RENDER, not
        # against one hypothetical spelling of the rejected design.  The old
        # assertion here looked for a literal
        # "while ((uint32_t)(DWT->CYCCNT - t0) < ...)" that the template never
        # contained under any branch, so it could not fail.  Anchor on the
        # window CALL sites (the static inline definitions appear earlier) and
        # forbid the register outright.
        for out in (tflm_out, aot_out):
            begin = out.index("hpx_sync_window_begin();")
            window = out[begin : out.index("hpx_sync_window_end();", begin)]
            assert "DWT->CYCCNT" not in window

    @pytest.mark.parametrize(
        "soc_shape",
        [
            # Apollo3/3P power binary: no broad shutdown, but nothing asserts
            # CDBGPWRUPREQ once it free-runs, so DWT never advances.
            {"power_window_timer": "stimer", "broad_peripheral_shutdown": False},
            # Apollo4 family power binary: _peripheral_power_down.j2 disables
            # AM_HAL_PWRCTRL_PERIPH_DEBUG ~200 rendered lines above the probe.
            {"power_window_timer": "stimer", "broad_peripheral_shutdown": True},
        ],
        ids=["apollo3p_shaped", "apollo4p_shaped"],
    )
    @pytest.mark.parametrize("window_mode", ["fixed", "auto"])
    def test_busy_loop_calibration_never_reads_dwt_on_a_power_binary(
        self, soc_shape: dict, window_mode: str
    ):
        """Regression, issue #112.

        The Cortex-M4F power binaries cannot read DWT->CYCCNT: AP4 powers the
        debug domain down itself, and neither AP3 nor AP4 has a debugger
        holding that domain up once the binary free-runs.  The busy-loop probe
        used to size its iteration count from a DWT delta anyway; the delta
        read 0, ``if (busy_calib_cyc > 0U)`` was skipped, the count kept its
        hardcoded 100000 seed, and the window ran for an arbitrary length.

        These renders are AP3/AP4-shaped (has_armv8m_pmu=False,
        power_window_timer="stimer") -- the combination the snapshot suite did
        not cover, because it pins clean_window_probe="infer", and that the
        busy-loop case here did not cover either, because it used AP5-shaped
        defaults.
        """
        for render in (_render_tflm, _render_aot):
            out = render(
                transport="rtt",
                power_only=True,
                window_mode=window_mode,
                clean_window_probe="busy_loop",
                has_armv8m_pmu=False,
                **soc_shape,
            )
            # The calibration pass exists and is timed by STIMER, not DWT.
            assert "busy_calib_t0 = hpx_stimer_ticks();" in out
            calib = out[out.index("busy_calib_t0") : out.index("busy_loop_iters =")]
            assert "DWT->CYCCNT" not in calib, (
                "busy-loop calibration reads DWT on a binary that cannot read it"
            )
            # STIMER must actually be defined in this render, not just called.
            assert "static inline void hpx_stimer_init(void)" in out
            shutdown = "am_hal_pwrctrl_periph_disable(AM_HAL_PWRCTRL_PERIPH_DEBUG);"
            if soc_shape["broad_peripheral_shutdown"]:
                # The AP4 shape: pin the ordering the original comment got
                # wrong -- the domain is gone long BEFORE the calibration, not
                # after it.
                assert out.index(shutdown) < out.index("busy_calib_t0")
            else:
                assert shutdown not in out

    @pytest.mark.parametrize("window_mode", ["fixed", "auto"])
    def test_busy_loop_window_duration_is_measured_not_the_nominal_target(
        self, window_mode: str
    ):
        """Regression, issue #112 (second half).

        ``clean_cycles = clean_probe_target_cyc`` made the terminal report echo
        window_target_ms as the measured duration, so a mis-sized window was
        indistinguishable from a correct one -- and in internal mode that
        duration is the denominator for average power and current.  The STIMER
        bracket around the window is now the only source of elapsed_us.
        """
        for render in (_render_tflm, _render_aot):
            out = render(
                transport="rtt",
                power_only=True,
                power_window_timer="stimer",
                window_mode=window_mode,
                clean_window_probe="busy_loop",
                has_armv8m_pmu=False,
                broad_peripheral_shutdown=True,
            )
            assert "clean_cycles = clean_probe_target" not in out
            assert "uint32_t clean_stimer_t0 = hpx_stimer_ticks();" in out
            assert "uint64_t clean_stimer_total_us =" in out
            # elapsed_us in the terminal report comes from the measurement.
            assert "clean_stimer_total_us," in out

    def test_busy_loop_calibration_rejects_an_implausible_measurement(self):
        """The scaling branch is gated on a plausibility BAND, not just != 0.

        ``busy_calib_ticks`` is the denominator that sizes the window, so a
        corrupt reading mis-sizes it by that same multiplicative factor.  The
        known source is ``hpx_stimer_init()`` itself: AM_HAL_STIMER_CFG_CLEAR
        drops the XT request before re-requesting it, and the XT is a crystal
        that restarts.  Measured on an Apollo4 Blue Plus KBR, reading STIMER
        straight after that sequence gave apparent rates varying 45% across
        identical builds (584/591/858 kHz against a true 95.771 MHz); a 750 ms
        settle made it repeatable to 20 ppm.  A transient that is negligible
        across a multi-second measured window can swallow this ~6-8 ms
        calibration pass whole.

        The settle belongs in ``hpx_stimer_init()`` and needs a bench pass to
        size (issue #110).  Until then the band keeps a bad reading from
        producing an absurd iteration count -- it falls back to the seed, and
        because the window is now measured, that fallback is visible in
        HPX_CLEAN_INFER_AVG_US rather than hidden behind the nominal target.
        """
        for render in (_render_tflm, _render_aot):
            out = render(
                transport="rtt",
                power_only=True,
                power_window_timer="stimer",
                clean_window_probe="busy_loop",
                has_armv8m_pmu=False,
                broad_peripheral_shutdown=True,
            )
            assert "const uint32_t busy_calib_min_ticks = 16U;" in out
            assert "const uint32_t busy_calib_max_ticks = 8192U;" in out
            # Both ends of the band gate the scaling branch -- a bare
            # "> 0U" guard (what this shipped with before) would let the
            # observed corruption straight through.
            assert (
                "if (busy_calib_ticks >= busy_calib_min_ticks &&\n"
                "            busy_calib_ticks <= busy_calib_max_ticks) {"
            ) in out
            assert "if (busy_calib_ticks > 0U) {" not in out
            # The seed survives as the fallback value.
            assert "uint32_t busy_loop_iters = 100000U;" in out

    def test_armv8m_infer_probe_keeps_debug_domain_up_for_clean_timing(self):
        tflm_out = _render_tflm(transport="rtt", has_armv8m_pmu=True)
        aot_out = _render_aot(transport="rtt", has_armv8m_pmu=True)

        # The per-iteration delta is bound to a name before it is accumulated
        # so the loop can also test it for the zero that marks a stalled cycle
        # counter (#121); the DWT-timed accumulation itself is unchanged.
        for out in (tflm_out, aot_out):
            assert 'uint32_t t0 = DWT->CYCCNT;' in out
            assert 'const uint32_t clean_iter_cyc = (uint32_t)(DWT->CYCCNT - t0);' in out
            assert 'clean_cycles += clean_iter_cyc;' in out
            assert 'am_hal_debug_disable();' not in out

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


# ---------------------------------------------------------------------------
# On-target INA228 power monitor (power.driver: ina228)
# ---------------------------------------------------------------------------

_INA228_VARS: dict[str, object] = {
    "power_monitor": "ina228",
    # driver: ina228 — the monitor is the measurement of record, so setup /
    # arm / read failures are terminal. The bystander variant (driver:
    # joulescope + ina228 block) is exercised separately.
    "ina228_required": True,
    "ina228_i2c_iom": 1,
    "ina228_i2c_address": 0x40,
    "ina228_i2c_speed_hz": 400_000,
    "ina228_shunt_micro_ohms": 2_000_000,
    "ina228_max_current_ma": 500,
    "ina228_conversion_time_us": 540,
    "ina228_averaging_count": 16,
    "ina228_adc_range": 0,
    "ina228_shunt_cal": 6250,
    "ina228_current_lsb_divisor": "13107200000.0",
    "ina228_calibration_id": "ina228:r2000000uohm:i500ma:adc0",
}

_INA228_MEASUREMENT_KEYS = (
    "HPX_POWER_MEASUREMENT_SOURCE=ina228",
    "HPX_POWER_MEASUREMENT_SCOPE=fixed_n_inference",
    "HPX_POWER_ENERGY_NJ",
    "HPX_POWER_MEASUREMENT_DURATION_US",
    "HPX_POWER_MEASUREMENT_COUNT",
    "HPX_POWER_MEASUREMENT_OVERFLOW",
    "HPX_POWER_CHARGE_NC",
    "HPX_POWER_BUS_VOLTAGE_UV",
    "HPX_POWER_CALIBRATION_ID=ina228:r2000000uohm:i500ma:adc0",
)


class TestIna228PowerRender:
    @pytest.mark.parametrize("render", [_render_tflm, _render_aot])
    def test_power_only_render_contains_monitor_block(self, render):
        out = render(power_only=True, **_INA228_VARS)
        for fragment in (
            "hpx_ina228_setup",
            "hpx_ina228_window_begin",
            "hpx_ina228_window_end",
            "nsx_i2c_interface_init(&g_hpx_ina228_i2c, 400000U)",
            "ina228_set_adc_range(&g_hpx_ina228, 0U)",
            "INA228_TIME_540_us",
            "INA228_COUNT_16",
            "INA228_MODE_CONT_BUS_SHUNT",
            'hpx_power_terminal_fail("ina228_init"',
            'hpx_power_terminal_fail("ina228_arm"',
            'hpx_power_terminal_fail("ina228_read"',
            # SHUNT_CAL is read back and required non-zero: an uncalibrated
            # part silently reports zero current/energy (hardware finding).
            "g_hpx_ina228_shunt_cal == 0U",
            # Accumulators read raw (40-bit) rather than through the float API.
            "ina228_read_energy_raw",
            "ina228_read_charge_raw",
            "/ 13107200000.0",
            *_INA228_MEASUREMENT_KEYS,
        ):
            assert fragment in out, f"missing: {fragment}"

    @pytest.mark.parametrize("render", [_render_tflm, _render_aot])
    def test_accumulator_calls_bracket_the_sync_window(self, render):
        """Reset before the gate opens, read after it closes — the I2C
        traffic must sit strictly outside the measured region."""
        out = render(power_only=True, **_INA228_VARS)
        i_setup = out.index("uint32_t ina228_rc = hpx_ina228_setup()")
        i_begin = out.index("hpx_ina228_window_begin()", i_setup)
        i_sync_begin = out.index("hpx_sync_window_begin();", i_begin)
        i_sync_end = out.index("hpx_sync_window_end();", i_sync_begin)
        out.index("hpx_ina228_window_end()", i_sync_end)  # raises if absent

    @pytest.mark.parametrize("render", [_render_tflm, _render_aot])
    def test_monitor_statics_precede_terminal_report_definition(self, render):
        out = render(power_only=True, **_INA228_VARS)
        assert out.index("static bool     g_hpx_ina228_ok") < out.index(
            "static void hpx_power_terminal_report("
        )

    @pytest.mark.parametrize("render", [_render_tflm, _render_aot])
    def test_bystander_monitor_failures_are_not_terminal(self, render):
        """driver: joulescope + an ina228 block builds the same monitor
        firmware, but a missing/mis-wired chip must not kill the run — the
        external instrument owns the measurement. Failures are recorded and
        reported by the terminal report instead (no printf at the failure
        sites: UART/ITM are disabled for the window, and an ITM write with
        PD_DBG off hangs the part)."""
        out = render(power_only=True, **{**_INA228_VARS, "ina228_required": False})
        for phase in ("ina228_init", "ina228_arm", "ina228_read"):
            assert f'hpx_power_terminal_fail("{phase}"' not in out, phase
        assert "g_hpx_ina228_bystander_fail_phase" in out
        assert "HPX_POWER_INA228_BYSTANDER_FAILED=" in out
        # The window hooks must be conditional on a live monitor.
        assert "if (g_hpx_ina228_active)" in out
        # Measurement keys stay gated on a successful read, so a dropped
        # bystander yields no payload rather than a zeroed one.
        assert "g_hpx_ina228_ok" in out

    @pytest.mark.parametrize("render", [_render_tflm, _render_aot])
    def test_profile_binary_never_embeds_monitor_code(self, render):
        """power_only=false renders (the transport/profile binary) must not
        pick up monitor code even when the run selects the ina228 driver."""
        out = render(power_only=False, **_INA228_VARS)
        assert "ina228" not in out

    @pytest.mark.parametrize("render", [_render_tflm, _render_aot])
    def test_broad_peripheral_shutdown_spares_the_monitor_iom(self, render):
        """On families with broad peripheral shutdown (AP4) the IOM disable
        runs *after* hpx_ina228_setup(), so powering down the monitor's IOM
        would break the accumulator reset/read bracketing the window."""
        out = render(power_only=True, broad_peripheral_shutdown=True, **_INA228_VARS)
        disabled = [i for i in range(8) if f"PERIPH_IOM{i});" in out]
        assert disabled == [0, 2, 3, 4, 5, 6, 7]

    @pytest.mark.parametrize("render", [_render_tflm, _render_aot])
    def test_broad_peripheral_shutdown_unchanged_without_a_monitor(self, render):
        out = render(power_only=True, broad_peripheral_shutdown=True)
        assert [i for i in range(8) if f"PERIPH_IOM{i});" in out] == list(range(8))

    @pytest.mark.parametrize("render", [_render_tflm, _render_aot])
    @pytest.mark.parametrize("power_only", [False, True])
    def test_renders_without_monitor_stay_clean(self, render, power_only: bool):
        """No power_monitor var at all (StrictUndefined would raise on a bad
        gate) and no ina228 content — the WP2 byte-identical guarantee."""
        out = render(power_only=power_only)
        assert "ina228" not in out
        assert "HPX_POWER_MEASUREMENT_SOURCE" not in out
