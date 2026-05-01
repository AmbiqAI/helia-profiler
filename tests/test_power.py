"""Tests for power driver abstraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.errors import PowerError
from helia_profiler.power import get_driver, list_drivers
from helia_profiler.power.base import PowerMode, PowerResult, PowerSample, PowerSummary


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
        assert result.metadata == {}


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
            {"model": {"path": str(model)}, "engine": {"type": "tflm"}},
        )
        assert config.power.enabled is False
        assert config.power.driver == "joulescope"
        assert config.power.mode == "external"
        assert config.power.sync_gpio_pin == 10

    def test_custom_power_config(self, tmp_path: Path):
        from helia_profiler.config import load_config

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "tflm"},
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


class TestCapturePowerStage:
    def test_skipped_when_disabled(self, tmp_path: Path):
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.stages.s07_capture_power import CapturePowerStage

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {"model": {"path": str(model)}, "engine": {"type": "tflm"}},
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        stage = CapturePowerStage()
        assert stage.should_skip(ctx) is True

    def test_not_skipped_when_enabled(self, tmp_path: Path):
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.stages.s07_capture_power import CapturePowerStage

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "tflm"},
                "power": {"enabled": True},
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        stage = CapturePowerStage()
        assert stage.should_skip(ctx) is False
