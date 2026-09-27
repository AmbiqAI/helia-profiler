"""Canonical target declaration for the supported fixed-fixture reset path."""

from dataclasses import dataclass

from .platform import get_soc_for_board
from .platform.capabilities import resolve_app_flash_load_addr


@dataclass(frozen=True)
class FixtureTarget:
    board: str
    device: str
    load_address: int

    def verify(self) -> None:
        if self != supported_fixture_target():
            raise ValueError("Unsupported fixture target declaration")


def supported_fixture_target() -> FixtureTarget:
    board = "apollo510_evb"
    soc = get_soc_for_board(board)
    origin = resolve_app_flash_load_addr(soc)
    if origin is None:
        raise ValueError("Supported fixture target has no application boot origin")
    return FixtureTarget(board, soc.jlink_device, origin)
