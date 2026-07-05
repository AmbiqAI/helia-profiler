"""Probe-facing protocols used by the profiling pipeline."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Protocol, Self


class Probe(Protocol):
    """Identity exposed by a discovered hardware debug probe."""

    serial: str
    product: str
    connection: str


class ProbeSession(Protocol):
    """Open debug-session lifetime."""

    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type: object, exc: object, tb: object) -> object: ...

    def close(self) -> None: ...


class FlashBackend(Protocol):
    """Firmware flashing backend."""

    def flash(
        self,
        firmware_path: Path,
        *,
        toolchain: str,
        jlink_serial: str | None = None,
        timeout_s: float,
        verbose: bool = False,
    ) -> None: ...


class ResetController(Protocol):
    """Named target reset primitives selected by lifecycle policy."""

    def debug_reset(self, *, device: str, jlink_serial: str | None = None) -> None: ...

    def swpoi_reset(self, *, device: str, jlink_serial: str | None = None) -> None: ...

    def attached_reset_session(
        self,
        *,
        device: str,
        jlink_serial: str | None = None,
        attach_timeout_s: float = 30.0,
        settle_s: float = ...,
    ) -> AbstractContextManager[DebugMemorySession]: ...


class DebugMemorySession(ProbeSession, Protocol):
    """Minimal debug-memory surface needed by RTT capture."""

    def open(self, serial_no: int | None = None) -> None: ...

    def disable_dialog_boxes(self) -> None: ...

    def set_tif(self, interface: Any) -> None: ...

    def connect(self, device: str, speed: int) -> None: ...

    def halt(self) -> None: ...

    def halted(self) -> bool: ...

    def restart(self) -> None: ...

    def reset(self, halt: bool = False) -> None: ...

    def memory_read8(self, addr: int, num_items: int) -> Sequence[int]: ...

    def memory_read32(self, addr: int, num_items: int) -> Sequence[int]: ...

    def memory_write8(self, addr: int, data: Sequence[int]) -> None: ...

    def memory_write32(self, addr: int, data: Sequence[int]) -> None: ...

    def memory_write(self, addr: int, data: Sequence[int], *, nbits: int) -> None: ...

    def rtt_start(self, block_address: int | None = None) -> None: ...

    def rtt_stop(self) -> None: ...

    def rtt_get_status(self) -> Any: ...

    def rtt_read(self, buffer_index: int, num_bytes: int) -> Sequence[int]: ...

    def rtt_write(self, buffer_index: int, data: Sequence[int]) -> int | None: ...
