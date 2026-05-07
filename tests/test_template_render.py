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
) -> str:
    return _env.get_template("main.cc.j2").render(
        engine_header="tensorflow/lite/micro/micro_interpreter.h",
        arena_size=65_536,
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
        printf_linkage="",
        heartbeat_enabled=True,
        heartbeat_every_n_ops=4,
        heartbeat_every_ms=0,
    )


def _render_aot(transport: str = "rtt") -> str:
    return _env.get_template("main_aot.cc.j2").render(
        aot_prefix="fake",
        aot_op_manifest=[{"index": 0, "op_name": "CONV_2D"}],
        iterations=3,
        warmup=1,
        pmu_passes=[{"name": "Cache", "counters": ["ARM_PMU_CPU_CYCLES"]}],
        pmu_pass_names=["Cache"],
        power_sync_enabled=False,
        sync_gpio_pin=91,
        transport=transport,
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

    def test_psram_model_location_skips_model_data_header(self):
        out = _render_tflm(transport="rtt", model_location="psram", weights_region="psram")
        assert "#include \"model_data.h\"" not in out
        assert "ns_peripherals_psram.h" in out

    def test_psram_weights_override_skips_model_data_header(self):
        out = _render_tflm(transport="rtt", model_location="auto", weights_region="psram")
        assert "#include \"model_data.h\"" not in out
        assert "ns_peripherals_psram.h" in out


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

    def test_aot_op_manifest_embedded(self):
        out = _render_aot(transport="rtt")
        assert "CONV_2D" in out
