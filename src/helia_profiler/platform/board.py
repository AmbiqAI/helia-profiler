"""Board definitions and the built-in board registry."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_SYNC_GPIO_PIN = 10
# 3-wire lock-step sync defaults: device drives gate + state, host drives go.
# 0 disables the wire (degrades gracefully to 1-wire gate-only handshake).
DEFAULT_STATE_GPIO_PIN = 0
DEFAULT_GO_GPIO_PIN = 0


@dataclass(frozen=True)
class BoardDef:
    """Definition of an evaluation board."""

    name: str
    soc: str  # SoC name key (matches SocDef.name)
    channel: str  # "stable" or "preview"
    psram_kb: int | None = None  # None = inherit SoC default
    default_sync_gpio_pin: int = DEFAULT_SYNC_GPIO_PIN  # gate (device -> host)
    default_state_gpio_pin: int = DEFAULT_STATE_GPIO_PIN  # state/error (device -> host)
    default_go_gpio_pin: int = DEFAULT_GO_GPIO_PIN  # go (host -> device)
    starter_profile_board: str | None = None  # derive NSX profile/modules from this board
    # FPGA carrier boards do not model the NPU power-domain acknowledgement
    # (the NPU is always-on), so NPU init must tolerate a failed handshake.
    is_fpga: bool = False
    description: str = ""
    # GPIO holding the onboard Cooper BLE controller in RESET (SiP "Blue"
    # packages only). Held low during a power capture that doesn't need
    # Bluetooth. None = no onboard BLE radio.
    #
    # Unlike the sentinel-0 pins above, 0 is a valid pin here, so every
    # consumer checks `is not None`, never truthiness (firmware/context.py,
    # firmware/__init__.py, _ble_reset.j2). The YAML surface refuses a
    # literal 0 for the same reason (see custom.py's _ble_reset_gpio_pin).
    ble_reset_gpio_pin: int | None = None

    @property
    def profile_source_board(self) -> str:
        """Board name used to derive starter-profile-owned NSX metadata."""
        return self.starter_profile_board or self.name
