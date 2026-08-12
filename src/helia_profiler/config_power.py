"""Power measurement configuration — constants, `PowerConfig`, `Ina228Config`.

Extracted from ``config.py`` as a cohesive responsibility; everything here is
re-exported from :mod:`helia_profiler.config`, which remains the public import
surface (``from helia_profiler.config import PowerConfig`` keeps working).
"""

from __future__ import annotations

from pydantic import ConfigDict, model_validator
from pydantic.dataclasses import dataclass as pydantic_dataclass

from .platform import (
    DEFAULT_GO_GPIO_PIN,
    DEFAULT_STATE_GPIO_PIN,
    DEFAULT_SYNC_GPIO_PIN,
)
from .power.base import PowerMode
from .target.lifecycle import ResetStrategy

DEFAULT_POWER_DURATION_S = 30
DEFAULT_IO_VOLTAGE = 1.8
DEFAULT_POWER_DRIVER = "joulescope"
# Which binary is on the target during power capture. "dedicated" flashes the
# transport-free hpx_profiler_power image (see firmware/__init__.py WP2)
# before capture; SWO/UART/RTT/USB traffic on the shared transport binary was
# measured to add significant current contamination into the GPIO-gated
# Joulescope window on AP510 depending on transport, so "dedicated" is the
# default. "shared" restores the pre-WP2 behavior of reusing the
# already-flashed transport binary for power capture (useful when no J-Link
# is free to reflash, or for bring-up comparisons against the contaminated
# baseline).
POWER_FIRMWARE_MODES = ("dedicated", "shared")
DEFAULT_POWER_FIRMWARE = "dedicated"
DEFAULT_POWER_MODE = PowerMode.EXTERNAL
# On-target INA228 power monitor (power.driver: ina228). The ADC conversion
# time and averaging window are the INA228's own discrete hardware steps —
# arbitrary values cannot be programmed, so configs must pick from these.
INA228_CONVERSION_TIMES_US = (50, 84, 150, 280, 540, 1052, 2074, 4120)
INA228_AVERAGING_COUNTS = (1, 4, 16, 64, 128, 256, 512, 1024)
DEFAULT_INA228_I2C_ADDRESS = 0x40
DEFAULT_INA228_I2C_SPEED_HZ = 400_000  # I2C fast mode
DEFAULT_INA228_CONVERSION_TIME_US = 540
DEFAULT_INA228_AVERAGING_COUNT = 16
DEFAULT_POWER_SYNC_INPUT_INDEX = 0
# 3-wire lock-step sync: extra host-side digital channels. gate=INPUT0,
# state/error=INPUT1, go (host->device) = OUTPUT0. Lock-step is off by default
# so existing 1-wire gate captures are unchanged.
DEFAULT_POWER_STATE_INPUT_INDEX = 1
DEFAULT_POWER_GO_OUTPUT_INDEX = 0
#: Host-side statistics rate (Hz) for GPIO-gated Joulescope capture. The device
#: integrates charge/energy at its full native rate (~2 MSPS) and delivers the
#: integrals as stat packets at this cadence; ~1 kHz brackets a ~250 ms window
#: to <1% while keeping the data volume at a few KB (vs MB/s for raw streaming).
DEFAULT_POWER_STATS_RATE_HZ = 1000


@pydantic_dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class Ina228Config:
    """On-target INA228 power monitor wiring and calibration (I2C).

    The INA228 sits in series with the target rail and integrates energy and
    charge in hardware; firmware reads the accumulators over I2C around the
    fixed-N inference window. ``shunt_ohms`` has no default on purpose: a
    wrong shunt calibration produces plausible-looking but wrong energy, and
    the resistor value is a physical property of the user's wiring that HPX
    cannot guess.
    """

    # Shunt resistance in ohms (e.g. 2.0 for the MikroE Power Monitor Click).
    shunt_ohms: float
    # Full-scale current used to derive CURRENT_LSB for the shunt calibration.
    max_current_a: float = 0.5
    # Ambiq IOM instance the monitor's I2C bus is wired to.
    i2c_iom: int = 1
    i2c_address: int = DEFAULT_INA228_I2C_ADDRESS
    i2c_speed_hz: int = DEFAULT_INA228_I2C_SPEED_HZ
    # Per-signal ADC conversion time and averaging window — discrete INA228
    # hardware steps (INA228_CONVERSION_TIMES_US / INA228_AVERAGING_COUNTS).
    conversion_time_us: int = DEFAULT_INA228_CONVERSION_TIME_US
    averaging_count: int = DEFAULT_INA228_AVERAGING_COUNT
    # Free-form calibration label carried into result metadata. Defaults to a
    # value derived from shunt_ohms/max_current_a at firmware render time.
    calibration_id: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> Ina228Config:
        if self.shunt_ohms <= 0:
            raise ValueError(f"power.ina228.shunt_ohms must be > 0, got {self.shunt_ohms}.")
        if self.max_current_a <= 0:
            raise ValueError(f"power.ina228.max_current_a must be > 0, got {self.max_current_a}.")
        if not 0 <= self.i2c_iom <= 7:
            raise ValueError(f"power.ina228.i2c_iom must be 0..7, got {self.i2c_iom}.")
        if not 0x08 <= self.i2c_address <= 0x77:
            raise ValueError(
                f"power.ina228.i2c_address must be a 7-bit address in 0x08..0x77, "
                f"got 0x{self.i2c_address:02x}."
            )
        if self.i2c_speed_hz <= 0:
            raise ValueError(f"power.ina228.i2c_speed_hz must be > 0, got {self.i2c_speed_hz}.")
        if self.conversion_time_us not in INA228_CONVERSION_TIMES_US:
            raise ValueError(
                f"power.ina228.conversion_time_us must be one of "
                f"{INA228_CONVERSION_TIMES_US}, got {self.conversion_time_us}."
            )
        if self.averaging_count not in INA228_AVERAGING_COUNTS:
            raise ValueError(
                f"power.ina228.averaging_count must be one of "
                f"{INA228_AVERAGING_COUNTS}, got {self.averaging_count}."
            )
        return self


