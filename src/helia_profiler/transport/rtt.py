"""RTT capture transport — reads HPX output via SEGGER Real-Time Transfer.

RTT uses an in-SRAM ring buffer that the J-Link reads via background SWD
memory accesses — **zero CPU interrupts, zero PMU contamination**.

The firmware uses a compile-time sized up-buffer. It keeps RTT non-blocking
during boot and timed inference so diagnostic writes cannot stall the CPU and
contaminate PMU measurements. It switches to blocking mode outside the timed
window for CSV dumps and the final ``HPX_END`` sentinel so profiling rows are
not silently dropped. After output bursts, the firmware calls
``SCB_CleanDCache()`` so the J-Link host can read the data via SWD (which
bypasses the CPU D-cache).

When ``weights_region="psram"``, the firmware initialises PSRAM and emits
``HPX_PSRAM_READY=<addr>,<size>`` before waiting.  The host writes the
model flatbuffer to the PSRAM XIP address via ``jlink.memory_write()`` and
sends ``HPX_GO`` on the RTT down-channel to resume inference.

For ordinary RTT runs, the firmware emits ``HPX_READY`` and then writes the
``HPX_START`` header in lossless (wait-for-space) mode: it blocks until the
host drains the up-buffer, so the protocol sentinels are never lost no matter
when the host attaches.  The host therefore only waits for ``HPX_READY`` as a
liveness signal and does **not** reply on the down-channel.  (The old
``HPX_HOST_READY`` down-channel handshake was the fragile path — stale D-cache
on the target's down-buffer descriptor — that repeatedly regressed.)

Sequence:
  1. Reset the target via SEGGER commander (handles Apollo510 SBL correctly).
  2. Connect pylink and start RTT — locate the RTT control block.
  3. (PSRAM) Wait for HPX_PSRAM_READY, upload model, send HPX_GO.
  4. Collect lines until ``--- HPX_END ---`` or timeout.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from ..config import Transport
from ..errors import CaptureError
from .base import BaseCaptureTransport, CaptureArgs
from ..target.probe.base import DebugMemorySession, ResetController
from ..target.probe.jlink import (
    JLinkResetController,
    create_debug_memory_session,
    is_jlink_exception,
    is_jlink_rtt_exception,
    open_jlink_with_retry,
    resume_if_halted,
)
from .protocol import (
    DEFAULT_TIMEOUT_S,
    HEARTBEAT_TIMEOUT_S,
    HPX_END,
    HPX_START,
    collect_lines,
)
from .timing import SBL_SETTLE_S

log = logging.getLogger("hpx")

_RTT_CB_TIMEOUT_S = 30  # max time to wait for RTT control block discovery
_RTT_PRECLEAN_TIMEOUT_S = 10  # bound the pre-reset connect used to wipe stale blocks
_PSRAM_READY_TIMEOUT_S = 15  # wait for PSRAM init + ready signal
_PSRAM_WRITE_CHUNK = 65536  # bytes per J-Link memory_write call
_RTT_READY_TIMEOUT_S = 15  # wait for firmware/host RTT startup handshake

_RTT_SCAN_CHUNK = 0x4000  # 16 KB per memory_read call
_RTT_MAGIC = b"SEGGER RTT"
_RTT_DESC_WORDS = 6
_RTT_CB_HEADER_SIZE = 24
_RTT_DESC_SIZE = _RTT_DESC_WORDS * 4
_RTT_ID_SIZE = 16  # bytes of the acID magic field at the start of a control block
_RTT_DISCOVERY_SETTLE_S = 2.0

# The firmware always names up-channel 0 "HPX" (see firmware/templates/
# main*.cc.j2: SEGGER_RTT_ConfigUpBuffer(0, "HPX", ...)).  This is the
# strongest, board-agnostic way to tell *our* RTT control block apart from
# unrelated ones (e.g. a bootloader/monitor's default "Terminal" block, or a
# stale block left in retained SRAM by a previous firmware).
_RTT_APP_CHANNEL0_NAME = b"HPX"
_RTT_NAME_MAX_LEN = 16
_RTT_READY_LINE = "HPX_READY"

# Scoring weights.  These are powers of two chosen so the qualitative signals
# dominate in priority order (name match > live activity > buffer size) while
# still letting buffer size act as a final tiebreaker.
_RTT_NAME_MATCH_BONUS = 1 << 28
_RTT_ACTIVITY_BONUS = 1 << 26


def _read_rtt_up_channel0_name(
    jlink: DebugMemorySession,
    block_address: int,
    max_len: int = _RTT_NAME_MAX_LEN,
) -> bytes:
    """Return the NUL-terminated name string of up-channel 0, or ``b""``.

    The name lives behind the ``sName`` pointer in the channel-0 buffer
    descriptor.  Any probe error degrades gracefully to an empty name so the
    caller simply falls back to the other (size/activity) signals.
    """
    try:
        name_ptr = jlink.memory_read32(block_address + _RTT_CB_HEADER_SIZE, 1)[0]
        if name_ptr == 0:
            return b""
        raw = bytes(jlink.memory_read8(name_ptr, max_len))
    except Exception:  # noqa: BLE001 — name probing is best-effort
        return b""
    nul = raw.find(0)
    return raw[:nul] if nul >= 0 else raw


def _score_rtt_control_block(jlink: DebugMemorySession, block_address: int) -> int:
    """Rank an RTT control-block candidate by how likely it is *our* live block.

    A raw "SEGGER RTT" magic match is too weak on its own: Apollo5 parts retain
    SRAM across reset/reflash, so a structurally-valid but stale control block
    from a previous firmware can survive alongside the current one.  This ranks
    candidates by, in order: (1) up-channel 0 named "HPX", (2) the block has
    unread data (it is actively producing output, not a drained leftover),
    (3) larger plausible buffer.  Returns -1 for unusable candidates.
    """
    try:
        max_up_buffers = jlink.memory_read32(block_address + 16, 1)[0]
        if max_up_buffers <= 0:
            return -1
        desc_addr = block_address + _RTT_CB_HEADER_SIZE
        name_ptr, buf_ptr, size, wr_off, rd_off, _flags = jlink.memory_read32(desc_addr, _RTT_DESC_WORDS)
    except Exception:  # noqa: BLE001 — invalid candidates are ignored
        return -1

    # Structural validity: a usable up-channel 0 must point at a real buffer
    # with sane read/write offsets.  Reject clearly-broken candidates outright.
    if buf_ptr == 0 or size <= 0:
        return -1
    if wr_off > size or rd_off > size:
        return -1

    score = 0
    if name_ptr != 0:
        score += 1

    # Strongest signal: the firmware names up-channel 0 "HPX".  This filters out
    # unrelated RTT blocks regardless of SoC (Apollo510, Apollo510B, ...).
    if _read_rtt_up_channel0_name(jlink, block_address) == _RTT_APP_CHANNEL0_NAME:
        score += _RTT_NAME_MATCH_BONUS

    # Next strongest: a block with unread data (wr_off != rd_off) is live.  A
    # drained stale block left in retained SRAM has wr_off == rd_off and so
    # scores lower than the block actively emitting HPX output.
    if wr_off != rd_off:
        score += _RTT_ACTIVITY_BONUS

    # Final tiebreaker: prefer the larger plausible buffer.
    score += min(size, 1 << 20)
    return score


def _direct_rtt_read(
    jlink: DebugMemorySession,
    *,
    block_address: int,
    buffer_index: int = 0,
    max_bytes: int = 4096,
) -> bytes:
    """Read directly from an RTT up-buffer via SWD memory accesses.

    Some Apollo5 setups expose a valid RTT control block in RAM but SEGGER's
    RTT control API never transitions to a discovered state. This helper polls
    the control block and advances ``RdOff`` manually so host capture can still
    proceed.
    """
    max_up_buffers = jlink.memory_read32(block_address + 16, 1)[0]
    if buffer_index >= max_up_buffers:
        return b""

    desc_addr = block_address + _RTT_CB_HEADER_SIZE + (buffer_index * _RTT_DESC_SIZE)
    name_ptr, buf_ptr, size, wr_off, rd_off, _flags = jlink.memory_read32(desc_addr, _RTT_DESC_WORDS)
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


def _direct_rtt_write(
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
        desc_addr,
        _RTT_DESC_WORDS,
    )
    if buf_ptr == 0 or size <= 1:
        return 0
    if wr_off > size or rd_off > size:
        return 0

    if rd_off <= wr_off:
        free = size - (wr_off - rd_off) - 1
    else:
        free = rd_off - wr_off - 1
    if free <= 0:
        return 0

    payload = data[:free]
    first_count = min(len(payload), size - wr_off)
    if first_count:
        jlink.memory_write8(buf_ptr + wr_off, list(payload[:first_count]))
    second_count = len(payload) - first_count
    if second_count:
        jlink.memory_write8(buf_ptr, list(payload[first_count:]))
    new_wr_off = (wr_off + len(payload)) % size
    jlink.memory_write32(desc_addr + 12, [new_wr_off])
    return len(payload)


def _scan_rtt_control_blocks(
    jlink: DebugMemorySession,
    ranges: tuple[tuple[int, int], ...],
) -> list[tuple[int, int]]:
    """Return every valid RTT control block as ``(address, score)``, best-first.

    Single pass over the SRAM ranges: each "SEGGER RTT" magic match is scored
    once and de-duplicated, then the survivors are sorted by score.  This is a
    deterministic fallback for J-Link's built-in RTT auto-scan, which on some
    devices fails to cover the relevant SRAM region.
    """
    candidates: list[tuple[int, int]] = []
    seen: set[int] = set()

    for base, length in ranges:
        for offset in range(0, length, _RTT_SCAN_CHUNK):
            chunk_len = min(_RTT_SCAN_CHUNK, length - offset)
            try:
                chunk = bytes(jlink.memory_read8(base + offset, chunk_len))
            except Exception:  # noqa: BLE001 — probe errors are non-fatal
                continue
            start = 0
            while True:
                idx = chunk.find(_RTT_MAGIC, start)
                if idx < 0:
                    break
                block_address = base + offset + idx
                if block_address not in seen:
                    seen.add(block_address)
                    score = _score_rtt_control_block(jlink, block_address)
                    if score >= 0:
                        candidates.append((block_address, score))
                start = idx + 1

    candidates.sort(key=lambda item: item[1], reverse=True)
    return candidates


def _scan_for_rtt_control_block(
    jlink: DebugMemorySession,
    ranges: tuple[tuple[int, int], ...],
) -> tuple[int, int] | None:
    """Return the best-scoring RTT control block and its score, or None.

    Thin wrapper over :func:`_scan_rtt_control_blocks` for callers that only
    need the single best candidate.
    """
    candidates = _scan_rtt_control_blocks(jlink, ranges)
    if not candidates:
        return None
    best_addr, best_score = candidates[0]
    if len(candidates) > 1:
        second_addr, second_score = candidates[1]
        log.debug(
            "RTT scan found %d control blocks; selected 0x%08X (score=%d) over 0x%08X (score=%d)",
            len(candidates),
            best_addr,
            best_score,
            second_addr,
            second_score,
        )
    return best_addr, best_score


def _wipe_rtt_control_blocks(
    jlink: DebugMemorySession,
    ranges: tuple[tuple[int, int], ...],
) -> int:
    """Blank the "SEGGER RTT" magic of every control block found in SRAM.

    Apollo5 parts retain SRAM across reset/reflash, so a control block written
    by a *previously* flashed firmware (often a different buffer size, at a
    different link address) can survive and race the live block during
    discovery.  Zeroing the leading acID field makes those blocks invisible to
    both the J-Link auto-scan and our own scan.

    This is intended as a pre-reset clean: the currently flashed firmware
    re-establishes its single control block on the next reset, while the stale
    locations stay dead because no running code ever rewrites them.

    Returns the number of control blocks blanked.
    """
    zeros = [0] * _RTT_ID_SIZE
    wiped = 0
    seen: set[int] = set()
    for base, length in ranges:
        for offset in range(0, length, _RTT_SCAN_CHUNK):
            chunk_len = min(_RTT_SCAN_CHUNK, length - offset)
            try:
                chunk = bytes(jlink.memory_read8(base + offset, chunk_len))
            except Exception:  # noqa: BLE001 — probe errors are non-fatal
                continue
            start = 0
            while True:
                idx = chunk.find(_RTT_MAGIC, start)
                if idx < 0:
                    break
                addr = base + offset + idx
                if addr in seen:
                    start = idx + 1
                    continue
                seen.add(addr)
                try:
                    if _score_rtt_control_block(jlink, addr) < 0:
                        start = idx + 1
                        continue
                    jlink.memory_write8(addr, zeros)
                    wiped += 1
                    log.debug("pre-clean blanked RTT control block at 0x%08X", addr)
                except Exception:  # noqa: BLE001 — a failed wipe is non-fatal
                    pass
                start = idx + 1
    return wiped


def _direct_rtt_read_any(
    jlink: DebugMemorySession,
    *,
    ranges: tuple[tuple[int, int], ...],
    preferred_block_address: int | None = None,
    max_bytes: int = 4096,
    allow_rescan: bool = True,
) -> tuple[bytes, int | None]:
    """Read from whichever RTT control block is actually publishing bytes."""
    if preferred_block_address is not None:
        data = _direct_rtt_read(jlink, block_address=preferred_block_address, max_bytes=max_bytes)
        if data:
            return data, preferred_block_address

    if not allow_rescan:
        return b"", preferred_block_address

    for block_address, _score in _scan_rtt_control_blocks(jlink, ranges):
        if preferred_block_address is not None and block_address == preferred_block_address:
            continue
        data = _direct_rtt_read(jlink, block_address=block_address, max_bytes=max_bytes)
        if data:
            return data, block_address
    return b"", preferred_block_address


def _wait_for_rtt_line(
    read_chunk,
    *,
    expected_line: str,
    timeout_s: float,
) -> bytes:
    """Read RTT text until *expected_line* is observed.

    Returns every byte consumed while waiting so the caller can feed the same
    stream into the next protocol stage without losing any early lines.
    """
    buf = b""
    captured = b""
    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:
        chunk = read_chunk()
        if chunk:
            buf += chunk
            while b"\n" in buf:
                raw_line, buf = buf.split(b"\n", 1)
                captured += raw_line + b"\n"
                line = raw_line.decode("ascii", errors="replace").strip()
                if not line:
                    continue
                if line == expected_line:
                    return captured + buf
                if line.startswith("HPX_ERROR="):
                    raise CaptureError(
                        f"Firmware reported RTT startup error: {line}",
                        hint="Check target boot logs and RTT handshake state.",
                    )
        else:
            time.sleep(0.005)

    raise CaptureError(
        f"Timed out waiting for {expected_line} from firmware",
        hint="The target did not complete the RTT startup handshake.",
    )


def _write_rtt_command_direct(
    jlink: DebugMemorySession,
    *,
    block_address: int,
    command: bytes,
    timeout_s: float = _RTT_READY_TIMEOUT_S,
) -> None:
    written = 0
    deadline = time.monotonic() + timeout_s

    while written < len(command) and time.monotonic() < deadline:
        advanced = _direct_rtt_write(
            jlink,
            block_address=block_address,
            data=command[written:],
        )
        if advanced > 0:
            written += advanced
            continue
        time.sleep(0.005)

    if written < len(command):
        raise CaptureError(
            "Timed out sending RTT host-ready command",
            hint="The firmware did not expose a writable RTT down-buffer in time.",
        )


def _write_rtt_command_api(
    jlink: DebugMemorySession,
    *,
    command: bytes,
    timeout_s: float = _RTT_READY_TIMEOUT_S,
) -> None:
    written = 0
    deadline = time.monotonic() + timeout_s

    while written < len(command) and time.monotonic() < deadline:
        advanced_raw = jlink.rtt_write(0, list(command[written:]))
        advanced = len(command) - written if advanced_raw is None else int(advanced_raw)
        if advanced > 0:
            written += advanced
            continue
        time.sleep(0.005)

    if written < len(command):
        raise CaptureError(
            "Timed out sending RTT host-ready command",
            hint="The firmware did not expose a writable RTT down-buffer in time.",
        )


def _perform_rtt_ready_handshake(
    *,
    read_chunk,
) -> bytes:
    # Wait for the firmware's HPX_READY liveness line.  We do NOT reply on the
    # RTT down-channel: the firmware emits HPX_READY and the entire HPX_START
    # header in lossless (wait-for-space) mode, so it blocks until we drain the
    # up-buffer and nothing is lost regardless of attach timing.  The old
    # HPX_HOST_READY down-channel reply was the fragile path (stale D-cache on
    # the target's down-buffer descriptor) that repeatedly regressed.
    return _wait_for_rtt_line(
        read_chunk,
        expected_line=_RTT_READY_LINE,
        timeout_s=_RTT_READY_TIMEOUT_S,
    )


def _prepend_pending_bytes(read_chunk, pending: bytes):
    """Return a read function that yields *pending* before *read_chunk*()."""
    pending_buf = pending

    def _read() -> bytes:
        nonlocal pending_buf
        if pending_buf:
            chunk = pending_buf
            pending_buf = b""
            return chunk
        return read_chunk()

    return _read


def _upload_model_to_psram(
    jlink: DebugMemorySession,
    model_path: Path,
    timeout_s: float = _PSRAM_READY_TIMEOUT_S,
    initial_buf: bytes = b"",
) -> None:
    """Wait for HPX_PSRAM_READY, upload model via J-Link, send HPX_GO.

    The firmware emits ``HPX_PSRAM_READY=0x60000000,<size>\\n`` once PSRAM
    is initialised and XIP is enabled.  We read RTT until we see that line,
    then write the model flatbuffer directly to the PSRAM XIP address via
    SWD memory writes, and finally send ``HPX_GO`` on RTT down-channel 0
    so the firmware proceeds with inference.
    """
    buf = initial_buf
    deadline = time.monotonic() + timeout_s
    psram_addr: int | None = None
    expected_size: int | None = None

    # --- Read RTT until HPX_PSRAM_READY ---
    while time.monotonic() < deadline:
        chunk = bytes(jlink.rtt_read(0, 4096))
        if chunk:
            buf += chunk
            text = buf.decode("ascii", errors="replace")
            m = re.search(r"HPX_PSRAM_READY=0x([0-9a-fA-F]+),(\d+)", text)
            if m:
                psram_addr = int(m.group(1), 16)
                expected_size = int(m.group(2))
                break
            # Check for init errors
            if "HPX_ERROR=" in text:
                raise CaptureError(
                    f"Firmware error during PSRAM init: {text.strip()}",
                    hint="Check that the board has PSRAM and it is connected.",
                )
        time.sleep(0.01)

    if psram_addr is None:
        raise CaptureError(
            "Timed out waiting for HPX_PSRAM_READY from firmware",
            hint=(
                "Firmware did not signal PSRAM readiness. "
                "Ensure the board has PSRAM and --model-location psram is correct."
            ),
        )

    log.info("PSRAM ready at 0x%08X, uploading model (%d bytes)", psram_addr, expected_size)

    # --- Write model data to PSRAM via J-Link SWD ---
    model_data = model_path.read_bytes()
    if len(model_data) != expected_size:
        log.warning(
            "Model size mismatch: file=%d, firmware expects=%d",
            len(model_data),
            expected_size,
        )

    total = len(model_data)
    written = 0
    t0 = time.monotonic()
    while written < total:
        end = min(written + _PSRAM_WRITE_CHUNK, total)
        chunk_data = list(model_data[written:end])
        jlink.memory_write(psram_addr + written, chunk_data, nbits=8)
        written = end
        if log.isEnabledFor(logging.DEBUG):
            log.debug("PSRAM write: %d / %d bytes", written, total)

    elapsed = time.monotonic() - t0
    rate_kbps = (total / 1024) / elapsed if elapsed > 0 else 0
    log.info("Model uploaded to PSRAM in %.1fs (%.0f KB/s)", elapsed, rate_kbps)

    # --- Send HPX_GO to resume firmware ---
    _write_rtt_command_api(jlink, command=b"HPX_GO")
    log.info("Sent HPX_GO — firmware resuming")


def capture_rtt_output(
    *,
    jlink_serial: str | None = None,
    jlink_device: str,
    rtt_scan_ranges: tuple[tuple[int, int], ...],
    known_block_address: int | None = None,
    timeout_s: float | None = None,
    heartbeat_timeout_s: float = HEARTBEAT_TIMEOUT_S,
    model_path: Path | None = None,
    weights_region: str = "mram",
    timing_out: dict[str, float] | None = None,
    reset_controller: ResetController | None = None,
) -> list[str]:
    """Capture firmware output via SEGGER RTT until HPX_END or hang detection.

    When *weights_region* is ``"psram"``, the function uploads the model
    flatbuffer to PSRAM via J-Link SWD writes before collecting profiling
    output.

    Args:
        known_block_address: Linked address of the RTT control block recovered
            from build artifacts.  When provided, capture attaches directly to
            this address and skips both the stale-block pre-clean and the
            host-side discovery scan.  If the firmware never publishes bytes
            there, capture transparently falls back to scanning.
        timeout_s: Overall wall-clock ceiling.  ``None`` = rely on
            heartbeats (recommended for long inferences).
        heartbeat_timeout_s: Maximum gap between any firmware lines before
            the run is declared hung.

    Returns:
        List of captured text lines.
    """
    capture_started_s = time.monotonic()
    hpx_start_s: float | None = None
    hpx_end_s: float | None = None

    def record_phase_duration(name: str, started_s: float, *, detail: str = "") -> float:
        elapsed_s = time.monotonic() - started_s
        if timing_out is not None:
            timing_out[f"rtt_phase_{name}_s"] = elapsed_s
        suffix = f" ({detail})" if detail else ""
        log.info("RTT phase %s completed in %.3fs%s", name, elapsed_s, suffix)
        return elapsed_s

    def on_line(line: str, line_ts: float) -> None:
        nonlocal hpx_start_s, hpx_end_s
        if line == HPX_START and hpx_start_s is None:
            hpx_start_s = line_ts
            log.info(
                "RTT observed HPX_START %.3fs after capture start",
                hpx_start_s - capture_started_s,
            )
        elif line == HPX_END:
            hpx_end_s = line_ts
            if hpx_start_s is not None:
                log.info(
                    "RTT observed HPX_END %.3fs after HPX_START",
                    hpx_end_s - hpx_start_s,
                )

    def finalize_timing() -> None:
        if timing_out is None:
            return
        timing_out["capture_duration_s"] = time.monotonic() - capture_started_s
        if hpx_start_s is not None:
            timing_out["hpx_start_latency_s"] = hpx_start_s - capture_started_s
        if hpx_start_s is not None and hpx_end_s is not None:
            timing_out["protocol_duration_s"] = hpx_end_s - hpx_start_s

    controller = reset_controller or JLinkResetController()
    jlink = create_debug_memory_session()

    try:
        # --- Phase 0: pre-clean stale RTT control blocks ---
        # Apollo5 retains SRAM across reset, so a control block from a
        # previously flashed firmware can linger and race the live one during
        # discovery.  Connect to the still-running target, blank every
        # "SEGGER RTT" magic, and release the probe; the reset below then lets
        # the current firmware come up as the only block in SRAM.  Best-effort:
        # if the pre-clean attach fails we fall through to reset and rely on
        # the discovery scoring to pick the live block.
        #
        # When the control block address is known up-front, discovery targets a
        # single fixed address that the firmware re-initialises on every boot,
        # so stale blocks elsewhere are irrelevant and the pre-clean is skipped.
        preclean_ok = False
        if known_block_address is None:
            preclean_started_s = time.monotonic()
            try:
                open_jlink_with_retry(
                    jlink,
                    device=jlink_device,
                    jlink_serial=jlink_serial,
                    timeout_s=_RTT_PRECLEAN_TIMEOUT_S,
                )
                try:
                    jlink.halt()
                except Exception:  # noqa: BLE001 — halt is best-effort
                    pass
                wiped = _wipe_rtt_control_blocks(jlink, rtt_scan_ranges)
                preclean_ok = True
                if wiped:
                    log.info("pre-clean blanked %d stale RTT control block(s)", wiped)
            except CaptureError:
                log.debug("pre-clean RTT attach failed; continuing without wipe", exc_info=True)
            finally:
                record_phase_duration(
                    "preclean",
                    preclean_started_s,
                    detail="attached" if preclean_ok else "attach_failed",
                )
                try:
                    jlink.close()
                except Exception:  # noqa: BLE001 — close errors are non-fatal
                    pass

        # --- Step 1: reset the target via SEGGER commander subprocess ---
        # SEGGER commander handles the Apollo510 secure bootloader (SBL) correctly;
        # pylink's reset() does not trigger the vendor-specific handler.
        reset_started_s = time.monotonic()
        controller.debug_reset(device=jlink_device, jlink_serial=jlink_serial)
        record_phase_duration("reset", reset_started_s)

        # --- Step 2: connect pylink and wait for RTT readiness ---
        # Apollo510 may still be transitioning through the unobservable SBL
        # phase immediately after reset, so keep a small fixed floor delay here.
        # After that, retry the host attach and poll until the RTT control
        # block becomes visible.
        sbl_settle_started_s = time.monotonic()
        time.sleep(SBL_SETTLE_S)
        record_phase_duration("sbl_settle", sbl_settle_started_s)
        cb_deadline = time.monotonic() + _RTT_CB_TIMEOUT_S
        attach_started_s = time.monotonic()
        open_jlink_with_retry(
            jlink,
            device=jlink_device,
            jlink_serial=jlink_serial,
            timeout_s=_RTT_CB_TIMEOUT_S,
        )
        record_phase_duration("attach", attach_started_s)

        log.info("pylink connected to %s for RTT capture", jlink_device)
        resume_if_halted(jlink)

        # --- Step 3: start RTT and wait for control block ---
        # When the linked control block address is known (recovered from the
        # build map/ELF), attach directly and skip the host-side scan entirely.
        # The firmware re-initialises that fixed address on boot, so this is
        # both deterministic and ~100x faster than sweeping SRAM over SWD.  If
        # the firmware never publishes bytes there, the API-probe fallback below
        # re-scans, so a stale or mismatched address degrades gracefully.
        #
        # J-Link's built-in RTT auto-scan does not always cover the SRAM
        # regions used by armclang-linked firmware (e.g. the Apollo510
        # TCM at 0x2000xxxx).  Do an explicit host-side scan for the
        # "SEGGER RTT" magic signature and pass the address to
        # rtt_start() so discovery is deterministic across toolchains.
        # When the pre-clean wipe ran, stale blocks are gone, so the first
        # named, actively-producing "HPX" block is unambiguously ours and we
        # stop as soon as it appears.  When pre-clean could not attach, a stale
        # block may still carry leftover unread bytes that also look named and
        # "live", so we do NOT early-break on it: instead keep scanning through
        # the settle window and let scoring (size as tiebreaker) outlast the
        # race for the live block's signature to settle.
        if known_block_address is not None:
            block_address = known_block_address
            scan_started_s = time.monotonic()
            record_phase_duration(
                "control_block_scan",
                scan_started_s,
                detail=f"known_address=0x{known_block_address:08X}",
            )
        else:
            block_address = None
            best_score = -1
            first_candidate_s: float | None = None
            live_named_score = _RTT_NAME_MATCH_BONUS + _RTT_ACTIVITY_BONUS
            scan_started_s = time.monotonic()
            while time.monotonic() < cb_deadline:
                candidate = _scan_for_rtt_control_block(jlink, rtt_scan_ranges)
                if candidate is not None:
                    candidate_addr, candidate_score = candidate
                    if candidate_score > best_score:
                        block_address = candidate_addr
                        best_score = candidate_score
                    if preclean_ok and best_score >= live_named_score:
                        break
                    if first_candidate_s is None:
                        first_candidate_s = time.monotonic()
                    elif time.monotonic() - first_candidate_s >= _RTT_DISCOVERY_SETTLE_S:
                        break
                time.sleep(0.2)
            scan_detail = (
                f"block=0x{block_address:08X}, score={best_score}"
                if block_address is not None
                else "no_block"
            )
            record_phase_duration("control_block_scan", scan_started_s, detail=scan_detail)

        if block_address is not None:
            log.info("RTT control block located at 0x%08X", block_address)
            jlink.rtt_start(block_address=block_address)
        else:
            # Fall back to J-Link auto-scan if manual scan found nothing
            # (e.g. non-standard SRAM ranges).
            log.info("manual RTT scan did not find control block; falling back to J-Link auto-scan")
            jlink.rtt_start()

        # Probe the SEGGER RTT API directly instead of trusting NumUpBuffers,
        # which is unreliable on some J-Link/Apollo5 DLL combos (it can report 0
        # even while the RTT engine is happily delivering data).  The firmware
        # has already emitted HPX_READY and is streaming the HPX_START header in
        # lossless mode (it blocks until we drain), so a working attach returns
        # those bytes here.  Commit to the J-Link-driven RTT path on the first
        # byte (or a positive NumUpBuffers) and feed the probed bytes into the
        # handshake; fall back to direct SWD only if nothing arrives.
        probe_bytes = b""
        num_up_buffers = 0
        api_attached = False
        probe_deadline = time.monotonic() + _RTT_CB_TIMEOUT_S
        api_probe_started_s = time.monotonic()
        while time.monotonic() < probe_deadline:
            try:
                num_up_buffers = jlink.rtt_get_status().NumUpBuffers
            except Exception as exc:
                if not is_jlink_rtt_exception(exc):
                    raise
                num_up_buffers = 0
            try:
                chunk = bytes(jlink.rtt_read(0, 4096))
            except Exception as exc:
                if not is_jlink_rtt_exception(exc):
                    raise
                chunk = b""
            if chunk:
                probe_bytes += chunk
            if probe_bytes or num_up_buffers > 0:
                api_attached = True
                break
            time.sleep(0.05)
        record_phase_duration(
            "api_probe",
            api_probe_started_s,
            detail=(
                f"attached,num_up_buffers={num_up_buffers},probed_bytes={len(probe_bytes)}"
                if api_attached
                else "no_attach"
            ),
        )

        if not api_attached:
            if block_address is None:
                raise CaptureError(
                    "RTT control block not found on target",
                    hint=(
                        "Ensure the firmware was built with RTT support "
                        "(--transport rtt) and the target is running."
                    ),
                )
            if weights_region == "psram" and model_path is not None:
                raise CaptureError(
                    "RTT API attach failed on target",
                    hint="PSRAM model upload currently requires a working J-Link RTT API session.",
                )
            log.warning(
                "SEGGER RTT API delivered no data for control block 0x%08X; falling back to direct SWD polling",
                block_address,
            )
            try:
                jlink.rtt_stop()
            except Exception:
                pass
            try:
                jlink.close()
            except Exception:
                pass

            # Some Apollo5 setups leave the probe in a bad state after a failed
            # SEGGER RTT API attach. Reopen a fresh SWD session before the
            # manual control-block polling path so direct reads/writes behave
            # like a standalone memory-access session.
            jlink = create_debug_memory_session()
            fallback_attach_started_s = time.monotonic()
            open_jlink_with_retry(
                jlink,
                device=jlink_device,
                jlink_serial=jlink_serial,
                timeout_s=_RTT_CB_TIMEOUT_S,
            )
            record_phase_duration("fallback_attach", fallback_attach_started_s)
            resume_if_halted(jlink)
            active_block_address = block_address
            direct_idle_polls = 0

            def read_direct_chunk() -> bytes:
                nonlocal active_block_address
                nonlocal direct_idle_polls
                data, active_block_address = _direct_rtt_read_any(
                    jlink,
                    ranges=rtt_scan_ranges,
                    preferred_block_address=active_block_address,
                    max_bytes=4096,
                    allow_rescan=direct_idle_polls >= 20,
                )
                if data:
                    direct_idle_polls = 0
                else:
                    direct_idle_polls += 1
                return data

            handshake_started_s = time.monotonic()
            pending = _perform_rtt_ready_handshake(
                read_chunk=read_direct_chunk,
            )
            record_phase_duration(
                "ready_handshake_direct",
                handshake_started_s,
                detail=f"pending_bytes={len(pending)}",
            )
            read_after_handshake = _prepend_pending_bytes(read_direct_chunk, pending)

            collect_started_s = time.monotonic()
            lines = collect_lines(
                read_after_handshake,
                transport_name="RTT",
                overall_timeout_s=timeout_s,
                heartbeat_timeout_s=heartbeat_timeout_s,
                poll_interval_s=0.005,
                on_line=on_line,
            )
            record_phase_duration("line_collection_direct", collect_started_s)
            finalize_timing()
            return lines
        log.info(
            "SEGGER RTT API attached (NumUpBuffers=%d, probed %d bytes)",
            num_up_buffers,
            len(probe_bytes),
        )

        def read_rtt_chunk() -> bytes:
            # Pure J-Link RTT engine read.  Do NOT mix in a direct-SWD read on
            # this path: the background RTT engine and a manual _direct_rtt_read
            # would both drain the same up buffer and advance RdOff, racing each
            # other and interleaving/corrupting the byte stream (CSV rows spliced
            # mid-row with heartbeats).  The attach probe already proved the
            # engine delivers, so a single reader is correct and sufficient.
            return bytes(jlink.rtt_read(0, 4096))

        # Feed the bytes already drained during the attach probe (typically the
        # HPX_READY line) into the handshake so none are lost.
        handshake_read = (
            _prepend_pending_bytes(read_rtt_chunk, probe_bytes)
            if probe_bytes
            else read_rtt_chunk
        )
        handshake_started_s = time.monotonic()
        pending = _perform_rtt_ready_handshake(
            read_chunk=handshake_read,
        )
        record_phase_duration(
            "ready_handshake_api",
            handshake_started_s,
            detail=f"pending_bytes={len(pending)}",
        )

        # --- Step 3a: PSRAM model upload (if applicable) ---
        if weights_region == "psram" and model_path is not None:
            psram_upload_started_s = time.monotonic()
            _upload_model_to_psram(jlink, model_path, initial_buf=pending)
            record_phase_duration("psram_upload", psram_upload_started_s)
            pending = b""

        # --- Step 3b: collect lines via shared helper ---
        read_after_handshake = _prepend_pending_bytes(read_rtt_chunk, pending)
        collect_started_s = time.monotonic()
        lines = collect_lines(
            read_after_handshake,
            transport_name="RTT",
            overall_timeout_s=timeout_s,
            heartbeat_timeout_s=heartbeat_timeout_s,
            poll_interval_s=0.005,  # 5 ms — RTT has high bandwidth
            on_line=on_line,
        )
        record_phase_duration("line_collection_api", collect_started_s)
        finalize_timing()
        return lines

    except CaptureError:
        raise
    except Exception as exc:
        if is_jlink_exception(exc):
            raise CaptureError(
                f"J-Link RTT error: {exc}",
                hint="Check J-Link probe connection and that the probe is not in use.",
            ) from exc
        raise CaptureError(
            f"RTT capture failed: {exc}",
            hint="Check J-Link connection to the board.",
        ) from exc
    finally:
        try:
            jlink.rtt_stop()
        except Exception:
            pass
        try:
            jlink.close()
        except Exception:
            pass


class RttTransport(BaseCaptureTransport):
    """RTT capture backend.

    ``prepare`` recovers the linked RTT control-block address from the build
    artifacts so capture can attach directly and skip the slow SWD discovery
    sweep; ``collect`` runs :func:`capture_rtt_output`, which owns its own reset.
    """

    transport = Transport.RTT
    #: RTT always resets and re-attaches — it never holds the probe attached.
    honors_keep_attached = False

    def prepare(self, ctx, args: CaptureArgs) -> None:
        super().prepare(ctx, args)
        from ..capture.rtt_symbol import resolve_rtt_control_block_address

        # Recover the linked RTT control block address from the build artifacts
        # so capture can attach directly and skip the slow SWD discovery sweep.
        self._known_block_address = resolve_rtt_control_block_address(
            args.build_dir, ctx.config.target.toolchain
        )
        if self._known_block_address is not None:
            log.info(
                "Using known RTT control block address 0x%08X (skipping host-side scan)",
                self._known_block_address,
            )

    def collect(self, ctx) -> list[str]:
        from ..placement import Placement

        args = self._args
        return capture_rtt_output(
            jlink_serial=args.jlink_serial,
            jlink_device=args.jlink_device,
            rtt_scan_ranges=ctx.soc.rtt_scan_ranges,
            known_block_address=self._known_block_address,
            model_path=ctx.config.model.path,
            weights_region=ctx.weights_region or Placement.MRAM,
            timeout_s=args.overall_timeout_s,
            heartbeat_timeout_s=args.heartbeat_timeout_s,
            timing_out=args.timing_raw,
            reset_controller=args.reset_controller,
        )
