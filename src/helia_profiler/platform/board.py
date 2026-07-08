"""Board definitions and the built-in board registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .capabilities import PowerCaptureCapabilities

DEFAULT_SYNC_GPIO_PIN = 10
# 3-wire lock-step sync defaults: device drives gate + state, host drives go.
# 0 disables the wire (degrades gracefully to 1-wire gate-only handshake).
DEFAULT_STATE_GPIO_PIN = 0
DEFAULT_GO_GPIO_PIN = 0


# ---------------------------------------------------------------------------
# Board definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoardDef:
    """Definition of an evaluation board."""

    name: str  # e.g. "apollo510_evb"
    soc: str  # SoC name key (matches SocDef.name)
    channel: str  # "stable" or "preview"
    psram_kb: int | None = None  # None = inherit SoC default
    default_sync_gpio_pin: int = DEFAULT_SYNC_GPIO_PIN  # gate (device -> host)
    default_state_gpio_pin: int = DEFAULT_STATE_GPIO_PIN  # state/error (device -> host)
    default_go_gpio_pin: int = DEFAULT_GO_GPIO_PIN  # go (host -> device)
    starter_profile_board: str | None = None  # derive NSX profile/modules from this board
    description: str = ""
    # GPIO that drives the onboard Cooper BLE controller's hardware RESET
    # line (SiP "Blue" packages only). Mirrors AutoDeploy's
    # AM_DEVICES_BLECTRLR_RESET_PIN / ns_power_down_peripherals(): held low
    # (in reset) whenever a power capture doesn't need Bluetooth, since an
    # un-reset Cooper radio idles at non-trivial standby current that a
    # non-Blue board doesn't have to begin with. None = no onboard BLE
    # radio on this board (nothing to hold in reset).
    ble_reset_gpio_pin: int | None = None

    @property
    def profile_source_board(self) -> str:
        """Board name used to derive starter-profile-owned NSX metadata."""
        return self.starter_profile_board or self.name

    @property
    def power_capture(self) -> PowerCaptureCapabilities:
        """Power-capture wiring defaults (sync/state/go GPIO pins) for this board."""
        from .capabilities import build_power_capture_capabilities

        return build_power_capture_capabilities(self)


# ---------------------------------------------------------------------------
# Built-in board registry
# ---------------------------------------------------------------------------

_BOARDS: dict[str, BoardDef] = {}


def _register_board(board: BoardDef) -> BoardDef:
    _BOARDS[board.name] = board
    return board


# Power-capture GPIOs: gate GPIO 26 / state GPIO 24 (device->JS GPI0/GPI1)
# + trigger GPIO 25 (JS GPO0->device).
#
# The AutoDeploy-style 22/23/24 wiring is NOT usable on Apollo3 EVBs: pads
# 22/23 are the J-Link OB virtual-COM UART (AM_BSP_GPIO_COM_UART_TX/RX), so
# muxing them to GPIO kills the `uart` transport (22) and contends with the
# J-Link OB driver (23). Pads 24/25/26 carry only dormant MSPI0/IOM2 claims,
# which means power sync still conflicts with PSRAM placement on this board.
_register_board(
    BoardDef(
        "apollo3p_evb",
        soc="apollo3p",
        channel="stable",
        psram_kb=8192,
        default_sync_gpio_pin=26,
        default_state_gpio_pin=24,
        default_go_gpio_pin=25,
    )
)
_register_board(
    BoardDef(
        "apollo3p_evb_cygnus",
        soc="apollo3p",
        channel="preview",
        psram_kb=8192,
        default_sync_gpio_pin=26,
        default_state_gpio_pin=24,
        default_go_gpio_pin=25,
    )
)

# Power-capture GPIOs mirror neuralSPOT AutoDeploy AP4 wiring:
#   AP4P: state bus GPIO 22/23 + trigger GPIO 24.
#   AP4L: state0 moves to GPIO 61 (22 unavailable), state1 GPIO 23 + trigger GPIO 24.
_register_board(
    BoardDef(
        "apollo4p_evb",
        soc="apollo4p",
        channel="preview",
        psram_kb=32768,
        default_sync_gpio_pin=22,
        default_state_gpio_pin=23,
        default_go_gpio_pin=24,
    )
)
_register_board(
    BoardDef(
        "apollo4l_evb",
        soc="apollo4l",
        channel="preview",
        psram_kb=32768,
        default_sync_gpio_pin=61,
        default_state_gpio_pin=23,
        default_go_gpio_pin=24,
    )
)
_register_board(
    BoardDef(
        "apollo4l_blue_evb",
        soc="apollo4l",
        channel="preview",
        psram_kb=32768,
        default_sync_gpio_pin=61,
        default_state_gpio_pin=23,
        default_go_gpio_pin=24,
        # AM_DEVICES_BLECTRLR_RESET_PIN (AmbiqSuite BSP am_bsp.h for this board).
        ble_reset_gpio_pin=55,
    )
)
_register_board(
    BoardDef(
        "apollo4p_blue_kbr_evb",
        soc="apollo4p",
        channel="preview",
        psram_kb=32768,
        default_sync_gpio_pin=22,
        default_state_gpio_pin=23,
        default_go_gpio_pin=24,
        # AM_DEVICES_BLECTRLR_RESET_PIN (AmbiqSuite BSP am_bsp.h for this board).
        ble_reset_gpio_pin=42,
    )
)
_register_board(
    BoardDef(
        "apollo4p_blue_kxr_evb",
        soc="apollo4p",
        channel="preview",
        psram_kb=32768,
        default_sync_gpio_pin=22,
        default_state_gpio_pin=23,
        default_go_gpio_pin=24,
        # AM_DEVICES_BLECTRLR_RESET_PIN (AmbiqSuite BSP am_bsp.h for this board).
        ble_reset_gpio_pin=55,
    )
)

_register_board(
    BoardDef(
        "apollo510_evb",
        soc="apollo510",
        channel="stable",
        default_sync_gpio_pin=29,
        default_state_gpio_pin=36,
        default_go_gpio_pin=14,
    )
)
_register_board(
    BoardDef(
        "apollo510b_evb",
        soc="apollo510b",
        channel="preview",
        default_sync_gpio_pin=29,
        default_state_gpio_pin=36,
        default_go_gpio_pin=14,
    )
)
_register_board(BoardDef("apollo5b_evb", soc="apollo5b", channel="preview"))
_register_board(
    BoardDef(
        "apollo330mP_evb",
        soc="apollo330P",
        channel="preview",
        description="Apollo330 — Cortex-M55 (AP5 family)",
    )
)
