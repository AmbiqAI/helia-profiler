"""INA228 on-device power measurement — config, driver, and envelope contract.

Firmware template coverage lives in test_template_render.py
(TestIna228PowerRender); module-graph coverage in test_firmware.py.
"""

from __future__ import annotations

from pathlib import Path

import pydantic
import pytest

from helia_profiler.capture.power_terminal import parse_power_terminal_envelope
from helia_profiler.config import (
    Ina228Config,
    PowerConfig,
    PowerMode,
    load_config,
)
from helia_profiler.errors import PowerError
from helia_profiler.firmware.context import PowerMonitorContext
from helia_profiler.power import get_driver, list_drivers


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestIna228Config:
    def test_defaults(self):
        ina = Ina228Config(shunt_ohms=2.0)
        assert ina.max_current_a == 0.5
        assert ina.i2c_iom == 1
        assert ina.i2c_address == 0x40
        assert ina.i2c_speed_hz == 400_000
        assert ina.conversion_time_us == 540
        assert ina.averaging_count == 16
        assert ina.calibration_id is None

    @pytest.mark.parametrize(
        ("kwargs", "fragment"),
        [
            ({"shunt_ohms": 0}, "shunt_ohms"),
            ({"shunt_ohms": -1.0}, "shunt_ohms"),
            ({"shunt_ohms": 2.0, "max_current_a": 0}, "max_current_a"),
            ({"shunt_ohms": 2.0, "i2c_iom": 8}, "i2c_iom"),
            ({"shunt_ohms": 2.0, "i2c_iom": -1}, "i2c_iom"),
            ({"shunt_ohms": 2.0, "i2c_address": 0x00}, "i2c_address"),
            ({"shunt_ohms": 2.0, "i2c_address": 0x99}, "i2c_address"),
            ({"shunt_ohms": 2.0, "i2c_speed_hz": 0}, "i2c_speed_hz"),
            # 100 us is not one of the INA228's discrete hardware steps.
            ({"shunt_ohms": 2.0, "conversion_time_us": 100}, "conversion_time_us"),
            ({"shunt_ohms": 2.0, "averaging_count": 3}, "averaging_count"),
        ],
    )
    def test_rejects_invalid_values(self, kwargs: dict, fragment: str):
        with pytest.raises(pydantic.ValidationError, match=fragment):
            Ina228Config(**kwargs)


class TestPowerConfigIna228Coupling:
    def test_driver_requires_ina228_block(self):
        with pytest.raises(pydantic.ValidationError, match="power.ina228 block"):
            PowerConfig(enabled=True, driver="ina228", mode=PowerMode.INTERNAL)

    def test_driver_requires_internal_mode(self):
        with pytest.raises(pydantic.ValidationError, match="power.mode: internal"):
            PowerConfig(
                enabled=True,
                driver="ina228",
                ina228=Ina228Config(shunt_ohms=2.0),
            )

    def test_driver_requires_dedicated_firmware(self):
        with pytest.raises(pydantic.ValidationError, match="dedicated"):
            PowerConfig(
                enabled=True,
                driver="ina228",
                mode=PowerMode.INTERNAL,
                firmware="shared",
                ina228=Ina228Config(shunt_ohms=2.0),
            )

    def test_happy_path(self):
        power = PowerConfig(
            enabled=True,
            driver="ina228",
            mode=PowerMode.INTERNAL,
            ina228=Ina228Config(shunt_ohms=2.0),
        )
        assert power.ina228 is not None and power.ina228.shunt_ohms == 2.0

    def test_joulescope_default_is_untouched(self):
        power = PowerConfig(enabled=True)
        assert power.driver == "joulescope"
        assert power.ina228 is None

    def test_yaml_round_trip_through_load_config(self, tmp_path: Path):
        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x1c\x00\x00\x00TFL3" + b"\x00" * 100)
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "tflm"},
                "power": {
                    "enabled": True,
                    "driver": "ina228",
                    "mode": "internal",
                    "ina228": {"shunt_ohms": 2.0, "i2c_iom": 3},
                },
                "work_dir": str(tmp_path / "work"),
            },
        )
        assert config.power.driver == "ina228"
        assert config.power.ina228 is not None
        assert config.power.ina228.i2c_iom == 3


