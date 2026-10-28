"""Image-bound expectations and bounded PSRAM weight placement evidence."""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

from ..platform.memory_map import MemoryRegion
from .image import require
from .target import fixture_app_region

PSRAM_MAGIC = 0x50535231
MAX_PSRAM_PAYLOADS = 16


def payload_checksum(data: bytes) -> int:
    """The firmware's byte checksum, separate from artifact SHA256 identity."""
    value = 0
    for byte in data:
        value = (value * 31 + byte) & 0xFFFFFFFF
    return value


@dataclass(frozen=True)
class FixturePsramPayload:
    region_id: int
    size: int
    checksum: int

    def __post_init__(self) -> None:
        if not (
            type(self.region_id) is int
            and 0 <= self.region_id < 256
            and type(self.size) is int
            and 0 < self.size <= 64 * 1024 * 1024
            and type(self.checksum) is int
            and 0 <= self.checksum < 2**32
        ):
            raise ValueError("Invalid PSRAM payload expectation")


@dataclass(frozen=True)
class FixturePsramWeights:
    """Expected constant payloads; an uncompiled build has no image binding."""

    payloads: tuple[FixturePsramPayload, ...]
    image_sha256: str | None = None
    clock_hz: int = 96_000_000

    def __post_init__(self) -> None:
        if not (
            isinstance(self.payloads, tuple)
            and 0 < len(self.payloads) <= MAX_PSRAM_PAYLOADS
            and all(isinstance(p, FixturePsramPayload) for p in self.payloads)
            and self.clock_hz in (48_000_000, 96_000_000, 125_000_000, 192_000_000, 250_000_000)
            and type(self.clock_hz) is int
            and len({p.region_id for p in self.payloads}) == len(self.payloads)
            and (
                self.image_sha256 is None
                or isinstance(self.image_sha256, str)
                and re.fullmatch(r"[0-9a-f]{64}", self.image_sha256)
            )
        ):
            raise ValueError("Invalid PSRAM weight expectation")

    @property
    def sink_size(self) -> int:
        return 4 * (8 + 4 * len(self.payloads))


@dataclass(frozen=True)
class FixturePsramObservation:
    base_address: int
    size_bytes: int
    clock_hz: int
    #: Active consumer addresses, in the expectation's payload order.
    active_addresses: tuple[int, ...]


def decode_psram_weights(data: bytes, expected: FixturePsramWeights) -> FixturePsramObservation:
    require(len(data) == expected.sink_size, "Invalid PSRAM terminal size")
    words = struct.unpack(f"<{len(data) // 4}I", data)
    magic, version, status, base, size, clock, xip, count = words[:8]
    known_base, _ = fixture_app_region(MemoryRegion.PSRAM)
    require(
        (magic, version, status, xip, count) == (PSRAM_MAGIC, 1, 0, 1, len(expected.payloads))
        and base == known_base
        and 0 < size <= 64 * 1024 * 1024
        and base + size <= 2**32
        and clock == expected.clock_hz,
        "Invalid ready/XIP PSRAM terminal",
    )
    addresses = []
    spans = []
    for i, payload in enumerate(expected.payloads):
        region, address, length, checksum = words[8 + 4 * i : 12 + 4 * i]
        require(
            (region, length, checksum) == (payload.region_id, payload.size, payload.checksum)
            and address % 64 == 0
            and base <= address < address + length <= base + size,
            "PSRAM active payload differs from bound expectation",
        )
        addresses.append(address)
        spans.append((address, address + length))
    spans.sort()
    require(all(a[1] <= b[0] for a, b in zip(spans, spans[1:])), "Overlapping PSRAM payloads")
    return FixturePsramObservation(base, size, clock, tuple(addresses))
