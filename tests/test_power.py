"""Tests for power driver abstraction."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from helia_profiler.errors import PowerError
from helia_profiler.power import get_driver, list_drivers
from helia_profiler.power.base import (
    GatedPowerWindow,
    PowerMode,
    PowerResult,
    PowerSample,
    PowerSummary,
)

#: time64 tick rate (2**30 ticks per second), mirrors ``pyjoulescope_driver.time64.SECOND``.
_SECOND = 1 << 30


class TestPowerTypes:
    def test_power_sample_power_w(self):
        s = PowerSample(timestamp_s=0.0, current_a=0.010, voltage_v=1.8)
        assert abs(s.power_w - 0.018) < 1e-9

    def test_power_summary_frozen(self):
        summary = PowerSummary(
            avg_current_a=0.01,
            avg_power_w=0.018,
            peak_current_a=0.05,
            energy_j=0.54,
            duration_s=30.0,
            sample_count=1000,
        )
        with pytest.raises(AttributeError):
            summary.avg_current_a = 0.02  # type: ignore[misc]

    def test_power_result_no_per_layer_by_default(self):
        summary = PowerSummary(
            avg_current_a=0.01,
            avg_power_w=0.018,
            peak_current_a=0.05,
            energy_j=0.54,
            duration_s=30.0,
            sample_count=1000,
        )
        result = PowerResult(summary=summary)
        assert result.per_layer is None
        assert result.samples == []
        assert result.gated_windows == []
        assert result.metadata == {}

    def test_gated_window_is_typed(self):
        window = GatedPowerWindow(
            start_s=0.1,
            end_s=0.3,
            duration_s=0.2,
            charge_c=0.001,
            energy_j=0.002,
            avg_current_a=0.005,
            avg_power_w=0.01,
            peak_current_a=0.02,
            sample_count=123,
        )
        assert window.duration_s == 0.2
        assert window.sample_count == 123


class TestPowerDiagnostics:
    def test_sync_handshake_metadata_serializes_observed_ready(self):
        from helia_profiler.power.diagnostics import SyncHandshakeMetadata

        metadata = SyncHandshakeMetadata(
            lockstep=True,
            ready_wait_s=0.012,
            ready_observed=True,
        ).to_metadata()

        assert metadata == {
            "lockstep": True,
            "ready_wait_s": 0.012,
            "ready_observed": True,
        }

    def test_gate_failure_classifies_missing_rise(self):
        from helia_profiler.power.diagnostics import GateFailureKind, classify_gate_failure

        failure = classify_gate_failure(saw_gate_rise=False, duration_s=7.0)

        assert failure.kind is GateFailureKind.NO_GATE_RISE
        assert "rising edge" in failure.message

    def test_gate_failure_classifies_missing_fall(self):
        from helia_profiler.power.diagnostics import GateFailureKind, classify_gate_failure

        failure = classify_gate_failure(saw_gate_rise=True, duration_s=7.0)

        assert failure.kind is GateFailureKind.NO_GATE_FALL
        assert "did not fall" in failure.message


class TestGatedStatsProcessing:
    """Host-side integration of on-device stat packets into gated windows."""

    @staticmethod
    def _packet(u0: int, u1: int, cur_int: float, pwr_int: float, cur_max: float):
        return {
            "time": {"utc": {"value": [u0, u1]}},
            "signals": {
                "current": {
                    "avg": {"value": cur_int / ((u1 - u0) / _SECOND)},
                    "max": {"value": cur_max},
                    "integral": {"value": cur_int},
                },
                "power": {
                    "avg": {"value": pwr_int / ((u1 - u0) / _SECOND)},
                    "integral": {"value": pwr_int},
                },
            },
        }

    @staticmethod
    def _packet_with_host_time(
        u0: int,
        u1: int,
        cur_int: float,
        pwr_int: float,
        cur_max: float,
        host_time64: int,
    ):
        packet = TestGatedStatsProcessing._packet(u0, u1, cur_int, pwr_int, cur_max)
        packet["_host_time64"] = host_time64
        return packet

    def test_gated_window_sums_ondevice_integrals(self):
        from helia_profiler.power.joulescope_driver import _process_gated_stats

        ms = _SECOND // 1000
        packets = []
        for i in range(20):
            u0 = i * ms
            u1 = (i + 1) * ms
            # Inject a transient spike in one in-window packet's max sample.
            cur_max = 0.5 if i == 8 else 0.12
            packets.append(self._packet(u0, u1, 0.0001, 0.00018, cur_max))

        rise = 5 * ms  # window covers packets with midpoint in [5ms, 15ms]
        fall = 15 * ms
        poll_samples = [(0, 0), (rise, 1), (fall, 0)]

        windows, summary = _process_gated_stats(
            packets=packets, poll_samples=poll_samples, io_voltage=1.8
        )

        assert len(windows) == 1
        w = windows[0]
        assert w.sample_count == 10
        assert w.charge_c == pytest.approx(0.001, rel=1e-6)
        assert w.energy_j == pytest.approx(0.0018, rel=1e-6)
        assert w.duration_s == pytest.approx(0.01, rel=1e-6)
        assert w.avg_current_a == pytest.approx(0.1, rel=1e-6)
        assert w.avg_power_w == pytest.approx(0.18, rel=1e-6)
        # Raw peak captures the transient spike; the p99 robust peak rejects it.
        assert w.peak_current_a == pytest.approx(0.5, rel=1e-6)
        assert w.peak_current_p99_a < 0.5
        assert w.median_current_a == pytest.approx(0.1, rel=1e-6)
        assert summary.energy_j == pytest.approx(0.0018, rel=1e-6)

    def test_no_windows_returns_empty(self):
        from helia_profiler.power.joulescope_driver import _process_gated_stats

        ms = _SECOND // 1000
        packets = [self._packet(0, ms, 0.0001, 0.00018, 0.12)]
        windows, summary = _process_gated_stats(
            packets=packets, poll_samples=[], io_voltage=1.8
        )
        assert windows == []
        assert summary.sample_count == 0

    def test_gated_diagnostics_separates_selected_packets(self):
        from helia_profiler.power.joulescope_driver import _gated_stats_diagnostics

        ms = _SECOND // 1000
        packets = []
        for i in range(20):
            u0 = i * ms
            u1 = (i + 1) * ms
            cur_int = 0.0001 if 5 <= i < 15 else 0.00002
            pwr_int = cur_int * 1.8
            packets.append(self._packet(u0, u1, cur_int, pwr_int, 0.12))

        rise = 5 * ms
        fall = 15 * ms
        poll_samples = [(0, 0), (rise, 1), (fall, 0)]

        diagnostics = _gated_stats_diagnostics(packets=packets, poll_samples=poll_samples)

        assert diagnostics["window_count"] == 1
        assert diagnostics["selected_packets"] == 10
        assert diagnostics["rejected_packets"] == 10
        assert diagnostics["selected_median_current_a"] == pytest.approx(0.1, rel=1e-6)
        assert diagnostics["rejected_median_current_a"] == pytest.approx(0.02, rel=1e-6)

    def test_gated_stats_uses_host_packet_time_axis_when_available(self):
        from helia_profiler.power.joulescope_driver import (
            _gated_stats_diagnostics,
            _process_gated_stats,
        )

        ms = _SECOND // 1000
        host_base = 10_000 * ms
        packets = []
        for i in range(20):
            u0 = i * ms
            u1 = (i + 1) * ms
            host_tick = host_base + ((i * ms) + (ms // 2))
            cur_int = 0.0001 if 5 <= i < 15 else 0.00002
            pwr_int = cur_int * 1.8
            packets.append(
                self._packet_with_host_time(u0, u1, cur_int, pwr_int, 0.12, host_tick)
            )

        rise = host_base + 5 * ms
        fall = host_base + 15 * ms
        poll_samples = [(host_base, 0), (rise, 1), (fall, 0)]

        windows, summary = _process_gated_stats(
            packets=packets, poll_samples=poll_samples, io_voltage=1.8
        )
        diagnostics = _gated_stats_diagnostics(packets=packets, poll_samples=poll_samples)

        assert len(windows) == 1
        assert windows[0].sample_count == 10
        assert summary.avg_current_a == pytest.approx(0.1, rel=1e-6)
        assert diagnostics["mask_time_axis"] == "host_packet_arrival_time64"
        assert diagnostics["selected_packets"] == 10

    def test_whole_summary_sums_all_packets(self):
        from helia_profiler.power.joulescope_driver import _whole_summary_from_stats

        ms = _SECOND // 1000
        packets = [
            self._packet(i * ms, (i + 1) * ms, 0.0001, 0.00018, 0.12) for i in range(10)
        ]
        summary = _whole_summary_from_stats(packets)
        assert summary.sample_count == 10
        assert summary.energy_j == pytest.approx(0.0018, rel=1e-6)
        assert summary.duration_s == pytest.approx(0.01, rel=1e-6)
        assert summary.avg_power_w == pytest.approx(0.18, rel=1e-6)



class TestPowerMode:
    def test_external(self):
        assert PowerMode.EXTERNAL == "external"
        assert PowerMode("external") is PowerMode.EXTERNAL

    def test_internal(self):
        assert PowerMode.INTERNAL == "internal"
        assert PowerMode("internal") is PowerMode.INTERNAL


class TestDriverRegistry:
    def test_list_drivers(self):
        drivers = list_drivers()
        assert "joulescope" in drivers
        assert "ondevice" in drivers

    def test_get_joulescope(self):
        driver = get_driver("joulescope")
        assert driver.name == "Joulescope"
        assert driver.mode is PowerMode.EXTERNAL

    def test_get_ondevice(self):
        driver = get_driver("ondevice")
        assert driver.name == "On-Device"
        assert driver.mode is PowerMode.INTERNAL

    def test_unknown_driver_raises(self):
        with pytest.raises(PowerError, match="Unknown power driver"):
            get_driver("nonexistent")


class TestJoulescopeDriver:
    def test_mode_is_external(self):
        driver = get_driver("joulescope")
        assert driver.mode is PowerMode.EXTERNAL

    def test_check_available_raises_without_package(self):
        """Joulescope check_available should raise PowerError if not installed."""
        driver = get_driver("joulescope")
        try:
            import pyjoulescope_driver  # noqa: F401

            # If pyjoulescope_driver is actually installed, skip this test
            pytest.skip("pyjoulescope_driver is installed — cannot test import failure")
        except ImportError:
            with pytest.raises(PowerError, match="not installed"):
                driver.check_available()


class TestOnDeviceDriver:
    def test_mode_is_internal(self):
        driver = get_driver("ondevice")
        assert driver.mode is PowerMode.INTERNAL

    def test_check_available_passes(self):
        driver = get_driver("ondevice")
        driver.check_available()  # Should not raise

    def test_capture_raises_not_implemented(self):
        driver = get_driver("ondevice")
        with pytest.raises(PowerError, match="not yet implemented"):
            driver.capture(duration_s=10.0, io_voltage=1.8)

    def test_power_cycle_raises_not_supported(self):
        driver = get_driver("ondevice")
        with pytest.raises(PowerError, match="cannot power-cycle"):
            driver.power_cycle()


class TestPowerConfig:
    def test_default_config(self, tmp_path: Path):
        from helia_profiler.config import load_config

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {"model": {"path": str(model)}, "engine": {"type": "helia-rt"}},
        )
        assert config.power.enabled is False
        assert config.power.driver == "joulescope"
        assert config.power.mode == "external"
        assert config.power.sync_gpio_pin == 29

    def test_default_sync_gpio_pin_uses_board_metadata(self, tmp_path: Path):
        from helia_profiler.config import load_config

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")

        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "target": {
                    "board": "apollo510_lab",
                    "custom_boards": {
                        "apollo510_lab": {
                            "based_on": "apollo510_evb",
                            "default_sync_gpio_pin": 27,
                        }
                    },
                },
            },
        )

        assert config.power.sync_gpio_pin == 27

    def test_custom_power_config(self, tmp_path: Path):
        from helia_profiler.config import load_config

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "power": {
                    "enabled": True,
                    "driver": "ondevice",
                    "mode": "internal",
                    "sync_gpio_pin": 42,
                    "duration_s": 60,
                },
            },
        )
        assert config.power.enabled is True
        assert config.power.driver == "ondevice"
        assert config.power.mode == "internal"
        assert config.power.sync_gpio_pin == 42
        assert config.power.duration_s == 60

    def test_power_reset_strategy_config(self, tmp_path: Path):
        from helia_profiler.config import load_config
        from helia_profiler.target_lifecycle import ResetStrategy

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "power": {"reset_strategy": "swpoi_reset"},
            },
        )

        assert config.power.reset_strategy is ResetStrategy.SWPOI_RESET


class TestCapturePowerStage:
    def test_skipped_when_disabled(self, tmp_path: Path):
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.stages.capture_power import CapturePowerStage

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {"model": {"path": str(model)}, "engine": {"type": "helia-rt"}},
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        stage = CapturePowerStage()
        assert stage.should_skip(ctx) is True

    def test_not_skipped_when_enabled(self, tmp_path: Path):
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.stages.capture_power import CapturePowerStage

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "power": {"enabled": True},
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        stage = CapturePowerStage()
        assert stage.should_skip(ctx) is False

    def test_resets_target_before_capture(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # Power capture must re-launch the firmware so the gated window fires
        # under the live poller; relay-cycled boards drawing USB bench power are
        # not rebooted, so a J-Link reset is the deterministic restart.
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.platform import get_soc_for_board
        from helia_profiler.stages.capture_power import CapturePowerStage

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "target": {"transport": "uart", "jlink_serial": "1160002204"},
                "power": {"enabled": True, "driver": "joulescope-js110"},
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        ctx.soc = get_soc_for_board("apollo510_evb")
        reset_calls: dict[str, object] = {}

        class FakeDriver:
            def power_cycle(self, **kwargs):
                raise AssertionError("auto reset must not power-cycle")

        monkeypatch.setattr(
            "helia_profiler.jlink.reset_target",
            lambda **k: reset_calls.update(k),
        )
        monkeypatch.setattr(
            "helia_profiler.jlink.reset_target_poi",
            lambda **k: reset_calls.setdefault("swpoi", k),
        )

        def fake_capture_power(ctx, **kwargs):
            plan = kwargs["prepare_target"](FakeDriver(), "joulescope-js110")
            return PowerResult(
                summary=PowerSummary(0.0, 0.0, 0.0, 0.0, 0.0, 0),
                metadata={"target_lifecycle": plan.to_metadata()},
            )

        monkeypatch.setattr(
            "helia_profiler.capture.capture_power",
            fake_capture_power,
        )
        CapturePowerStage().run(ctx)
        assert reset_calls["jlink_serial"] == "1160002204"
        assert reset_calls["device"] == ctx.soc.jlink_device
        assert ctx.power_result is not None
        lifecycle = ctx.power_result.metadata["target_lifecycle"]
        assert {k: v for k, v in lifecycle.items() if k != "timings_s"} == {
            "phase": "power",
            "power_cycle_attempted": False,
            "power_cycle_succeeded": False,
            "reset_strategy": "auto",
            "reset_action": "debug_reset+swpoi_reset",
            "actions": ["debug_reset+swpoi_reset"],
        }
        assert set(lifecycle["timings_s"]) == {"reset"}


class TestTargetLifecycle:
    def _make_ctx(self, tmp_path: Path, *, board: str):
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.platform import get_soc_for_board

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "target": {"board": board, "jlink_serial": "1160002204"},
                "power": {"enabled": True, "driver": "joulescope-js110"},
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        ctx.soc = get_soc_for_board(board)
        return ctx

    def test_ap4_power_phase_uses_debug_reset_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.target_lifecycle import (
            CapturePhase,
            ResetAction,
            prepare_target_for_phase,
        )

        ctx = self._make_ctx(tmp_path, board="apollo4p_blue_kxr_evb")
        calls: list[tuple[str, dict]] = []

        class FakeDriver:
            def power_cycle(self, **kwargs):
                raise AssertionError("auto AP4 reset must not power-cycle")

        monkeypatch.setattr(
            "helia_profiler.jlink.reset_target",
            lambda **k: calls.append(("reset", k)),
        )
        monkeypatch.setattr(
            "helia_profiler.jlink.reset_target_poi",
            lambda **k: calls.append(("swpoi", k)),
        )

        plan = prepare_target_for_phase(
            ctx,
            phase=CapturePhase.POWER,
            power_driver=FakeDriver(),
            power_driver_name="joulescope-js110",
        )

        assert plan.phase is CapturePhase.POWER
        assert plan.power_cycle_attempted is False
        assert plan.power_cycle_succeeded is False
        assert plan.reset_action is ResetAction.DEBUG_RESET
        assert plan.actions == ("debug_reset",)
        assert [name for name, _ in calls] == ["reset"]

    def test_ap5_power_phase_preserves_current_swpoi_policy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.target_lifecycle import (
            CapturePhase,
            ResetAction,
            prepare_target_for_phase,
        )

        ctx = self._make_ctx(tmp_path, board="apollo510_evb")
        calls: list[tuple[str, dict]] = []

        class FakeDriver:
            def power_cycle(self, **kwargs):
                raise AssertionError("auto AP5 reset must not power-cycle")

        monkeypatch.setattr(
            "helia_profiler.jlink.reset_target",
            lambda **k: calls.append(("reset", k)),
        )
        monkeypatch.setattr(
            "helia_profiler.jlink.reset_target_poi",
            lambda **k: calls.append(("swpoi", k)),
        )

        plan = prepare_target_for_phase(
            ctx,
            phase=CapturePhase.POWER,
            power_driver=FakeDriver(),
            power_driver_name="joulescope-js110",
        )

        assert plan.reset_action is ResetAction.DEBUG_RESET_THEN_SWPOI
        assert plan.actions == ("debug_reset+swpoi_reset",)
        assert [name for name, _ in calls] == ["reset", "swpoi"]

    def test_explicit_ap4_swpoi_uses_swpoi_as_replacement(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.target_lifecycle import (
            CapturePhase,
            ResetAction,
            prepare_target_for_phase,
        )

        ctx = self._make_ctx(tmp_path, board="apollo4p_blue_kxr_evb")
        ctx.config = replace(ctx.config, power=replace(ctx.config.power, reset_strategy="swpoi_reset"))
        calls: list[tuple[str, dict]] = []

        class FakeDriver:
            def power_cycle(self, **kwargs):
                raise AssertionError("explicit SWPOI reset must not power-cycle")

        monkeypatch.setattr(
            "helia_profiler.jlink.reset_target",
            lambda **k: calls.append(("reset", k)),
        )
        monkeypatch.setattr(
            "helia_profiler.jlink.reset_target_poi",
            lambda **k: calls.append(("swpoi", k)),
        )

        plan = prepare_target_for_phase(
            ctx,
            phase=CapturePhase.POWER,
            power_driver=FakeDriver(),
            power_driver_name="joulescope-js110",
        )

        assert plan.reset_action is ResetAction.SWPOI_RESET
        assert plan.actions == ("swpoi_reset",)
        assert [name for name, _ in calls] == ["swpoi"]

    def test_explicit_no_reset_does_not_touch_hardware(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.target_lifecycle import CapturePhase, ResetAction, prepare_target_for_phase

        ctx = self._make_ctx(tmp_path, board="apollo4p_blue_kxr_evb")
        ctx.config = replace(ctx.config, power=replace(ctx.config.power, reset_strategy="none"))
        calls: list[tuple[str, dict]] = []

        class FakeDriver:
            def power_cycle(self, **kwargs):
                raise AssertionError("none reset must not power-cycle")

        monkeypatch.setattr(
            "helia_profiler.jlink.reset_target",
            lambda **k: calls.append(("reset", k)),
        )
        monkeypatch.setattr(
            "helia_profiler.jlink.reset_target_poi",
            lambda **k: calls.append(("swpoi", k)),
        )

        plan = prepare_target_for_phase(
            ctx,
            phase=CapturePhase.POWER,
            power_driver=FakeDriver(),
            power_driver_name="joulescope-js110",
        )

        assert plan.reset_action is ResetAction.NONE
        assert plan.actions == ()
        assert [name for name, _ in calls] == []

    def test_explicit_power_cycle_requires_rail_toggle_and_skips_jlink_reset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.target_lifecycle import CapturePhase, ResetAction, prepare_target_for_phase

        ctx = self._make_ctx(tmp_path, board="apollo4p_blue_kxr_evb")
        ctx.config = replace(ctx.config, power=replace(ctx.config.power, reset_strategy="power_cycle"))
        calls: list[tuple[str, dict]] = []

        class FakeDriver:
            def power_cycle(self, **kwargs):
                calls.append(("power_cycle", kwargs))

        monkeypatch.setattr(
            "helia_profiler.jlink.reset_target",
            lambda **k: calls.append(("reset", k)),
        )
        monkeypatch.setattr(
            "helia_profiler.jlink.reset_target_poi",
            lambda **k: calls.append(("swpoi", k)),
        )

        plan = prepare_target_for_phase(
            ctx,
            phase=CapturePhase.POWER,
            power_driver=FakeDriver(),
            power_driver_name="joulescope-js110",
        )

        assert plan.reset_action is ResetAction.NONE
        assert plan.actions == ("power_cycle",)
        assert [name for name, _ in calls] == ["power_cycle"]

    def test_explicit_power_cycle_fails_if_rail_toggle_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.target_lifecycle import CapturePhase, prepare_target_for_phase

        ctx = self._make_ctx(tmp_path, board="apollo4p_blue_kxr_evb")
        ctx.config = replace(ctx.config, power=replace(ctx.config.power, reset_strategy="power_cycle"))
        calls: list[tuple[str, dict]] = []

        class FakeDriver:
            def power_cycle(self, **kwargs):
                raise PowerError("no rail control")

        monkeypatch.setattr(
            "helia_profiler.jlink.reset_target",
            lambda **k: calls.append(("reset", k)),
        )

        with pytest.raises(PowerError, match="no rail control"):
            prepare_target_for_phase(
                ctx,
                phase=CapturePhase.POWER,
                power_driver=FakeDriver(),
                power_driver_name="joulescope-js110",
            )

        assert calls == []

    def test_non_power_phase_does_not_touch_hardware(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.target_lifecycle import CapturePhase, ResetAction, prepare_target_for_phase

        ctx = self._make_ctx(tmp_path, board="apollo510_evb")

        class FakeDriver:
            def power_cycle(self, **kwargs):
                raise AssertionError("non-power phase must not power-cycle")

        monkeypatch.setattr(
            "helia_profiler.jlink.reset_target",
            lambda **k: (_ for _ in ()).throw(AssertionError("must not reset")),
        )

        plan = prepare_target_for_phase(
            ctx,
            phase=CapturePhase.PMU,
            power_driver=FakeDriver(),
            power_driver_name="joulescope-js110",
        )

        assert plan.phase is CapturePhase.PMU
        assert plan.power_cycle_attempted is False
        assert plan.reset_action is ResetAction.NONE


class TestEstimateCaptureDuration:
    """Regression coverage for the auto-tuned capture-duration estimate.

    Bug: the estimate previously only accounted for the per-layer PMU
    passes (presets x (warmup + iterations)) and ignored the separately
    configured GPIO-gated clean window, so a long clean window (window_mode
    'auto' with a large window_target_ms, or a large 'fixed' iterations
    count) produced a safety bound shorter than the actual firmware run,
    causing the Joulescope poller to miss the window's falling edge.
    """

    def _make_ctx(self, tmp_path: Path, *, profiling_overrides: dict):
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.platform import get_soc_for_board
        from helia_profiler.results import FirmwareMeta, LayerResult, PlatformInfo, PmuResult

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "profiling": profiling_overrides,
                "power": {"enabled": True},
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        ctx.soc = get_soc_for_board("apollo510_evb")
        ctx.run_metadata.platform = PlatformInfo(cpu_clock_mhz=96)
        # 96,000 cycles at 96 MHz == 1 ms/inference, a convenient round number.
        ctx.pmu_result = PmuResult(
            meta=FirmwareMeta(presets=("basic_cpu",)),
            layers=[LayerResult(id=0, op="CONV_2D", cycles=96_000.0)],
        )
        return ctx

    def test_fixed_window_includes_clean_iterations(self, tmp_path: Path):
        from helia_profiler.stages.capture_power import (
            _BOOT_SETTLE_S,
            _SAFETY_MARGIN_S,
            _estimate_capture_duration,
        )

        ctx = self._make_ctx(
            tmp_path,
            profiling_overrides={
                "window_mode": "fixed",
                "iterations": 300,
                "warmup": 1,
            },
        )
        estimated = _estimate_capture_duration(ctx)
        assert estimated is not None
        # profiled pass: 1 * (1 + 300) = 301 inferences.
        # clean pass (fixed): max(1, 300) + max(1, 1) = 301 inferences.
        # total = 602 inferences * 1 ms/inference = 0.602 s.
        expected = _BOOT_SETTLE_S + 0.602 + _SAFETY_MARGIN_S
        assert estimated == pytest.approx(expected, rel=1e-6)

    def test_auto_window_scales_with_target_ms(self, tmp_path: Path):
        from helia_profiler.stages.capture_power import (
            _BOOT_SETTLE_S,
            _SAFETY_MARGIN_S,
            _estimate_capture_duration,
        )

        ctx = self._make_ctx(
            tmp_path,
            profiling_overrides={
                "window_mode": "auto",
                "window_target_ms": 8000,
                "window_min": 10,
                "window_max": 500,
                "iterations": 3,
                "warmup": 1,
            },
        )
        estimated = _estimate_capture_duration(ctx)
        assert estimated is not None
        # profiled pass: 1 * (1 + 3) = 4 inferences = 4ms.
        # clean pass (auto): target 8000ms / 1ms = 8000 iters, clamped to
        # window_max=500, plus 3 hardcoded warm reps = 503 inferences = 0.503s.
        expected = _BOOT_SETTLE_S + (0.004 + 0.503) + _SAFETY_MARGIN_S
        assert estimated == pytest.approx(expected, rel=1e-6)

    def test_auto_window_regression_reproduces_prior_underestimate_bug(
        self, tmp_path: Path
    ):
        # This mirrors the real config that triggered "No GPIO-high windows
        # detected": a 21.136ms-per-inference model with window_target_ms
        # 8000 needs ~379 clean iterations (~8s), which the old estimate
        # (based only on the 4 profiled PMU passes) undercounted as ~7.1s.
        from helia_profiler.stages.capture_power import _estimate_capture_duration
        from helia_profiler.results import FirmwareMeta, LayerResult, PmuResult

        ctx = self._make_ctx(
            tmp_path,
            profiling_overrides={
                "window_mode": "auto",
                "window_target_ms": 8000,
                "window_min": 10,
                "window_max": 500,
                "iterations": 3,
                "warmup": 1,
            },
        )
        # 2,029,073 cycles at 96 MHz == ~21.136ms/inference (real AP510 KWS values).
        ctx.pmu_result = PmuResult(
            meta=FirmwareMeta(presets=("basic_cpu",)),
            layers=[LayerResult(id=0, op="CONV_2D", cycles=2_029_073.0)],
        )
        estimated = _estimate_capture_duration(ctx)
        assert estimated is not None
        # The real firmware run was observed at ~8.16s wall-clock; the fixed
        # estimate must cover that, unlike the old ~7.1s underestimate.
        assert estimated > 8.16


class TestCapturePowerWrapper:
    def test_capture_power_uses_gated_joulescope_path_and_preserves_serial(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.capture import capture_power
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.results import FirmwareMeta, PmuResult

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "power": {
                    "enabled": True,
                    "driver": "joulescope-js110",
                    "serial": "004204",
                    "sync_input_index": 0,
                },
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        ctx.pmu_result = PmuResult(meta=FirmwareMeta(clean_infer_count=11), layers=[])

        summary = PowerSummary(0.01, 0.02, 0.03, 0.04, 0.05, 6)
        called: dict[str, object] = {}

        class FakeDriver:
            def check_available(self):
                called["checked"] = True

            def capture(self, **kwargs):
                called["capture"] = kwargs
                return PowerResult(summary=summary)

            def capture_gated(self, **kwargs):
                called["capture_gated"] = kwargs
                return PowerResult(
                    summary=summary,
                    metadata={"measurement_scope": "gpio_gated_clean_window"},
                )

        def fake_get_driver(name: str, *, serial: str | None = None):
            called["name"] = name
            called["serial"] = serial
            return FakeDriver()

        monkeypatch.setattr("helia_profiler.power.get_driver", fake_get_driver)

        result = capture_power(ctx, duration_override_s=7.0)

        assert result.metadata["measurement_scope"] == "gpio_gated_clean_window"
        assert called["name"] == "joulescope-js110"
        assert called["serial"] == "004204"
        assert called["checked"] is True
        assert "capture" not in called
        gated = dict(called["capture_gated"])
        on_started = gated.pop("on_started")
        assert callable(on_started)
        assert gated == {
            "duration_s": 7.0,
            "io_voltage": 1.8,
            "sync_input_index": 0,
            "stats_rate_hz": 1000,
            "clean_infer_count": 11,
        }

    def test_capture_power_waits_for_lockstep_ready_before_go(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.capture import capture_power
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.results import FirmwareMeta, PmuResult

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "power": {
                    "enabled": True,
                    "driver": "joulescope-js110",
                    "lockstep": True,
                    "state_gpio_pin": 23,
                    "go_gpio_pin": 24,
                },
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        ctx.pmu_result = PmuResult(meta=FirmwareMeta(clean_infer_count=11), layers=[])

        calls: list[str] = []
        summary = PowerSummary(0.01, 0.02, 0.03, 0.04, 0.05, 6)

        class FakeSync:
            lockstep = True

            def arm(self):
                calls.append("arm")

            def wait_ready(self, *, timeout_s: float):
                calls.append(f"wait_ready:{timeout_s}")
                return True

            def signal_go(self):
                calls.append("go")

            def read_state(self):
                raise AssertionError("read_state should not be called on ready path")

            def release(self):
                calls.append("release")

        class FakeDriver:
            def check_available(self):
                calls.append("check")

            def make_sync_controller(self, wiring):
                calls.append("make_sync")
                return FakeSync()

            def capture_gated(self, **kwargs):
                calls.append("capture_gated")
                kwargs["on_started"]()
                return PowerResult(summary=summary)

        monkeypatch.setattr("helia_profiler.power.get_driver", lambda *a, **k: FakeDriver())

        def prepare_target(driver, name):
            calls.append(f"prepare:{name}")

            class Plan:
                def to_metadata(self):
                    return {"reset_action": "debug_reset"}

            return Plan()

        result = capture_power(ctx, duration_override_s=7.0, prepare_target=prepare_target)

        assert calls == [
            "check",
            "make_sync",
            "arm",
            "prepare:joulescope-js110",
            "wait_ready:7.0",
            "capture_gated",
            "go",
            "release",
        ]
        assert result.metadata["sync"]["lockstep"] is True
        assert result.metadata["sync"]["ready_wait_s"] >= 0.0
        assert result.metadata["sync"]["ready_observed"] is True
        assert result.metadata["target_lifecycle"] == {"reset_action": "debug_reset"}