@pydantic_dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class PowerConfig:
    """Power measurement settings."""

    enabled: bool = False
    driver: str = DEFAULT_POWER_DRIVER
    # "dedicated" flashes hpx_profiler_power (transport-free) before capture;
    # "shared" reuses the already-flashed transport binary. See
    # POWER_FIRMWARE_MODES above for the contamination rationale.
    firmware: str = DEFAULT_POWER_FIRMWARE
    mode: PowerMode = DEFAULT_POWER_MODE
    # ``None`` means "not explicitly set": consumers use
    # DEFAULT_POWER_DURATION_S and may auto-tune the bound from PMU-phase
    # timing.  An explicit value (YAML or --power-duration, even if equal to
    # the default) always wins and disables auto-tuning.
    duration_s: int | None = None
    io_voltage: float = DEFAULT_IO_VOLTAGE
    sync_gpio_pin: int = DEFAULT_SYNC_GPIO_PIN  # GPIO for external sync
    # Host-side sync input index on external instruments. For Joulescope this
    # is the digital input channel number (validated default wiring is INPUT0).
    sync_input_index: int = DEFAULT_POWER_SYNC_INPUT_INDEX
    # Optional 3-wire lock-step handshake (AutoDeploy-compatible wiring).
    # gate=sync_gpio_pin (device->host), state_gpio_pin (device->host),
    # go_gpio_pin (host->device). 0 disables a wire; lockstep stays off until
    # the monitor exposes a GO output and both extra pins are configured.
    # ``None`` means "not explicitly set": callers resolve the effective value
    # via ``target.lifecycle.resolve_power_lockstep``, which auto-enables
    # lock-step when the board is wired for it and the SoC family's default
    # power reset policy needs it to stay race-free (e.g. Apollo5's
    # debug_reset+swpoi_reset combo -- see the AP510 combo+RTT gate-race
    # investigation). An explicit ``true``/``false`` here always wins.
    lockstep: bool | None = None
    state_gpio_pin: int = DEFAULT_STATE_GPIO_PIN
    go_gpio_pin: int = DEFAULT_GO_GPIO_PIN
    state_input_index: int = DEFAULT_POWER_STATE_INPUT_INDEX
    go_output_index: int = DEFAULT_POWER_GO_OUTPUT_INDEX
    # Host-side statistics rate (Hz) for gated Joulescope capture. Controls the
    # cadence of on-device-integrated charge/energy stat packets used to bracket
    # the gated window. Higher = finer edge resolution, more (still tiny) packets.
    stats_rate_hz: int = DEFAULT_POWER_STATS_RATE_HZ
    # Reset strategy before power capture. "auto" keeps board/SoC defaults;
    # explicit strategies are for bring-up experiments and custom boards.
    reset_strategy: ResetStrategy = ResetStrategy.AUTO
    # Optional Joulescope serial number (e.g. "004204") to disambiguate
    # when more than one device is plugged in. Leave None to auto-pick the
    # single available device (and fail loudly if multiple are present).
    serial: str | None = None
    # On-target INA228 monitor settings; required when driver is "ina228".
    ina228: Ina228Config | None = None

    @model_validator(mode="after")
    def _validate(self) -> PowerConfig:
        if self.sync_input_index < 0:
            raise ValueError(f"power.sync_input_index must be >= 0, got {self.sync_input_index}.")
        if self.stats_rate_hz < 1:
            raise ValueError(f"power.stats_rate_hz must be >= 1, got {self.stats_rate_hz}.")
        if self.lockstep and (self.state_gpio_pin <= 0 or self.go_gpio_pin <= 0):
            raise ValueError("power.lockstep requires both state_gpio_pin and go_gpio_pin > 0.")
        if self.firmware not in POWER_FIRMWARE_MODES:
            raise ValueError(
                f"Unknown power.firmware '{self.firmware}'. "
                f"Choose one of: {', '.join(POWER_FIRMWARE_MODES)}."
            )
        if self.driver == "ina228":
            # The INA228 is read by firmware around the fixed-N window, so the
            # driver only works as an internal-mode measurement in the
            # dedicated power binary. Explicit over auto-magic: require the
            # config to say so rather than silently flipping modes.
            if self.ina228 is None:
                raise ValueError(
                    "power.driver: ina228 requires a power.ina228 block "
                    "(at minimum shunt_ohms — the physical shunt value on your wiring)."
                )
            if self.mode is not PowerMode.INTERNAL:
                raise ValueError(
                    "power.driver: ina228 measures on-device; set power.mode: internal."
                )
            if self.firmware != "dedicated":
                raise ValueError(
                    "power.driver: ina228 requires power.firmware: dedicated "
                    "(the measurement lives in the fixed-N power binary)."
                )
        return self
