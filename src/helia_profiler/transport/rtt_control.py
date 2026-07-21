"""Direct RTT control-block discovery and ring-buffer access."""

from __future__ import annotations

import logging

from ..target.probe.base import DebugMemorySession

log = logging.getLogger("hpx")

_RTT_SCAN_CHUNK = 0x4000
_RTT_MAGIC = b"SEGGER RTT"
_RTT_DESC_WORDS = 6
_RTT_CB_HEADER_SIZE = 24
_RTT_DESC_SIZE = _RTT_DESC_WORDS * 4
_RTT_ID_SIZE = 16
_RTT_APP_CHANNEL0_NAME = b"HPX"
_RTT_NAME_MAX_LEN = 16
_RTT_NAME_MATCH_BONUS = 1 << 28
_RTT_ACTIVITY_BONUS = 1 << 26
RTT_LIVE_NAMED_SCORE = _RTT_NAME_MATCH_BONUS + _RTT_ACTIVITY_BONUS


def read_rtt_up_channel0_name(
    jlink: DebugMemorySession,
    block_address: int,
    max_len: int = _RTT_NAME_MAX_LEN,
) -> bytes:
    """Return the NUL-terminated name string of up-channel 0, or ``b""``."""
    try:
        name_ptr = jlink.memory_read32(block_address + _RTT_CB_HEADER_SIZE, 1)[0]
        if name_ptr == 0:
            return b""
        raw = bytes(jlink.memory_read8(name_ptr, max_len))
    except Exception:  # noqa: BLE001 - name probing is best-effort
        return b""
    nul = raw.find(0)
    return raw[:nul] if nul >= 0 else raw


def score_rtt_control_block(jlink: DebugMemorySession, block_address: int) -> int:
    """Rank a control-block candidate by likelihood of being the live HPX block."""
    try:
        max_up_buffers = jlink.memory_read32(block_address + 16, 1)[0]
        if max_up_buffers <= 0:
            return -1
        desc_addr = block_address + _RTT_CB_HEADER_SIZE
        name_ptr, buf_ptr, size, wr_off, rd_off, _flags = jlink.memory_read32(
            desc_addr, _RTT_DESC_WORDS
        )
    except Exception:  # noqa: BLE001 - invalid candidates are ignored
        return -1

    if buf_ptr == 0 or size <= 0 or wr_off > size or rd_off > size:
        return -1

    score = 1 if name_ptr != 0 else 0
    if read_rtt_up_channel0_name(jlink, block_address) == _RTT_APP_CHANNEL0_NAME:
        score += _RTT_NAME_MATCH_BONUS
    if wr_off != rd_off:
        score += _RTT_ACTIVITY_BONUS
    return score + min(size, 1 << 20)


def direct_rtt_read(
    jlink: DebugMemorySession,
    *,
    block_address: int,
    buffer_index: int = 0,
    max_bytes: int = 4096,
) -> bytes:
    """Read directly from an RTT up-buffer via SWD memory accesses."""
    max_up_buffers = jlink.memory_read32(block_address + 16, 1)[0]
    if buffer_index >= max_up_buffers:
        return b""

    desc_addr = _descriptor_address(block_address, buffer_index)
    name_ptr, buf_ptr, size, wr_off, rd_off, _flags = jlink.memory_read32(
        desc_addr, _RTT_DESC_WORDS
    )
    if name_ptr == 0 or buf_ptr == 0 or size == 0 or wr_off == rd_off:
        return b""
    if wr_off > size or rd_off > size:
        return b""

    if wr_off > rd_off:
        count = min(wr_off - rd_off, max_bytes)
        data = bytes(jlink.memory_read8(buf_ptr + rd_off, count))
        new_rd_off = rd_off + count
    else:
        first_count = min(size - rd_off, max_bytes)
        first = bytes(jlink.memory_read8(buf_ptr + rd_off, first_count))
        remain = max_bytes - len(first)
        second = bytes(jlink.memory_read8(buf_ptr, min(wr_off, remain)))
        data = first + second
        new_rd_off = (rd_off + len(data)) % size

    if data:
        jlink.memory_write32(desc_addr + 16, [new_rd_off])
    return data


