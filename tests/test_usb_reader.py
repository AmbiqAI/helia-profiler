from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from helia_profiler.transport import usb_cdc as usb_reader
from helia_profiler.errors import CaptureError
from helia_profiler.transport.usb_identity import USB_MARKER_PREFIX, usb_marker_serial


def _port(device, **kw):
    base = dict(
        manufacturer=None,
        product=None,
        description=None,
        interface=None,
        hwid="",
        serial_number=None,
    )
    base.update(kw)
    return SimpleNamespace(device=device, **base)


def test_find_cdc_port_raises_when_only_jlink_ports_exist(monkeypatch):
    monkeypatch.setattr(
        usb_reader,
        "_snapshot_cdc_ports",
        lambda: {"/dev/ttyACM0", "/dev/ttyACM1"},
    )
    monkeypatch.setattr(
        usb_reader.list_ports,
        "comports",
        lambda: [
            SimpleNamespace(
                device="/dev/ttyACM0",
                manufacturer="SEGGER",
                product="J-Link",
                description="SEGGER J-Link",
                interface="J-Link VCOM",
                hwid="USB VID:PID=1366:0105",
            ),
            SimpleNamespace(
                device="/dev/ttyACM1",
                manufacturer="SEGGER",
                product="J-Link",
                description="SEGGER J-Link",
                interface="J-Link VCOM",
                hwid="USB VID:PID=1366:0105",
            ),
        ],
    )

    with pytest.raises(CaptureError, match="No application USB CDC device appeared") as exc_info:
        usb_reader._find_cdc_port(pre_existing={"/dev/ttyACM0", "/dev/ttyACM1"}, timeout_s=0)

    hint = exc_info.value.hint or ""
    assert "J-Link" in hint


def test_find_cdc_port_no_device_hint_covers_host_platforms(monkeypatch):
    monkeypatch.setattr(usb_reader, "_snapshot_cdc_ports", lambda: set())

    with pytest.raises(CaptureError, match="No USB CDC device found") as exc_info:
        usb_reader._find_cdc_port(timeout_s=0)

    hint = exc_info.value.hint or ""
    assert "hpx ports list --all" in hint
    assert "ls /dev/tty.usbmodem*" in hint
    assert "ls /dev/ttyACM*" in hint


def test_find_cdc_port_falls_back_to_existing_non_jlink(monkeypatch):
    monkeypatch.setattr(
        usb_reader,
        "_snapshot_cdc_ports",
        lambda: {"/dev/ttyACM0", "/dev/ttyACM2"},
    )
    monkeypatch.setattr(
        usb_reader.list_ports,
        "comports",
        lambda: [
            SimpleNamespace(
                device="/dev/ttyACM0",
                manufacturer="SEGGER",
                product="J-Link",
                description="SEGGER J-Link",
                interface="J-Link VCOM",
                hwid="USB VID:PID=1366:0105",
            ),
            SimpleNamespace(
                device="/dev/ttyACM2",
                manufacturer="Ambiq",
                product="TinyUSB CDC",
                description="Apollo USB CDC",
                interface="CDC",
                hwid="USB VID:PID=1234:5678",
            ),
        ],
    )

    port = usb_reader._find_cdc_port(pre_existing={"/dev/ttyACM0", "/dev/ttyACM2"}, timeout_s=0)

    assert port == "/dev/ttyACM2"


def test_find_cdc_port_raises_on_multiple_app_devices(monkeypatch):
    """Two non-J-Link CDC devices is ambiguous — must raise, not guess."""
    monkeypatch.setattr(
        usb_reader,
        "_snapshot_cdc_ports",
        lambda: {"/dev/ttyACM1", "/dev/ttyACM2"},
    )
    monkeypatch.setattr(
        usb_reader.list_ports,
        "comports",
        lambda: [
            _port("/dev/ttyACM1", manufacturer="Ambiq", product="NSX USB Device"),
            _port("/dev/ttyACM2", manufacturer="Ambiq", product="NSX USB Device"),
        ],
    )

    with pytest.raises(CaptureError, match="could not be identified automatically") as exc_info:
        usb_reader._find_cdc_port(timeout_s=0)

    assert "--usb-port" in (exc_info.value.hint or "")


def test_usb_marker_serial_derivation():
    assert usb_marker_serial(None) is None
    assert usb_marker_serial("") is None
    assert usb_marker_serial("1160001350") == f"{USB_MARKER_PREFIX}1160001350"
    # Truncated to the 31-char USB string-descriptor limit.
    truncated = usb_marker_serial("9" * 40)
    assert truncated is not None
    assert len(truncated) == 31


def test_find_port_by_marker_matches_serial_number():
    marker = usb_marker_serial("1160001350")
    assert marker is not None
    monkeypatch_ports = [
        _port("/dev/ttyACM0", manufacturer="SEGGER", product="J-Link", serial_number="1160001350"),
        _port(
            "/dev/ttyACM1", manufacturer="Ambiq", product="NSX HPX Profiler", serial_number=marker
        ),
        _port(
            "/dev/ttyACM2", manufacturer="Ambiq", product="NSX USB Device", serial_number="000001"
        ),
    ]
    import helia_profiler.transport.usb_cdc as mod

    orig = mod.list_ports.comports
    mod.list_ports.comports = (  # ty: ignore[invalid-assignment]
        lambda: monkeypatch_ports
    )  # manual monkeypatch of the pyserial module attr
    try:
        assert mod._find_port_by_marker(marker) == "/dev/ttyACM1"
        assert mod._find_port_by_marker("HPX-nope") is None
    finally:
        mod.list_ports.comports = orig


