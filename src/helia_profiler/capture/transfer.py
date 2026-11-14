"""Parse transfer lines without mixing separate runs or PMU passes."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TypeVar

from ..errors import CaptureError
from ..results.transfer import (
    TransferActivity,
    TransferEvidence,
    TransferOverlap,
    TransferRefills,
    TransferStats,
)
from ..wire import WireKey

_KEYS = (WireKey.TRANSFER_STATS, WireKey.TRANSFER_OVERLAP, WireKey.TRANSFER_REFILLS)
_KEY_PATTERN = re.compile(r"^(HPX_TRANSFER_(?:STATS|OVERLAP|REFILLS))(?=\W|$)")
_T = TypeVar("_T")


def _uint(value: str) -> int:
    if not re.fullmatch(r"[0-9]+", value) or len(value) > 10:
        raise ValueError("expected a uint32 decimal")
    number = int(value)
    if number > 0xFFFFFFFF:
        raise ValueError("value exceeds uint32")
    return number


def _activity(value: str) -> TransferActivity:
    if not re.fullmatch(r"[01x]/[01]", value):
        raise ValueError("expected DMA/pending flags")
    return TransferActivity(None if value[0] == "x" else value[0] == "1", value[2] == "1")


def _indexed(value: str, parse: Callable[[str], _T]) -> dict[int, _T]:
    result: dict[int, _T] = {}
    if value:
        for entry in value.split(","):
            index, item = entry.split(":")
            key = _uint(index)
            if key in result:
                raise ValueError("duplicate index")
            result[key] = parse(item)
    return result


class TransferEvidenceParser:
    """Mutable capture assembly; only frozen records leave this parser."""

    def __init__(self) -> None:
        self.records: list[TransferEvidence] = []
        self._preset: str | None = None
        self._iteration: int | None = None
        self._stats: TransferStats | None = None
        self._overlap: TransferOverlap | None = None
        self._refills: TransferRefills | None = None
        self._seen: set[tuple[str, int]] = set()
        self._last_line: str | None = None

    def start_preset(self, name: str) -> None:
        self.flush()
        self._preset = name
        self._iteration = None

    def start_iteration(self, index: int) -> None:
        self.flush()
        if self._preset is None:
            self._preset = "_default"
        self._iteration = index

    def flush(self) -> None:
        if self._stats is not None or self._overlap is not None or self._refills is not None:
            assert self._preset is not None and self._iteration is not None
            identity = (self._preset, self._iteration)
            if identity in self._seen:
                raise CaptureError(
                    f"Duplicate transfer evidence for pass/iteration {identity!r}: {self._last_line!r}."
                )
            self._seen.add(identity)
            self.records.append(
                TransferEvidence(*identity, self._stats, self._overlap, self._refills)
            )
        self._stats = None
        self._overlap = None
        self._refills = None
        self._last_line = None

    def feed_line(self, line: str) -> bool:
        match = _KEY_PATTERN.match(line)
        key = next((key for key in _KEYS if match and match.group(1) == key.wire), None)
        if key is None:
            return False
        try:
            suffix = line[len(key.wire) :]
            if not suffix.startswith("="):
                raise ValueError("missing equals sign")
            value = suffix[1:]
            if self._iteration is None:
                raise ValueError("outside an instrumented iteration")
            if key == WireKey.TRANSFER_STATS:
                if self._stats is not None:
                    raise ValueError("duplicate stats line")
                fields = value.split(",")
                if len(fields) != 6:
                    raise ValueError("expected six stats fields")
                self._stats = TransferStats(*(_uint(field) for field in fields))
            elif key == WireKey.TRANSFER_OVERLAP:
                if self._overlap is not None:
                    raise ValueError("duplicate overlap line")
                units, operators = value.split(";")
                self._overlap = TransferOverlap(
                    _indexed(units, _activity), _indexed(operators, _activity)
                )
            else:
                if self._refills is not None:
                    raise ValueError("duplicate refills line")
                units, operators = value.split(";")
                self._refills = TransferRefills(_indexed(units, _uint), _indexed(operators, _uint))
        except ValueError as exc:
            raise CaptureError(f"Invalid transfer evidence line {line!r}: {exc}.") from exc
        self._last_line = line
        return True
