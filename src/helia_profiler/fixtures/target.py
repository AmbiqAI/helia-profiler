"""Canonical target declaration for the supported fixed-fixture reset path."""

from dataclasses import dataclass

from ..engines import EngineType
from ..errors import ConfigError
from ..placement import Placement
from ..platform import get_soc_for_board
from ..platform.capabilities import resolve_app_flash_load_addr
from ..platform.memory_map import LinkFamily, MemoryRegion, linked_memory_map

#: Default fixture target; existing requests and records keep this identity.
FIXTURE_BOARD = "apollo510_evb"
FIXTURE_BOARDS = (FIXTURE_BOARD, "apollo510b_evb")
#: The only clock profile with a qualified fixture build and capture path.
FIXTURE_CLOCK_PROFILE = "lp"


@dataclass(frozen=True)
class FixtureTarget:
    """The board a fixture image runs on, its J-Link device and the image's load address."""

    board: str
    device: str
    load_address: int

    def verify(self) -> None:
        if type(self.load_address) is not int:
            raise ValueError("Integer fixture load address required")
        if self != fixture_target_for_board(self.board):
            raise ValueError("Unsupported fixture target declaration")


def _fixture_soc(board: str):
    if board not in FIXTURE_BOARDS:
        raise ValueError(f"Unsupported fixture target board: {board}")
    return get_soc_for_board(board)


def supported_fixture_target() -> FixtureTarget:
    return fixture_target_for_board(FIXTURE_BOARD)


def fixture_target_for_board(board: str) -> FixtureTarget:
    soc = _fixture_soc(board)
    origin = resolve_app_flash_load_addr(soc)
    if origin is None:
        raise ValueError("Supported fixture target has no application boot origin")
    # The ELF validator shares one bounded MRAM/DTCM layout across these boards.
    # Refuse canonical metadata drift rather than validating against the wrong bank.
    if board != FIXTURE_BOARD:
        default_origin = resolve_app_flash_load_addr(_fixture_soc(FIXTURE_BOARD))
        if origin != default_origin or any(
            fixture_app_region(region, board) != fixture_app_region(region)
            for region in (MemoryRegion.MRAM, MemoryRegion.DTCM)
        ):
            raise ValueError("Fixture target differs from the supported image/sink layout")
    return FixtureTarget(board, soc.jlink_device, origin)


def fixture_cpu_hz(board: str = FIXTURE_BOARD) -> int:
    """The fixture board's CPU frequency at the fixture clock profile."""
    speed = _fixture_soc(board).cpu_clock.speed(FIXTURE_CLOCK_PROFILE)
    if speed is None:
        raise ValueError(f"{board} has no {FIXTURE_CLOCK_PROFILE!r} clock")
    return speed.mhz * 1_000_000


def fixture_app_region(region: MemoryRegion, board: str = FIXTURE_BOARD) -> tuple[int, int]:
    """``[start, end)`` of the fixture board's linked application extent for ``region``.

    Fixture builds link with ATfE, which uses the GNU linker scripts.
    """
    windows = linked_memory_map(_fixture_soc(board))
    window = next((w for w in windows if w.region is region), None)
    if window is None:
        raise ValueError(f"{board} has no linked {region} region")
    extent = window.app_window[LinkFamily.GNU]
    return extent.start, extent.end


def fixture_engines_for_board(board: str) -> tuple[EngineType, ...]:
    """Engines implemented by the fixture target."""
    _fixture_soc(board)
    engines = (EngineType.HELIA_AOT, EngineType.LITERT)
    return engines if board == "apollo510b_evb" else (*engines, EngineType.HELIA_RT)


def validate_fixture_placement(
    arena: Placement | None,
    weights: Placement | None,
    engine: EngineType | None = None,
    *,
    board: str = FIXTURE_BOARD,
) -> None:
    """Validate the fixture's implemented static arenas and weight backing."""
    fixture_target_for_board(board)
    if board == "apollo510b_evb" and (
        engine not in fixture_engines_for_board(board)
        or weights not in (Placement.MRAM, Placement.TCM)
    ):
        raise ConfigError("AP510B fixtures implement heliaAOT/LiteRT with MRAM or TCM weights")
    if arena not in (Placement.SRAM, Placement.TCM):
        raise ConfigError("Fixture arena placement requires writable static SRAM or DTCM")
    if not isinstance(weights, Placement):
        raise ConfigError("Explicit fixture weight backing required")
    for placement in (arena, weights):
        try:
            start, end = fixture_app_region(placement.region, board)
        except ValueError as exc:
            raise ConfigError("Fixture target has no selected memory bank") from exc
        if start >= end:
            raise ConfigError("Fixture target memory bank has no usable capacity")
