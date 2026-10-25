"""Choice types for options more than one ``hpx`` command declares."""

from __future__ import annotations

from enum import StrEnum

from ..placement import Placement

_ArenaLocation = StrEnum(
    "_ArenaLocation", {p.name: p.value for p in Placement if p != Placement.MRAM}
)


class _CoreOverride(StrEnum):
    CM4 = "cm4"
    CM55 = "cm55"
