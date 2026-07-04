"""Contract: board resolution is complete from platform metadata alone.

Every registered board must resolve its sync/state/go GPIO pins, its J-Link
device string, and its physical memory ranges purely from the platform
registry — with no board-name special-casing anywhere in the pipeline stages.
Parametrising over ``list_boards()`` means a newly registered board is covered
automatically: if it is missing any of this metadata, the contract fails.

This is the safety net for turning board metadata into first-class capability
objects during the refactor.
"""

from __future__ import annotations

import pathlib

import pytest

from helia_profiler.platform import (
    get_default_go_gpio_pin,
    get_default_state_gpio_pin,
    get_default_sync_gpio_pin,
    get_soc_for_board,
    list_boards,
    soc_placement_ranges,
)
from helia_profiler.placement import Placement

_ALL_BOARDS = list_boards()
_BOARD_IDS = [b.name for b in _ALL_BOARDS]

# A fallback value no board legitimately uses, so we can prove the resolver
# returned board metadata rather than the generic default.
_SENTINEL = -0xBEEF


def test_registry_is_non_empty():
    assert _ALL_BOARDS, "no boards registered — platform registry is empty"


@pytest.mark.parametrize("board", _ALL_BOARDS, ids=_BOARD_IDS)
def test_gpio_pins_resolve_from_board_metadata(board):
    sync = get_default_sync_gpio_pin(board.name, fallback=_SENTINEL)
    state = get_default_state_gpio_pin(board.name, fallback=_SENTINEL)
    go = get_default_go_gpio_pin(board.name, fallback=_SENTINEL)

    # Never the fallback: the value came from the board definition.
    assert sync != _SENTINEL
    assert state != _SENTINEL
    assert go != _SENTINEL

    assert sync == board.default_sync_gpio_pin
    assert state == board.default_state_gpio_pin
    assert go == board.default_go_gpio_pin


@pytest.mark.parametrize("board", _ALL_BOARDS, ids=_BOARD_IDS)
def test_jlink_device_resolves_from_board_metadata(board):
    soc = get_soc_for_board(board.name)
    assert soc.jlink_device, f"board {board.name} has no J-Link device string"


@pytest.mark.parametrize("board", _ALL_BOARDS, ids=_BOARD_IDS)
def test_memory_ranges_resolve_from_board_metadata(board):
    soc = get_soc_for_board(board.name)
    ranges = soc_placement_ranges(soc)

    assert ranges, f"board {board.name} resolved no memory ranges"
    # Weights live in MRAM on every currently-registered target.
    assert Placement.MRAM in ranges
    for placement, mrange in ranges.items():
        assert mrange.length > 0, f"{board.name} {placement} has zero length"
        # Start is a physical base address; MRAM is XIP at 0x0 so allow >= 0.
        assert mrange.start >= 0


@pytest.mark.parametrize("board", _ALL_BOARDS, ids=_BOARD_IDS)
def test_full_resolution_needs_no_board_name_branching(board):
    """A single call path yields every value — no per-board conditionals.

    Resolving the complete GPIO + device + memory bundle for *any* board
    through the generic resolvers proves the data is complete; a stage never
    needs to inspect the board name to fill a gap.
    """
    soc = get_soc_for_board(board.name)
    bundle = {
        "sync": get_default_sync_gpio_pin(board.name, fallback=_SENTINEL),
        "state": get_default_state_gpio_pin(board.name, fallback=_SENTINEL),
        "go": get_default_go_gpio_pin(board.name, fallback=_SENTINEL),
        "jlink_device": soc.jlink_device,
        "ranges": soc_placement_ranges(soc),
    }
    assert _SENTINEL not in (bundle["sync"], bundle["state"], bundle["go"])
    assert bundle["jlink_device"]
    assert bundle["ranges"]


def test_no_stage_compares_board_name_strings():
    """No pipeline stage hard-codes a registered board name for resolution.

    If a stage compared against a board-name literal to pick GPIO pins, a
    device string, or memory ranges, that would defeat metadata-driven
    resolution.  Scan every stage module and assert none mention a registered
    board name.
    """
    stages_dir = pathlib.Path(__file__).resolve().parents[2] / "src" / "helia_profiler" / "stages"
    assert stages_dir.is_dir(), stages_dir
    offenders: list[str] = []
    for stage_file in sorted(stages_dir.glob("*.py")):
        text = stage_file.read_text()
        for name in _BOARD_IDS:
            if name in text:
                offenders.append(f"{stage_file.name}: {name}")
    assert not offenders, f"stage modules reference board-name literals: {offenders}"