# ---------------------------------------------------------------------------
# Driver registration and capability flags
# ---------------------------------------------------------------------------


class TestIna228Driver:
    def test_registered(self):
        assert "ina228" in list_drivers()

    def test_capabilities(self):
        driver = get_driver("ina228")
        assert driver.mode is PowerMode.INTERNAL
        assert driver.supports_firmware_measurement is True
        assert getattr(driver, "supports_gated_capture", False) is False

    def test_no_host_side_dependencies(self):
        get_driver("ina228").check_available()  # must not raise

    def test_generic_ondevice_stub_still_has_no_producer(self):
        driver = get_driver("ondevice")
        assert driver.supports_firmware_measurement is False

    def test_host_side_capture_paths_stay_closed(self):
        driver = get_driver("ina228")
        with pytest.raises(PowerError):
            driver.capture(duration_s=1.0, io_voltage=1.8)
        with pytest.raises(PowerError):
            driver.capture_gated(duration_s=1.0, io_voltage=1.8, sync_input_index=0)
        with pytest.raises(PowerError):
            driver.power_cycle()

    def test_ensure_target_powered_is_permissive(self):
        assert get_driver("ina228").ensure_target_powered(required=True) is True


# ---------------------------------------------------------------------------
# Render-context derivation
# ---------------------------------------------------------------------------


def _profile_config(tmp_path: Path, power: dict):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x1c\x00\x00\x00TFL3" + b"\x00" * 100)
    return load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "tflm"},
            "power": power,
            "work_dir": str(tmp_path / "work"),
        },
    )


class TestPowerMonitorContext:
    def test_disabled_without_ina228_driver(self, tmp_path: Path):
        config = _profile_config(tmp_path, {"enabled": True})
        assert PowerMonitorContext.from_config(config).power_monitor is None

    def test_disabled_when_power_off(self, tmp_path: Path):
        config = _profile_config(tmp_path, {"enabled": False})
        assert PowerMonitorContext.from_config(config).power_monitor is None

    def test_integer_scaling_and_wide_adc_range(self, tmp_path: Path):
        config = _profile_config(
            tmp_path,
            {
                "enabled": True,
                "driver": "ina228",
                "mode": "internal",
                # 0.1 ohm x 0.5 A = 50 mV worst case > 40.96 mV -> range 0
                "ina228": {"shunt_ohms": 0.1, "max_current_a": 0.5},
            },
        )
        monitor = PowerMonitorContext.from_config(config)
        assert monitor.power_monitor == "ina228"
        assert monitor.ina228_shunt_micro_ohms == 100_000
        assert monitor.ina228_max_current_ma == 500
        assert monitor.ina228_adc_range == 0
        assert monitor.ina228_calibration_id == "ina228:r100000uohm:i500ma:adc0"

    def test_narrow_shunt_drop_selects_high_resolution_range(self, tmp_path: Path):
        config = _profile_config(
            tmp_path,
            {
                "enabled": True,
                "driver": "ina228",
                "mode": "internal",
                # 0.1 ohm x 0.4 A = 40 mV <= 40.96 mV -> range 1
                "ina228": {"shunt_ohms": 0.1, "max_current_a": 0.4},
            },
        )
        monitor = PowerMonitorContext.from_config(config)
        assert monitor.ina228_adc_range == 1

    def test_explicit_calibration_id_wins(self, tmp_path: Path):
        config = _profile_config(
            tmp_path,
            {
                "enabled": True,
                "driver": "ina228",
                "mode": "internal",
                "ina228": {"shunt_ohms": 2.0, "calibration_id": "bench-A"},
            },
        )
        assert PowerMonitorContext.from_config(config).ina228_calibration_id == "bench-A"


