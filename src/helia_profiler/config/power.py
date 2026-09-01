"""Power measurement configuration — constants, `PowerConfig`, `Ina228Config`.

Extracted from ``config.py`` as a cohesive responsibility; everything here is
re-exported from :mod:`helia_profiler.config`, which remains the public import
surface (``from helia_profiler.config import PowerConfig`` keeps working).
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import ConfigDict, model_validator
from pydantic.dataclasses import dataclass as pydantic_dataclass

from ..platform import (
    DEFAULT_GO_GPIO_PIN,
    DEFAULT_STATE_GPIO_PIN,
    DEFAULT_SYNC_GPIO_PIN,
)
from ..power.base import PowerMode
from ..target.lifecycle import ResetStrategy

DEFAULT_POWER_DURATION_S = 30
DEFAULT_IO_VOLTAGE = 1.8
DEFAULT_POWER_DRIVER = "joulescope"


class PowerFirmware(StrEnum):
    """Which binary is on the target during power capture.

    ``DEDICATED`` flashes the transport-free ``hpx_profiler_power`` image
    (see ``firmware/__init__.py`` WP2) before capture; SWO/UART/RTT/USB
    traffic on the shared transport binary was measured to add significant
    current contamination into the GPIO-gated Joulescope window on AP510
    depending on transport, so ``DEDICATED`` is the default.  ``SHARED``
    restores the pre-WP2 behavior of reusing the already-flashed transport
    binary for power capture (useful when no J-Link is free to reflash, or
    for bring-up comparisons against the contaminated baseline).
    """

    DEDICATED = "dedicated"
    SHARED = "shared"


DEFAULT_POWER_FIRMWARE = PowerFirmware.DEDICATED
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
class MonitorBoardPreset:
    """Known breakout/Click board carrying an on-target power monitor chip.

    A preset is pure data: the electrical facts a specific board fixes
    (address strapping, an onboard shunt when the board has one) plus the
    board-specific hint to show when a required fact is missing. Explicit
    ``power.ina228.*`` values always win over the preset. Adding support for
    a new breakout of an already-supported chip is one registry entry here —
    no new driver, no new firmware.
    """

    label: str
    i2c_address: int | None = None
    shunt_ohms: float | None = None
    # Shown when the config resolves no shunt value; lets the error explain
    # the physical fix for this exact board.
    missing_shunt_hint: str | None = None


#: Known INA228 carrier boards, selectable via ``power.ina228.board``.
INA228_BOARD_PRESETS: dict[str, MonitorBoardPreset] = {
    # Schematic: only LED/pull-up resistors on board; IN+/IN- go straight to
    # the IN1 screw terminal, and both ADDR SEL jumpers ship Down (= SDA).
    "mikroe-power-monitor-click": MonitorBoardPreset(
        label="MikroE Power Monitor Click (MIKROE-4810)",
        i2c_address=0x4A,
        shunt_ohms=None,
        missing_shunt_hint=(
            "The Power Monitor Click has no onboard shunt: wire a sense "
            "resistor across the IN1 terminal (IN+/IN-) and set "
            "power.ina228.shunt_ohms to its value."
        ),
    ),
    # Onboard 15 mOhm shunt (R1 = HoLLR2512-2W-15mR-1% per Adafruit's
    # schematic; the product page advertises 0.1%, so treat ~1% as the
    # systematic floor when comparing against another instrument) and the
    # INA228 default 0x40 address strapping.
    "adafruit-ina228": MonitorBoardPreset(
        label="Adafruit INA228 breakout (5832)",
        i2c_address=0x40,
        shunt_ohms=0.015,
    ),
}


@pydantic_dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class Ina228Config:
    """On-target INA228 power monitor wiring and calibration (I2C).

    The INA228 sits in series with the target rail and integrates energy and
    charge in hardware; firmware reads the accumulators over I2C around the
    fixed-N inference window.

    ``board`` selects a known carrier's electrical facts (address strapping,
    onboard shunt) from :data:`INA228_BOARD_PRESETS`; explicit values always
    win over the preset. ``shunt_ohms`` has no bare default on purpose: a
    wrong shunt calibration produces plausible-looking but wrong energy, so
    the value must come either from the user's wiring or from a board that
    physically carries its shunt.
    """

    # Known carrier board (see INA228_BOARD_PRESETS); None = custom wiring.
    board: str | None = None
    # Shunt resistance in ohms. Required unless the selected board preset
    # carries an onboard shunt value.
    shunt_ohms: float | None = None
    # Full-scale current used to derive CURRENT_LSB for the shunt calibration.
    max_current_a: float = 0.5
    # Ambiq IOM instance the monitor's I2C bus is wired to.
    i2c_iom: int = 1
    # None = board preset's strapping, falling back to the INA228 power-on
    # default (0x40, A0/A1 to GND).
    i2c_address: int | None = None
    i2c_speed_hz: int = DEFAULT_INA228_I2C_SPEED_HZ
    # Per-signal ADC conversion time and averaging window — discrete INA228
    # hardware steps (INA228_CONVERSION_TIMES_US / INA228_AVERAGING_COUNTS).
    conversion_time_us: int = DEFAULT_INA228_CONVERSION_TIME_US
    averaging_count: int = DEFAULT_INA228_AVERAGING_COUNT
    # Calibration label carried into result metadata. Defaults to a value
    # derived from the resolved shunt/current at firmware render time.
    # Restricted charset: the value is rendered verbatim inside a C string
    # literal that doubles as an snprintf format string in the power
    # firmware, so '%', quotes, backslashes and whitespace are all unsafe.
    calibration_id: str | None = None

    @property
    def board_preset(self) -> MonitorBoardPreset | None:
        return INA228_BOARD_PRESETS[self.board] if self.board is not None else None

    @property
    def resolved_shunt_ohms(self) -> float:
        """Effective shunt: explicit value, else the board's onboard shunt."""
        if self.shunt_ohms is not None:
            return self.shunt_ohms
        preset = self.board_preset
        assert preset is not None and preset.shunt_ohms is not None  # _validate
        return preset.shunt_ohms

    @property
    def resolved_i2c_address(self) -> int:
        """Effective address: explicit, else board strapping, else chip default."""
        if self.i2c_address is not None:
            return self.i2c_address
        preset = self.board_preset
        if preset is not None and preset.i2c_address is not None:
            return preset.i2c_address
        return DEFAULT_INA228_I2C_ADDRESS

    @model_validator(mode="after")
    def _validate(self) -> Ina228Config:
        if self.board is not None and self.board not in INA228_BOARD_PRESETS:
            known = ", ".join(sorted(INA228_BOARD_PRESETS))
            raise ValueError(
                f"Unknown power.ina228.board '{self.board}'. Known boards: {known}. "
                "For custom wiring omit 'board' and set shunt_ohms/i2c_address directly."
            )
        preset = self.board_preset
        if self.shunt_ohms is None and (preset is None or preset.shunt_ohms is None):
            if preset is not None and preset.missing_shunt_hint is not None:
                raise ValueError(
                    f"power.ina228.shunt_ohms is required for board '{self.board}' "
                    f"({preset.label}). {preset.missing_shunt_hint}"
                )
            raise ValueError(
                "power.ina228.shunt_ohms is required: it is the physical sense "
                "resistor on your wiring, which HPX cannot guess. Boards with an "
                "onboard shunt can set power.ina228.board instead ("
                + ", ".join(sorted(INA228_BOARD_PRESETS))
                + ")."
            )
        if self.shunt_ohms is not None and self.shunt_ohms <= 0:
            raise ValueError(f"power.ina228.shunt_ohms must be > 0, got {self.shunt_ohms}.")
        if self.max_current_a <= 0:
            raise ValueError(f"power.ina228.max_current_a must be > 0, got {self.max_current_a}.")
        if not 0 <= self.i2c_iom <= 7:
            raise ValueError(f"power.ina228.i2c_iom must be 0..7, got {self.i2c_iom}.")
        if self.i2c_address is not None and not 0x08 <= self.i2c_address <= 0x77:
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
        if self.calibration_id is not None and not re.fullmatch(
            r"[A-Za-z0-9._:+-]{1,64}", self.calibration_id
        ):
            raise ValueError(
                "power.ina228.calibration_id must be 1-64 characters from "
                "[A-Za-z0-9._:+-]: it is rendered verbatim into a C format "
                f"string in the power firmware. Got {self.calibration_id!r}."
            )
        return self