def direct_rtt_write(
    jlink: DebugMemorySession,
    *,
    block_address: int,
    data: bytes,
    buffer_index: int = 0,
) -> int:
    """Write directly to an RTT down-buffer via SWD memory accesses."""
    if not data:
        return 0

    max_up_buffers = jlink.memory_read32(block_address + 16, 1)[0]
    max_down_buffers = jlink.memory_read32(block_address + 20, 1)[0]
    if buffer_index >= max_down_buffers:
        return 0
    desc_addr = (
        block_address
        + _RTT_CB_HEADER_SIZE
        + (max_up_buffers * _RTT_DESC_SIZE)
        + (buffer_index * _RTT_DESC_SIZE)
    )
    _name_ptr, buf_ptr, size, wr_off, rd_off, _flags = jlink.memory_read32(
        desc_addr, _RTT_DESC_WORDS
    )
    if buf_ptr == 0 or size <= 1 or wr_off > size or rd_off > size:
        return 0

    free = size - (wr_off - rd_off) - 1 if rd_off <= wr_off else rd_off - wr_off - 1
    if free <= 0:
        return 0
    payload = data[:free]
    first_count = min(len(payload), size - wr_off)
    if first_count:
        jlink.memory_write8(buf_ptr + wr_off, list(payload[:first_count]))
    if len(payload) > first_count:
        jlink.memory_write8(buf_ptr, list(payload[first_count:]))
    jlink.memory_write32(desc_addr + 12, [(wr_off + len(payload)) % size])
    return len(payload)


def scan_rtt_control_blocks(
    jlink: DebugMemorySession,
    ranges: tuple[tuple[int, int], ...],
) -> list[tuple[int, int]]:
    """Return valid RTT control blocks as ``(address, score)``, best-first."""
    candidates: list[tuple[int, int]] = []
    seen: set[int] = set()
    for base, length in ranges:
        for offset in range(0, length, _RTT_SCAN_CHUNK):
            chunk_len = min(_RTT_SCAN_CHUNK, length - offset)
            try:
                chunk = bytes(jlink.memory_read8(base + offset, chunk_len))
            except Exception:  # noqa: BLE001 - probe errors are non-fatal
                continue
            start = 0
            while True:
                index = chunk.find(_RTT_MAGIC, start)
                if index < 0:
                    break
                address = base + offset + index
                if address not in seen:
                    seen.add(address)
                    score = score_rtt_control_block(jlink, address)
                    if score >= 0:
                        candidates.append((address, score))
                start = index + 1
    candidates.sort(key=lambda item: item[1], reverse=True)
    return candidates


def scan_for_rtt_control_block(
    jlink: DebugMemorySession,
    ranges: tuple[tuple[int, int], ...],
) -> tuple[int, int] | None:
    """Return the best-scoring RTT control block and score, or ``None``."""
    candidates = scan_rtt_control_blocks(jlink, ranges)
    if not candidates:
        return None
    best_address, best_score = candidates[0]
    if len(candidates) > 1:
        second_address, second_score = candidates[1]
        log.debug(
            "RTT scan found %d control blocks; selected 0x%08X (score=%d) over "
            "0x%08X (score=%d)",
            len(candidates),
            best_address,
            best_score,
            second_address,
            second_score,
        )
    return best_address, best_score


def wipe_rtt_control_blocks(
    jlink: DebugMemorySession,
    ranges: tuple[tuple[int, int], ...],
) -> int:
    """Blank the magic of every structurally valid RTT block in SRAM."""
    zeros = [0] * _RTT_ID_SIZE
    wiped = 0
    seen: set[int] = set()
    for base, length in ranges:
        for offset in range(0, length, _RTT_SCAN_CHUNK):
            chunk_len = min(_RTT_SCAN_CHUNK, length - offset)
            try:
                chunk = bytes(jlink.memory_read8(base + offset, chunk_len))
            except Exception:  # noqa: BLE001 - probe errors are non-fatal
                continue
            start = 0
            while True:
                index = chunk.find(_RTT_MAGIC, start)
                if index < 0:
                    break
                address = base + offset + index
                start = index + 1
                if address in seen:
                    continue
                seen.add(address)
                try:
                    if score_rtt_control_block(jlink, address) < 0:
                        continue
                    jlink.memory_write8(address, zeros)
                    wiped += 1
                    log.debug("pre-clean blanked RTT control block at 0x%08X", address)
                except Exception:  # noqa: BLE001 - a failed wipe is non-fatal
                    pass
    return wiped


def direct_rtt_read_any(
    jlink: DebugMemorySession,
    *,
    ranges: tuple[tuple[int, int], ...],
    preferred_block_address: int | None = None,
    max_bytes: int = 4096,
    allow_rescan: bool = True,
) -> tuple[bytes, int | None]:
    """Read from whichever RTT control block is actually publishing bytes."""
    if preferred_block_address is not None:
        data = direct_rtt_read(
            jlink, block_address=preferred_block_address, max_bytes=max_bytes
        )
        if data:
            return data, preferred_block_address
    if not allow_rescan:
        return b"", preferred_block_address
    for block_address, _score in scan_rtt_control_blocks(jlink, ranges):
        if preferred_block_address is not None and block_address == preferred_block_address:
            continue
        data = direct_rtt_read(jlink, block_address=block_address, max_bytes=max_bytes)
        if data:
            return data, block_address
    return b"", preferred_block_address


def _descriptor_address(block_address: int, buffer_index: int) -> int:
    return block_address + _RTT_CB_HEADER_SIZE + (buffer_index * _RTT_DESC_SIZE)
