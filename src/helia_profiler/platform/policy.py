"""Supported profiler targets and their capture/build choices."""

from dataclasses import dataclass

from .board import BoardDef
from .soc import PerfTier, SocFamily


@dataclass(frozen=True)
class SocPolicy:
    board: str
    family: SocFamily
    max_ops: int = 2048
    usb: bool = True
    cpu_points: tuple[tuple[str, str, PerfTier], ...] = (
        ("lp", "lp", PerfTier.LOW),
        ("hp", "hp", PerfTier.HIGH),
    )
    scan_app_only: bool = False
    probe_alias: str | None = None


SOC_POLICIES = {
    "apollo3p": SocPolicy("apollo3p_evb", SocFamily.AP3, usb=False),
    "apollo4p": SocPolicy("apollo4p_evb", SocFamily.AP4),
    "apollo4l": SocPolicy("apollo4l_evb", SocFamily.AP4),
    "apollo510": SocPolicy("apollo510_evb", SocFamily.AP5, max_ops=4096),
    "apollo510b": SocPolicy("apollo510b_evb", SocFamily.AP5, max_ops=4096),
    "apollo330P": SocPolicy(
        "apollo330mP_evb",
        SocFamily.AP5,
        max_ops=512,
        cpu_points=(("lp", "lp", PerfTier.LOW), ("hp", "hp2", PerfTier.HIGH)),
        scan_app_only=True,
        probe_alias="Apollo330P_510L",
    ),
    "apollo510L": SocPolicy(
        "apollo510dL_evb",
        SocFamily.AP5,
        max_ops=512,
        cpu_points=(("lp", "lp", PerfTier.LOW), ("hp", "hp2", PerfTier.HIGH)),
        scan_app_only=True,
        probe_alias="AP510L",
    ),
    "atomiq110": SocPolicy(
        "atomiq110_fpga_turbo",
        SocFamily.AP5,
        max_ops=4096,
        usb=False,
        cpu_points=(("lp", "lp", PerfTier.LOW),),
        scan_app_only=True,
        probe_alias="Atomiq110",
    ),
}

# Probe control is not the physical trace clock. Preserve these routes pending
# their bounded migration checks: https://github.com/AmbiqAI/helia-profiler/issues/657
PROBE_CLOCK_COMPAT = {"apollo3p": 48, "atomiq110": 48}
CORE_CLOCKED_PROBES = frozenset({"apollo4p", "apollo4l", "apollo510", "apollo510b"})

# Measurement wiring remains an HPX choice; radio reset wiring is a sourced BSP
# exception until the catalog models it (helia-profiler#657).
BOARD_POLICIES = (
    BoardDef(
        name="apollo3p_evb",
        soc="apollo3p",
        channel="stable",
        default_sync_gpio_pin=26,
        default_state_gpio_pin=24,
        default_go_gpio_pin=25,
        starter_profile_board=None,
        description="",
        ble_reset_gpio_pin=None,
    ),
    BoardDef(
        name="apollo3p_evb_cygnus",
        soc="apollo3p",
        channel="preview",
        default_sync_gpio_pin=26,
        default_state_gpio_pin=24,
        default_go_gpio_pin=25,
        starter_profile_board=None,
        description="",
        ble_reset_gpio_pin=None,
    ),
    BoardDef(
        name="apollo4p_evb",
        soc="apollo4p",
        channel="preview",
        default_sync_gpio_pin=22,
        default_state_gpio_pin=23,
        default_go_gpio_pin=24,
        starter_profile_board=None,
        description="",
        ble_reset_gpio_pin=None,
    ),
    BoardDef(
        name="apollo4l_evb",
        soc="apollo4l",
        channel="preview",
        default_sync_gpio_pin=61,
        default_state_gpio_pin=23,
        default_go_gpio_pin=24,
        starter_profile_board=None,
        description="",
        ble_reset_gpio_pin=None,
    ),
    BoardDef(
        name="apollo4l_blue_evb",
        soc="apollo4l",
        channel="preview",
        default_sync_gpio_pin=61,
        default_state_gpio_pin=23,
        default_go_gpio_pin=24,
        starter_profile_board=None,
        description="",
        ble_reset_gpio_pin=55,
    ),
    BoardDef(
        name="apollo4p_blue_kbr_evb",
        soc="apollo4p",
        channel="preview",
        default_sync_gpio_pin=22,
        default_state_gpio_pin=23,
        default_go_gpio_pin=24,
        starter_profile_board=None,
        description="",
        ble_reset_gpio_pin=42,
    ),
    BoardDef(
        name="apollo4p_blue_kxr_evb",
        soc="apollo4p",
        channel="preview",
        default_sync_gpio_pin=22,
        default_state_gpio_pin=23,
        default_go_gpio_pin=24,
        starter_profile_board=None,
        description="",
        ble_reset_gpio_pin=55,
    ),
    BoardDef(
        name="apollo510_evb",
        soc="apollo510",
        channel="stable",
        default_sync_gpio_pin=29,
        default_state_gpio_pin=36,
        default_go_gpio_pin=14,
        starter_profile_board=None,
        description="",
        ble_reset_gpio_pin=None,
    ),
    BoardDef(
        name="apollo510b_evb",
        soc="apollo510b",
        channel="preview",
        default_sync_gpio_pin=29,
        default_state_gpio_pin=36,
        default_go_gpio_pin=14,
        starter_profile_board=None,
        description="",
        ble_reset_gpio_pin=None,
    ),
    BoardDef(
        name="apollo5b_evb",
        soc="apollo5b",
        channel="preview",
        default_sync_gpio_pin=10,
        default_state_gpio_pin=0,
        default_go_gpio_pin=0,
        starter_profile_board=None,
        description="",
        ble_reset_gpio_pin=None,
    ),
    BoardDef(
        name="apollo330mP_evb",
        soc="apollo330P",
        channel="preview",
        default_sync_gpio_pin=10,
        default_state_gpio_pin=0,
        default_go_gpio_pin=0,
        starter_profile_board=None,
        description="Apollo330 — Cortex-M55 (AP5 family)",
        ble_reset_gpio_pin=None,
    ),
    BoardDef(
        name="apollo510dL_evb",
        soc="apollo510L",
        channel="preview",
        default_sync_gpio_pin=10,
        default_state_gpio_pin=0,
        default_go_gpio_pin=0,
        starter_profile_board=None,
        description="Apollo510 Lite — Cortex-M55 (AP5 family)",
        ble_reset_gpio_pin=None,
    ),
    BoardDef(
        name="atomiq110_fpga_turbo",
        soc="atomiq110",
        channel="preview",
        default_sync_gpio_pin=10,
        default_state_gpio_pin=0,
        default_go_gpio_pin=0,
        starter_profile_board=None,
        description="Atomiq110 FPGA turbo — Cortex-M55 + Ethos-U85 NPU (fixed 25 MHz, FPGA-only)",
        ble_reset_gpio_pin=None,
    ),
)