@pydantic_dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class PowerConfig:
    """Power measurement settings."""

    enabled: bool = False
    driver: str = DEFAULT_POWER_DRIVER
    # "dedicated" flashes hpx_profiler_power (transport-free) before capture;
    # "shared" reuses the already-flashed transport binary. See
    # :class:`PowerFirmware` above for the contamination rationale.
    firmware: PowerFirmware = DEFAULT_POWER_FIRMWARE
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
    # via :attr:`lockstep_resolved` (re-exported as
    # ``target.lifecycle.resolve_power_lockstep``), which auto-enables
    # lock-step whenever the board is wired for it and gated external capture
    # is requested. An explicit ``true``/``false`` here always wins.
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
    # On-target INA228 monitor settings. The presence of this block — not the
    # driver — decides whether generated power firmware talks to a monitor:
    # required when driver is "ina228", and with any other driver it adds the
    # monitor as a bystander (its IOM stays powered, which costs measurable
    # target current). Delete the block when the monitor is not in use.
    ina228: Ina228Config | None = None

    @property
    def monitor_selected(self) -> bool:
        """Whether generated power firmware talks to an on-target monitor.

        The single source of truth for both firmware gates: NSX module
        selection in ``firmware/__init__.py`` and render-context derivation
        in ``PowerMonitorContext.from_config``. When these two used separate
        predicates and disagreed, runs silently built no monitor at all
        while appearing to configure one.
        """
        return self.enabled and self.ina228 is not None

    @property
    def gated_external_capture(self) -> bool:
        """Whether this run asks for host-gated *external* power capture.

        The single source of the predicate that gates every piece of GPIO sync
        machinery: the firmware's ``kPowerSyncEnabled`` (via
        ``SyncContext.power_sync_enabled`` and the NSX GPIO module selection in
        ``firmware/__init__.py``) and the host-side lock-step default below.
        Internal (on-device monitor) mode measures inside the firmware and has
        no host poller to race, so it is excluded.
        """
        return self.enabled and self.mode is PowerMode.EXTERNAL

    @property
    def lockstep_wiring_available(self) -> bool:
        """Whether the board carries the two extra lock-step wires.

        ``state`` (device -> host) and ``go`` (host -> device); ``0`` means the
        wire is not assigned. Single-sourced because three consumers ask the
        same question and must agree: the lock-step default
        (:attr:`lockstep_resolved`), the ``power.lockstep: true`` config
        validator, and the ``no_gate_rise`` diagnostic, which only names
        lock-step as the likely fix when the wiring can actually support it.
        """
        return self.state_gpio_pin > 0 and self.go_gpio_pin > 0

    @property
    def lockstep_resolved(self) -> bool:
        """Effective 3-wire GPIO lock-step decision for this run.

        An explicit ``power.lockstep`` always wins -- auto-enable is a
        *default*, never an override, so ``lockstep: false`` still forces the
        free-running path for bring-up on incomplete wiring.

        Left unset, lock-step is enabled whenever the board is wired for it and
        gated external capture is requested. The hazard it closes is not
        family-specific: without lock-step ``kSyncLockstep`` bakes false,
        ``hpx_sync_wait_go()`` compiles to a no-op, and the target free-runs its
        measured window straight out of reset. Any reset latency the host
        spends after that -- flash-tool exit, JLinkExe teardown, poller
        start-up -- races the gate. Apollo5's default
        ``debug_reset+swpoi_reset`` makes the gap widest (two sequential
        JLinkExe invocations; see the AP510 combo+RTT ``t2-gate-race``
        investigation, which is why the rule was originally AP5-only), but
        Apollo4 Blue Plus reproduced the same ``no_gate_rise`` degradation on a
        single-invocation ``debug_reset`` with a ~5 s window (issue #114), and
        Apollo3 differs only in how narrow the gap is. So the condition is the
        wiring and the mode, not the SoC family.

        This is the one place both the firmware generator (which bakes
        ``kSyncLockstep`` in at build time, via ``FirmwareRenderContext``) and
        the host-side capture path (which must arm/wait/signal accordingly)
        resolve the *same* answer -- callers must not read
        :attr:`lockstep` directly.
        """
        if self.lockstep is not None:
            return self.lockstep
        return self.gated_external_capture and self.lockstep_wiring_available

    @model_validator(mode="after")
    def _validate(self) -> PowerConfig:
        if self.sync_input_index < 0:
            raise ValueError(f"power.sync_input_index must be >= 0, got {self.sync_input_index}.")
        if self.stats_rate_hz < 1:
            raise ValueError(f"power.stats_rate_hz must be >= 1, got {self.stats_rate_hz}.")
        if self.lockstep and not self.lockstep_wiring_available:
            raise ValueError("power.lockstep requires both state_gpio_pin and go_gpio_pin > 0.")
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
            if self.firmware is not PowerFirmware.DEDICATED:
                raise ValueError(
                    "power.driver: ina228 requires power.firmware: dedicated "
                    "(the measurement lives in the fixed-N power binary)."
                )
        return self
