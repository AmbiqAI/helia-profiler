from __future__ import annotations

from types import SimpleNamespace

import pytest

from helia_profiler.capture import usb_reader
from helia_profiler.errors import CaptureError


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
