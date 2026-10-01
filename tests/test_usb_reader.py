from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from helia_profiler.errors import CaptureError
from helia_profiler.transport import usb_cdc as usb_reader
from helia_profiler.transport.usb_identity import USB_MARKER_PREFIX, usb_marker_serial

_APP_HWID = "USB VID:PID=2AEC:6010 SER=000001"


def _port(device, **kw):
    base = dict(
        manufacturer=None,
        product=None,
        description=None,
        interface=None,
        hwid=_APP_HWID,
        serial_number=None,
    )
    base.update(kw)
    return SimpleNamespace(device=device, **base)


def _jlink(device, **kw):
    fields = dict(
        manufacturer="SEGGER",
        product="J-Link",
        description="SEGGER J-Link",
        interface="J-Link VCOM",
        hwid="USB VID:PID=1366:0105 SER=001160002954",
        serial_number="001160002954",
    )
    fields.update(kw)
    return _port(device, **fields)


def _set_comports(monkeypatch, ports):
    calls = []

    def comports():
        calls.append(None)
        return list(ports)

    monkeypatch.setattr("serial.tools.list_ports.comports", comports)
    return calls


def test_find_cdc_port_raises_when_only_jlink_ports_exist(monkeypatch):
    _set_comports(monkeypatch, [_jlink("/dev/ttyACM0"), _jlink("/dev/ttyACM1")])

    with pytest.raises(CaptureError, match="No application USB CDC device appeared") as exc_info:
        usb_reader._find_cdc_port(pre_existing={"/dev/ttyACM0", "/dev/ttyACM1"}, timeout_s=0)

    hint = exc_info.value.hint or ""
    assert "J-Link" in hint


def test_find_cdc_port_no_device_hint_points_at_ports_list(monkeypatch):
    _set_comports(monkeypatch, [])

    with pytest.raises(CaptureError, match="No USB CDC device found") as exc_info:
        usb_reader._find_cdc_port(timeout_s=0)

    hint = exc_info.value.hint or ""
    assert "hpx ports list --all" in hint
    assert "/dev/" not in hint


def test_find_cdc_port_falls_back_to_existing_non_jlink(monkeypatch):
    _set_comports(
        monkeypatch,
        [
            _jlink("/dev/ttyACM0"),
            _port(
                "/dev/ttyACM2",
                manufacturer="Ambiq",
                product="TinyUSB CDC",
                description="Apollo USB CDC",
                interface="CDC",
            ),
        ],
    )

    port = usb_reader._find_cdc_port(pre_existing={"/dev/ttyACM0", "/dev/ttyACM2"}, timeout_s=0)

    assert port == "/dev/ttyACM2"


@pytest.mark.parametrize(
    "jlink,app",
    [
        pytest.param(
            _jlink("COM3", description="JLink CDC UART Port (COM3)", manufacturer="SEGGER"),
            _port("COM7", description="USB Serial Device (COM7)", manufacturer="Microsoft"),
            id="windows",
        ),
        pytest.param(
            _jlink("/dev/ttyACM0"),
            _port("/dev/ttyUSB0", manufacturer="Ambiq"),
            id="linux-ttyusb",
        ),
        pytest.param(
            _jlink("/dev/cu.usbmodem0011600029541", manufacturer=None, description="J-Link"),
            _port("/dev/cu.usbmodem14201", product="NSX USB Device"),
            id="macos",
        ),
    ],
)
def test_find_cdc_port_detects_newly_enumerated_port_on_every_host(monkeypatch, jlink, app):
    """A fresh CDC device is found whatever the host names it: COMx, ttyUSB, or cu.*."""
    ports = [jlink]
    _set_comports(monkeypatch, ports)
    pre_existing = set(usb_reader._snapshot_cdc_ports())
    assert pre_existing == {jlink.device}

    ports.append(app)
    monkeypatch.setattr(usb_reader.time, "sleep", lambda *_: None)

    assert usb_reader._find_cdc_port(pre_existing=pre_existing, timeout_s=1) == app.device


def test_find_cdc_port_never_falls_back_to_macos_jlink_vcom(monkeypatch):
    """macOS J-Link VCOMs enumerate as cu.* devices and are still never app candidates."""
    _set_comports(
        monkeypatch,
        [_jlink("/dev/cu.usbmodem0011600029541"), _jlink("/dev/cu.usbmodem0011600022041")],
    )

    with pytest.raises(CaptureError, match="No application USB CDC device appeared"):
        usb_reader._find_cdc_port(timeout_s=0)


