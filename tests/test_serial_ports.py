"""One classifier decides J-Link VCOM vs HPX CDC for the CLI and both serial transports."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from helia_profiler.errors import CaptureError
from helia_profiler.transport import uart
from helia_profiler.transport.ports import (
    HPX_USB_CDC,
    JLINK_VCOM,
    SERIAL,
    list_serial_ports,
)
from helia_profiler.transport.usb_identity import usb_marker_serial


def _info(device="/dev/ttyACM0", **kw):
    fields = dict(
        description="",
        manufacturer="",
        product="",
        serial_number="",
        interface="",
        hwid="USB VID:PID=2AEC:6010",
    )
    fields.update(kw)
    return SimpleNamespace(device=device, **fields)


@pytest.mark.parametrize(
    "fields,kind",
    [
        pytest.param({"manufacturer": "SEGGER"}, JLINK_VCOM, id="segger-manufacturer"),
        pytest.param({"product": "J-Link"}, JLINK_VCOM, id="j-link-product"),
        pytest.param({"description": "JLink CDC UART Port (COM3)"}, JLINK_VCOM, id="windows"),
        pytest.param({"interface": "J-Link VCOM"}, JLINK_VCOM, id="interface"),
        pytest.param({"hwid": "USB VID:PID=1366:1024 SER=001160002954"}, JLINK_VCOM, id="vid"),
        pytest.param({"serial_number": usb_marker_serial("1160002204")}, HPX_USB_CDC, id="marker"),
        pytest.param({"manufacturer": "Ambiq", "serial_number": "000001"}, SERIAL, id="app"),
    ],
)
def test_classifier(monkeypatch, fields, kind):
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: [_info(**fields)])

    (port,) = list_serial_ports(include_all=True)

    assert port.kind == kind


def test_default_listing_keeps_usb_ports_on_every_host(monkeypatch):
    monkeypatch.setattr(
        "serial.tools.list_ports.comports",
        lambda: [
            _info("COM1", hwid="ACPI\\PNP0501\\0"),
            _info("COM7", description="USB Serial Device (COM7)"),
            _info("/dev/cu.usbmodem14201"),
            _info("/dev/cu.Bluetooth-Incoming-Port", hwid="n/a"),
            _info("/dev/ttyUSB0", hwid="USB VID:PID=0403:6001 SER=A1"),
        ],
    )

    devices = [port.device for port in list_serial_ports()]

    assert devices == ["COM7", "/dev/cu.usbmodem14201", "/dev/ttyUSB0"]


def test_uart_selects_vcom_by_probe_serial(monkeypatch):
    monkeypatch.setattr(
        "serial.tools.list_ports.comports",
        lambda: [
            _info("COM3", description="JLink CDC UART Port (COM3)", serial_number="001160002954"),
            _info("COM4", hwid="USB VID:PID=1366:1024 SER=001160001350"),
            _info("COM7", serial_number=usb_marker_serial("1160002954")),
        ],
    )

    assert uart.find_jlink_vcom_port("1160001350") == "COM4"
    with pytest.raises(CaptureError, match="Multiple J-Link VCOM ports"):
        uart.find_jlink_vcom_port(None)


def test_uart_missing_vcom_hint_points_at_ports_list(monkeypatch):
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: [_info("COM7")])

    with pytest.raises(CaptureError) as exc_info:
        uart.find_jlink_vcom_port(None)

    assert "hpx ports list --all" in (exc_info.value.hint or "")
