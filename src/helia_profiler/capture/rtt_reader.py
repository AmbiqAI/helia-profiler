"""RTT capture transport — reads HPX output via SEGGER Real-Time Transfer.

RTT uses an in-SRAM ring buffer that the J-Link reads via background SWD
memory accesses — **zero CPU interrupts, zero PMU contamination**.

The firmware uses ``SEGGER_RTT_MODE_NO_BLOCK_TRIM`` with a 32 KB up-buffer.
Writes that don't fit are silently dropped (no blocking).  After all output
is written the firmware calls ``SCB_CleanDCache()`` so the J-Link host can
read the data via SWD (which bypasses the CPU D-cache).

When ``weights_region="psram"``, the firmware initialises PSRAM and emits
``HPX_PSRAM_READY=<addr>,<size>`` before waiting.  The host writes the
model flatbuffer to the PSRAM XIP address via ``jlink.memory_write()`` and
sends ``HPX_GO`` on the RTT down-channel to resume inference.

Sequence:
  1. Reset the target via JLinkExe (handles Apollo510 SBL correctly).
  2. Connect pylink and start RTT — locate the RTT control block.
  3. (PSRAM) Wait for HPX_PSRAM_READY, upload model, send HPX_GO.
  4. Collect lines until ``--- HPX_END ---`` or timeout.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import CaptureError
from ..jlink import reset_target
from .readiness import open_jlink_with_retry, resume_if_halted
from .transport import (
    DEFAULT_TIMEOUT_S,
    HEARTBEAT_TIMEOUT_S,
    HPX_END,
    HPX_START,
    collect_lines,
)

log = logging.getLogger("hpx")

if TYPE_CHECKING:
    import pylink

_RTT_CB_TIMEOUT_S = 30  # max time to wait for RTT control block discovery
_PSRAM_READY_TIMEOUT_S = 15  # wait for PSRAM init + ready signal
_PSRAM_WRITE_CHUNK = 65536  # bytes per J-Link memory_write call

_RTT_SCAN_CHUNK = 0x4000  # 16 KB per memory_read call
_RTT_MAGIC = b"SEGGER RTT"
_RTT_DESC_WORDS = 6
_RTT_CB_HEADER_SIZE = 24
_RTT_DESC_SIZE = _RTT_DESC_WORDS * 4


def _direct_rtt_read(
    jlink: "pylink.JLink",
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


def _scan_for_rtt_control_block(
    jlink: "pylink.JLink",
    ranges: tuple[tuple[int, int], ...],
) -> int | None:
    """Scan SRAM for the "SEGGER RTT" control-block magic.

    Returns the absolute address of the control block, or None if not
    found.  This is a deterministic fallback for J-Link's built-in RTT
    auto-scan, which on some devices (notably Apollo510 + armclang
    linker output) fails to cover the relevant SRAM region.
    """
    for base, length in ranges:
        for offset in range(0, length, _RTT_SCAN_CHUNK):
            try:
                chunk = bytes(jlink.memory_read8(base + offset, _RTT_SCAN_CHUNK))
            except Exception:  # noqa: BLE001 — probe errors are non-fatal
                continue
            idx = chunk.find(_RTT_MAGIC)
            if idx >= 0:
                return base + offset + idx
    return None


def _upload_model_to_psram(
    jlink: "pylink.JLink",
    model_path: Path,
    timeout_s: float = _PSRAM_READY_TIMEOUT_S,
) -> None:
    """Wait for HPX_PSRAM_READY, upload model via J-Link, send HPX_GO.

    The firmware emits ``HPX_PSRAM_READY=0x60000000,<size>\\n`` once PSRAM
    is initialised and XIP is enabled.  We read RTT until we see that line,
    then write the model flatbuffer directly to the PSRAM XIP address via
    SWD memory writes, and finally send ``HPX_GO`` on RTT down-channel 0
    so the firmware proceeds with inference.
    """
    buf = b""
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
    jlink.rtt_write(0, list(b"HPX_GO"))
    log.info("Sent HPX_GO — firmware resuming")


def capture_rtt_output(
    *,
    jlink_serial: str | None = None,
    jlink_device: str = "AP510NFA-CBR",
    rtt_scan_ranges: tuple[tuple[int, int], ...],
    timeout_s: float | None = None,
    heartbeat_timeout_s: float = HEARTBEAT_TIMEOUT_S,
    model_path: Path | None = None,
    weights_region: str = "mram",
    timing_out: dict[str, float] | None = None,
) -> list[str]:
    """Capture firmware output via SEGGER RTT until HPX_END or hang detection.

    When *weights_region* is ``"psram"``, the function uploads the model
    flatbuffer to PSRAM via J-Link SWD writes before collecting profiling
    output.

    Args:
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

    def on_line(line: str, line_ts: float) -> None:
        nonlocal hpx_start_s, hpx_end_s
        if line == HPX_START and hpx_start_s is None:
            hpx_start_s = line_ts
        elif line == HPX_END:
            hpx_end_s = line_ts

    def finalize_timing() -> None:
        if timing_out is None:
            return
        timing_out["capture_duration_s"] = time.monotonic() - capture_started_s
        if hpx_start_s is not None:
            timing_out["hpx_start_latency_s"] = hpx_start_s - capture_started_s
        if hpx_start_s is not None and hpx_end_s is not None:
            timing_out["protocol_duration_s"] = hpx_end_s - hpx_start_s

    try:
        import pylink
    except ImportError as exc:
        raise CaptureError(
            "pylink-square package not installed (required for RTT transport)",
            hint="pip install pylink-square",
        ) from exc

    jlink = pylink.JLink()

    try:
        # --- Step 1: reset the target via JLinkExe subprocess ---
        # JLinkExe handles the Apollo510 secure bootloader (SBL) correctly;
        # pylink's reset() does not trigger the vendor-specific handler.
        reset_target(device=jlink_device, jlink_serial=jlink_serial)

        # --- Step 2: connect pylink and wait for RTT readiness ---
        # Apollo510 may still be transitioning through SBL immediately after
        # reset. Rather than burning a fixed settle delay up front, retry the
        # host attach and then poll until the RTT control block becomes visible.
        cb_deadline = time.monotonic() + _RTT_CB_TIMEOUT_S
        open_jlink_with_retry(
            jlink,
            device=jlink_device,
            jlink_serial=jlink_serial,
            timeout_s=_RTT_CB_TIMEOUT_S,
        )

        log.info("pylink connected to %s for RTT capture", jlink_device)
        resume_if_halted(jlink)

        # --- Step 3: start RTT and wait for control block ---
        # J-Link's built-in RTT auto-scan does not always cover the SRAM
        # regions used by armclang-linked firmware (e.g. the Apollo510
        # TCM at 0x2000xxxx).  Do an explicit host-side scan for the
        # "SEGGER RTT" magic signature and pass the address to
        # rtt_start() so discovery is deterministic across toolchains.
        block_address = None
        while time.monotonic() < cb_deadline and block_address is None:
            block_address = _scan_for_rtt_control_block(jlink, rtt_scan_ranges)
            if block_address is None:
                time.sleep(0.2)

        if block_address is not None:
            log.info("RTT control block located at 0x%08X", block_address)
            jlink.rtt_start(block_address=block_address)
        else:
            # Fall back to J-Link auto-scan if manual scan found nothing
            # (e.g. non-standard SRAM ranges).
            log.info("manual RTT scan did not find control block; falling back to J-Link auto-scan")
            jlink.rtt_start()

        status = None
        while time.monotonic() < cb_deadline:
            try:
                status = jlink.rtt_get_status()
                if status.NumUpBuffers > 0:
                    break
            except pylink.errors.JLinkRTTException:
                status = None
            time.sleep(0.05)

        if status is None or status.NumUpBuffers == 0:
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
                "SEGGER RTT API did not attach to control block 0x%08X; falling back to direct SWD polling",
                block_address,
            )
            lines = collect_lines(
                lambda: _direct_rtt_read(jlink, block_address=block_address, max_bytes=4096),
                transport_name="RTT",
                overall_timeout_s=timeout_s,
                heartbeat_timeout_s=heartbeat_timeout_s,
                poll_interval_s=0.005,
                on_line=on_line,
            )
            finalize_timing()
            return lines
        log.info(
            "RTT control block found (%d up buffers)",
            status.NumUpBuffers,
        )

        # --- Step 3a: PSRAM model upload (if applicable) ---
        if weights_region == "psram" and model_path is not None:
            _upload_model_to_psram(jlink, model_path)

        # --- Step 3b: collect lines via shared helper ---
        lines = collect_lines(
            lambda: bytes(jlink.rtt_read(0, 4096)),
            transport_name="RTT",
            overall_timeout_s=timeout_s,
            heartbeat_timeout_s=heartbeat_timeout_s,
            poll_interval_s=0.005,  # 5 ms — RTT has high bandwidth
            on_line=on_line,
        )
        finalize_timing()
        return lines

    except CaptureError:
        raise
    except pylink.errors.JLinkException as exc:
        raise CaptureError(
            f"J-Link RTT error: {exc}",
            hint="Check J-Link probe connection and that the probe is not in use.",
        ) from exc
    except Exception as exc:
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