def test_find_cdc_port_enumerates_once_per_poll(monkeypatch):
    calls = _set_comports(
        monkeypatch,
        [
            _jlink("/dev/ttyACM0"),
            _port("/dev/ttyACM1", manufacturer="Ambiq", serial_number="000001"),
            _port("/dev/ttyACM2", manufacturer="Ambiq", serial_number="000002"),
        ],
    )

    with pytest.raises(CaptureError, match="could not be identified automatically") as exc_info:
        usb_reader._find_cdc_port(timeout_s=0, expected_marker=usb_marker_serial("1"))

    assert len(calls) == 1
    assert "/dev/ttyACM1 (Ambiq, 000001)" in str(exc_info.value)


def test_find_cdc_port_raises_on_multiple_app_devices(monkeypatch):
    """Two non-J-Link CDC devices is ambiguous — must raise, not guess."""
    _set_comports(
        monkeypatch,
        [
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


def test_find_port_by_marker_matches_serial_number(monkeypatch):
    marker = usb_marker_serial("1160001350")
    assert marker is not None
    _set_comports(
        monkeypatch,
        [
            _jlink("/dev/ttyACM0", serial_number="1160001350"),
            _port(
                "/dev/ttyACM1",
                manufacturer="Ambiq",
                product="NSX HPX Profiler",
                serial_number=marker,
            ),
            _port(
                "/dev/ttyACM2",
                manufacturer="Ambiq",
                product="NSX USB Device",
                serial_number="000001",
            ),
        ],
    )

    assert usb_reader._find_port_by_marker(marker) == "/dev/ttyACM1"
    assert usb_reader._find_port_by_marker("HPX-nope") is None


def test_resolve_cdc_port_prefers_marker(monkeypatch):
    marker = usb_marker_serial("1160001350")
    monkeypatch.setattr(usb_reader.time, "sleep", lambda *_: None)
    _set_comports(
        monkeypatch,
        [
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
    _set_comports(
        monkeypatch,
        [
            _jlink("/dev/ttyACM0", serial_number="1160001350"),
            _port(
                "/dev/ttyACM3",
                manufacturer="Ambiq",
                product="NSX HPX Profiler",
                serial_number=foreign,
            ),
        ],
    )

    with pytest.raises(CaptureError, match="stamped for another board") as exc_info:
        usb_reader._find_cdc_port(timeout_s=0, expected_marker=expected)

    assert f"/dev/ttyACM3 (Ambiq, NSX HPX Profiler, {foreign})" in str(exc_info.value)
    assert "J-Link" not in (exc_info.value.hint or "")


def test_resolve_cdc_port_does_not_fall_back_to_foreign_hpx(monkeypatch):
    expected = usb_marker_serial("1160001350")
    foreign = usb_marker_serial("1160002204")
    monkeypatch.setattr(usb_reader.time, "sleep", lambda *_: None)
    _set_comports(
        monkeypatch,
        [
            _port(
                "/dev/ttyACM3",
                manufacturer="Ambiq",
                product="NSX HPX Profiler",
                serial_number=foreign,
            ),
        ],
    )

    with pytest.raises(CaptureError, match="stamped for another board"):
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


def test_resolve_target_cdc_port_prefers_pinned_port(monkeypatch):
    def _fail(**_):
        raise AssertionError("a pinned port must not be resolved")

    monkeypatch.setattr(usb_reader, "resolve_cdc_port", _fail)
    assert usb_reader.resolve_target_cdc_port(usb_port="COM9", marker="HPX-1") == "COM9"


def test_resolve_target_cdc_port_forwards_to_resolver(monkeypatch):
    calls = []

    def _resolve(**kwargs):
        calls.append(kwargs)
        return "/dev/ttyACM3"

    monkeypatch.setattr(usb_reader, "resolve_cdc_port", _resolve)
    port = usb_reader.resolve_target_cdc_port(usb_port=None, marker="HPX-1", timeout_s=2)
    assert port == "/dev/ttyACM3"
    assert calls == [{"marker": "HPX-1", "timeout_s": 2}]


def test_open_cdc_port_asserts_dtr(monkeypatch):
    opened = []

    class _Serial:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.dtr = False
            opened.append(self)

    monkeypatch.setattr(usb_reader.serial, "Serial", _Serial)
    ser = usb_reader.open_cdc_port("COM9", timeout=0.5)
    assert ser is opened[0]
    assert ser.dtr is True
    assert ser.kwargs == {
        "port": "COM9",
        "baudrate": usb_reader.BAUD,
        "timeout": 0.5,
        "dsrdtr": True,
    }


def test_open_cdc_port_closes_when_dtr_fails(monkeypatch):
    closed = []

    class _Serial:
        def __init__(self, **_):
            pass

        @property
        def dtr(self):
            return False

        @dtr.setter
        def dtr(self, _value):
            raise usb_reader.serial.SerialException("dtr failed")

        def close(self):
            closed.append(self)

    monkeypatch.setattr(usb_reader.serial, "Serial", _Serial)
    with pytest.raises(usb_reader.serial.SerialException):
        usb_reader.open_cdc_port("COM9", timeout=None)
    assert len(closed) == 1