# ---------------------------------------------------------------------------
# Envelope wire-format round trip (exactly what _power_terminal.j2 emits)
# ---------------------------------------------------------------------------


def _success_record() -> str:
    return (
        "--- HPX_POWER_TERMINAL_START ---\n"
        "HPX_POWER_TERMINAL_VERSION=1\n"
        "HPX_POWER_STATUS=ok\n"
        "HPX_POWER_REQUESTED_COUNT=2000\n"
        "HPX_POWER_COMPLETED_COUNT=2000\n"
        "HPX_POWER_ELAPSED_US=512345\n"
        "HPX_POWER_FINAL_PHASE=complete\n"
        "HPX_POWER_ERROR_CODE=0\n"
        "HPX_POWER_GATE_ASSERTED=0\n"
        "HPX_POWER_GATE_LOWERED=1\n"
        "HPX_POWER_MEASUREMENT_SOURCE=ina228\n"
        "HPX_POWER_MEASUREMENT_SCOPE=fixed_n_inference\n"
        "HPX_POWER_ENERGY_NJ=4200000\n"
        "HPX_POWER_MEASUREMENT_DURATION_US=512345\n"
        "HPX_POWER_MEASUREMENT_COUNT=2000\n"
        "HPX_POWER_MEASUREMENT_OVERFLOW=0\n"
        "HPX_POWER_CHARGE_NC=3500000\n"
        "HPX_POWER_BUS_VOLTAGE_UV=1800000\n"
        "HPX_POWER_CALIBRATION_ID=ina228:r2000000uohm:i500ma:adc0\n"
        "--- HPX_POWER_TERMINAL_END ---\n"
    )


class TestIna228EnvelopeWireFormat:
    def test_success_record_round_trips(self):
        envelope = parse_power_terminal_envelope(_success_record().splitlines())
        measurement = envelope.measurement
        assert measurement is not None
        assert measurement.source == "ina228"
        assert measurement.scope == "fixed_n_inference"
        assert measurement.energy_nj == 4_200_000
        assert measurement.charge_nc == 3_500_000
        assert measurement.bus_voltage_uv == 1_800_000
        assert measurement.duration_us == 512_345
        assert measurement.inference_count == 2000
        assert measurement.overflow is False
        assert measurement.calibration_id == "ina228:r2000000uohm:i500ma:adc0"

    def test_failure_record_carries_phase_and_no_measurement(self):
        """The ina228_init/arm/read fail paths emit no measurement keys."""
        record = (
            "--- HPX_POWER_TERMINAL_START ---\n"
            "HPX_POWER_TERMINAL_VERSION=1\n"
            "HPX_POWER_STATUS=error\n"
            "HPX_POWER_REQUESTED_COUNT=2000\n"
            "HPX_POWER_COMPLETED_COUNT=0\n"
            "HPX_POWER_ELAPSED_US=0\n"
            "HPX_POWER_FINAL_PHASE=ina228_init\n"
            "HPX_POWER_ERROR_CODE=2\n"
            "HPX_POWER_GATE_ASSERTED=0\n"
            "HPX_POWER_GATE_LOWERED=1\n"
            "--- HPX_POWER_TERMINAL_END ---\n"
        )
        envelope = parse_power_terminal_envelope(record.splitlines())
        assert envelope.measurement is None
        assert envelope.terminal.status == "error"
        assert envelope.terminal.final_phase == "ina228_init"
        assert envelope.terminal.error_code == 2

    def test_overflow_flag_survives_parsing(self):
        record = _success_record().replace(
            "HPX_POWER_MEASUREMENT_OVERFLOW=0", "HPX_POWER_MEASUREMENT_OVERFLOW=1"
        )
        envelope = parse_power_terminal_envelope(record.splitlines())
        assert envelope.measurement is not None
        assert envelope.measurement.overflow is True