def test_resolve_cdc_port_prefers_marker(monkeypatch):
    marker = usb_marker_serial("1160001350")
    monkeypatch.setattr(usb_reader.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        usb_reader,
        "_snapshot_cdc_ports",
        lambda: {"/dev/ttyACM1", "/dev/ttyACM3"},
    )
    monkeypatch.setattr(
        usb_reader.list_ports,
        "comports",
        lambda: [
            _port(
                "/dev/ttyACM1",
                manufacturer="Ambiq",
                product="NSX USB Device",
                serial_number="000001",
            ),
            _port(
                "/dev/ttyACM3",
                manufacturer="Ambiq",
                product="NSX HPX Profiler",
                serial_number=marker,
            ),
        ],
    )

    port = usb_reader.resolve_cdc_port(marker=marker, pre_existing=set(), timeout_s=1)

    assert port == "/dev/ttyACM3"


def test_find_cdc_port_rejects_foreign_hpx_device(monkeypatch):
    """A present CDC device carrying a *different* HPX marker is another board.

    It must never be used as the heuristic fallback, otherwise the capture
    opens the wrong EVB and blocks until the read timeout.
    """
    expected = usb_marker_serial("1160001350")
    foreign = usb_marker_serial("1160002204")
    monkeypatch.setattr(
        usb_reader,
        "_snapshot_cdc_ports",
        lambda: {"/dev/ttyACM0", "/dev/ttyACM3"},
    )
    monkeypatch.setattr(
        usb_reader.list_ports,
        "comports",
        lambda: [
            _port(
                "/dev/ttyACM0", manufacturer="SEGGER", product="J-Link", serial_number="1160001350"
            ),
            _port(
                "/dev/ttyACM3",
                manufacturer="Ambiq",
                product="NSX HPX Profiler",
                serial_number=foreign,
            ),
        ],
    )

    with pytest.raises(CaptureError, match="No application USB CDC device appeared"):
        usb_reader._find_cdc_port(timeout_s=0, expected_marker=expected)


def test_resolve_cdc_port_does_not_fall_back_to_foreign_hpx(monkeypatch):
    expected = usb_marker_serial("1160001350")
    foreign = usb_marker_serial("1160002204")
    monkeypatch.setattr(usb_reader.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        usb_reader,
        "_snapshot_cdc_ports",
        lambda: {"/dev/ttyACM3"},
    )
    monkeypatch.setattr(
        usb_reader.list_ports,
        "comports",
        lambda: [
            _port(
                "/dev/ttyACM3",
                manufacturer="Ambiq",
                product="NSX HPX Profiler",
                serial_number=foreign,
            ),
        ],
    )

    with pytest.raises(CaptureError, match="No application USB CDC device appeared"):
        usb_reader.resolve_cdc_port(marker=expected, pre_existing=set(), timeout_s=0)


class _ChunkedSerial:
    """Serial fake returning one queued chunk per read."""

    def __init__(self, chunks: list[bytes]):
        self.chunks = list(chunks)
        self.is_open = True
        self.dtr = False
        self.timeout = None

    @property
    def in_waiting(self) -> int:
        return len(self.chunks[0]) if self.chunks else 0

    def reset_input_buffer(self) -> None:
        pass

    def readline(self) -> bytes:
        return self.read(0)

    def read(self, count: int) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""

    def close(self) -> None:
        self.is_open = False


def _capture_usb(monkeypatch, chunks: list[bytes], **kwargs) -> list[str]:
    port = _ChunkedSerial(chunks)
    monkeypatch.setattr(usb_reader, "_snapshot_cdc_ports", lambda: set())
    monkeypatch.setattr(usb_reader.time, "sleep", lambda *_: None)
    monkeypatch.setattr(usb_reader.serial, "Serial", lambda **_: port)
    reset = MagicMock()
    lines = usb_reader.capture_usb_output(
        jlink_device="dev", usb_port="/dev/ttyACM9", reset_controller=reset, **kwargs
    )
    assert not port.is_open
    return lines


def test_usb_capture_joins_partial_reads(monkeypatch):
    chunks = [b"--- HPX_START ---\n", b"0,CONV", b"_2D,5\n", b"--- HPX_END ---\n"]
    lines = _capture_usb(monkeypatch, chunks, timeout_s=5)
    assert lines == ["--- HPX_START ---", "0,CONV_2D,5", "--- HPX_END ---"]


def test_usb_capture_honours_heartbeat_timeout(monkeypatch):
    import time

    started = time.monotonic()
    lines = _capture_usb(
        monkeypatch, [b"--- HPX_START ---\n"], timeout_s=None, heartbeat_timeout_s=0.1
    )
    assert lines == ["--- HPX_START ---"]
    assert time.monotonic() - started < 5
