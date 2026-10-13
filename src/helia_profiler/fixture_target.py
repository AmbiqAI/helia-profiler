"""Canonical target declaration for the supported fixed-fixture reset path."""

from dataclasses import dataclass

from .engines import EngineType
from .errors import ConfigError
from .placement import Placement
from .platform import get_soc_for_board
from .platform.capabilities import resolve_app_flash_load_addr
from .platform.memory_map import LinkFamily, MemoryRegion, linked_memory_map

#: The only board with a qualified fixture build and capture path.
FIXTURE_BOARD = "apollo510_evb"
#: The only clock profile with a qualified fixture build and capture path.
FIXTURE_CLOCK_PROFILE = "lp"


@dataclass(frozen=True)
class FixtureTarget:
    board: str
    device: str
    load_address: int

    def verify(self) -> None:
        if self != supported_fixture_target():
            raise ValueError("Unsupported fixture target declaration")


def supported_fixture_target() -> FixtureTarget:
    soc = get_soc_for_board(FIXTURE_BOARD)
    origin = resolve_app_flash_load_addr(soc)
    if origin is None:
        raise ValueError("Supported fixture target has no application boot origin")
    return FixtureTarget(FIXTURE_BOARD, soc.jlink_device, origin)


def fixture_cpu_hz() -> int:
    """The fixture board's CPU frequency at the fixture clock profile."""
    speed = get_soc_for_board(FIXTURE_BOARD).cpu_clock.speed(FIXTURE_CLOCK_PROFILE)
    if speed is None:
        raise ValueError(f"{FIXTURE_BOARD} has no {FIXTURE_CLOCK_PROFILE!r} clock")
    return speed.mhz * 1_000_000


def fixture_app_region(region: MemoryRegion) -> tuple[int, int]:
    """``[start, end)`` of the fixture board's linked application extent for ``region``.

    Fixture builds link with ATfE, which uses the GNU linker scripts.
    """
    windows = linked_memory_map(get_soc_for_board(FIXTURE_BOARD))
    window = next((w for w in windows if w.region is region), None)
    if window is None:
        raise ValueError(f"{FIXTURE_BOARD} has no linked {region} region")
    extent = window.app_window[LinkFamily.GNU]
    return extent.start, extent.end


def validate_fixture_placement(
    arena: Placement | None, weights: Placement | None, engine: EngineType | None = None
) -> None:
    """Validate the fixture's implemented static arenas and weight backing."""
    if arena not in (Placement.SRAM, Placement.TCM):
        raise ConfigError("Fixture arena placement requires writable static SRAM or DTCM")
    if not isinstance(weights, Placement):
        raise ConfigError("Explicit fixture weight backing required")
    for placement in (arena, weights):
        try:
            start, end = fixture_app_region(placement.region)
        except ValueError as exc:
            raise ConfigError("Fixture target has no selected memory bank") from exc
        if start >= end:
            raise ConfigError("Fixture target memory bank has no usable capacity")
    if weights is Placement.PSRAM:
        if engine not in (EngineType.HELIA_AOT, EngineType.TFLM):
            raise ConfigError("Self-loading PSRAM fixtures implement helia-AOT or upstream TFLM")
