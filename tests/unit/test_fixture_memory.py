from __future__ import annotations

import pytest

from helia_profiler.firmware.fixture_memory import MemorySnapshots, decode_memory_snapshots


def _record(
    *,
    magic: int = 0x4D454D31,
    version: int = 1,
    capacity: int = 4096,
    after_io: int = 1024,
    after_warmup: int = 1024,
    after_invoke: int = 1024,
    valid: int = 1,
    checksum: int | None = None,
) -> bytes:
    words = [magic, version, capacity, after_io, after_warmup, after_invoke, valid]
    if checksum is None:
        check = 0
        for word in words:
            check = (check * 31 + word) & 0xFFFFFFFF
        checksum = check
    words.append(checksum)
    return b"".join(word.to_bytes(4, "little") for word in words)


def test_decode_memory_record() -> None:
    result = decode_memory_snapshots(_record(), expected_capacity_bytes=4096)

    assert result == MemorySnapshots(4096, 1024, 1024, 1024, True, result.checksum)


@pytest.mark.parametrize("data", [b"", bytes(31), bytes(33)])
def test_rejects_truncated_or_extra_record(data: bytes) -> None:
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        decode_memory_snapshots(data, expected_capacity_bytes=4096)


def test_rejects_wrong_version() -> None:
    with pytest.raises(ValueError, match="version"):
        decode_memory_snapshots(_record(version=2), expected_capacity_bytes=4096)


def test_rejects_capacity_mismatch() -> None:
    with pytest.raises(ValueError, match="does not match"):
        decode_memory_snapshots(_record(), expected_capacity_bytes=2048)


def test_rejects_usage_over_capacity() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        decode_memory_snapshots(_record(after_warmup=4097), expected_capacity_bytes=4096)


def test_rejects_nonpositive_usage() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        decode_memory_snapshots(_record(after_io=0), expected_capacity_bytes=4096)


def test_rejects_invalid_flag_and_checksum_corruption() -> None:
    with pytest.raises(ValueError, match="valid flag"):
        decode_memory_snapshots(_record(valid=0), expected_capacity_bytes=4096)
    with pytest.raises(ValueError, match="checksum"):
        decode_memory_snapshots(_record(checksum=123), expected_capacity_bytes=4096)


def test_rejects_invalid_expected_capacity() -> None:
    for capacity in (0, -1, 0x1_0000_0000, True):
        with pytest.raises(ValueError):
            decode_memory_snapshots(_record(), expected_capacity_bytes=capacity)
