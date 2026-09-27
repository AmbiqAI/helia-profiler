"""Decoder for the fixed eight-word firmware memory snapshot record."""

from __future__ import annotations

from dataclasses import dataclass

_MAGIC = 0x4D454D31
_VERSION = 1
_WORD_COUNT = 8
_U32_MAX = 0xFFFFFFFF


@dataclass(frozen=True, slots=True)
class MemorySnapshots:
    """Validated allocator-use observations from the fixture memory sink."""

    capacity_bytes: int
    after_io_bytes: int
    after_warmup_bytes: int
    after_invoke_bytes: int
    valid: bool
    checksum: int


def _checksum(words: tuple[int, ...]) -> int:
    result = 0
    for word in words:
        result = (result * 31 + word) & _U32_MAX
    return result


def decode_memory_snapshots(
    data: bytes | bytearray | memoryview,
    *,
    expected_capacity_bytes: int,
) -> MemorySnapshots:
    """Decode and validate the little-endian 8-word memory record.

    The three phase values report current allocator use. They are not peak or
    minimum measurements.
    """
    if not isinstance(expected_capacity_bytes, int) or isinstance(expected_capacity_bytes, bool):
        raise ValueError("expected capacity must be an integer")
    if expected_capacity_bytes <= 0 or expected_capacity_bytes > _U32_MAX:
        raise ValueError("expected capacity must be positive and fit uint32")
    raw = bytes(data)
    if len(raw) != _WORD_COUNT * 4:
        raise ValueError(f"memory record must be exactly {_WORD_COUNT * 4} bytes")
    words = tuple(
        int.from_bytes(raw[index : index + 4], "little") for index in range(0, len(raw), 4)
    )
    if words[0] != _MAGIC:
        raise ValueError("memory record magic mismatch")
    if words[1] != _VERSION:
        raise ValueError("unsupported memory record version")
    capacity, after_io, after_warmup, after_invoke = words[2:6]
    if capacity == 0:
        raise ValueError("memory record capacity must be positive")
    if capacity != expected_capacity_bytes:
        raise ValueError("memory record capacity does not match expected capacity")
    if any(value == 0 for value in (after_io, after_warmup, after_invoke)):
        raise ValueError("memory usage values must be positive")
    if any(value > capacity for value in (after_io, after_warmup, after_invoke)):
        raise ValueError("memory usage exceeds arena capacity")
    if words[6] != 1:
        raise ValueError("memory record valid flag is not set")
    if words[7] != _checksum(words[:7]):
        raise ValueError("memory record checksum mismatch")
    return MemorySnapshots(
        capacity_bytes=capacity,
        after_io_bytes=after_io,
        after_warmup_bytes=after_warmup,
        after_invoke_bytes=after_invoke,
        valid=True,
        checksum=words[7],
    )
