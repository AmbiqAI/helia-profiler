"""Typed host serial-port discovery shared by the CLI and the serial transports."""

from __future__ import annotations

from dataclasses import dataclass

from .usb_identity import USB_MARKER_PREFIX

JLINK_VCOM = "jlink-vcom"
HPX_USB_CDC = "hpx-usb-cdc"
SERIAL = "serial"

_SEGGER_VID = "VID:PID=1366:"
_JLINK_MARKERS = ("segger", "jlink")


@dataclass(frozen=True)
class SerialPortInfo:
    """Description of one host serial port relevant to HPX transports."""

    device: str
    kind: str
    description: str = ""
    manufacturer: str = ""
    product: str = ""
    serial_number: str = ""
    interface: str = ""
    hwid: str = ""


def list_serial_ports(*, include_all: bool = False) -> tuple[SerialPortInfo, ...]:
    """Return host serial ports from one enumeration, filtering unrelated devices by default.

    The default view keeps ports whose ``hwid`` carries a ``VID:PID`` and
    anything classified as a J-Link VCOM or an HPX CDC device; every other port
    is dropped. pyserial writes ``USB VID:PID=`` into ``hwid`` for USB devices:
    https://github.com/pyserial/pyserial/blob/master/serial/tools/list_ports_common.py
    """
    from serial.tools import list_ports

    ports = tuple(_describe_serial_port(info) for info in list_ports.comports())
    if include_all:
        return ports
    return tuple(port for port in ports if _is_relevant_serial_port(port))


def normalize_descriptor(value: str | None) -> str:
    """Lowercased alphanumeric-only form of *value* for tolerant descriptor matching."""
    return "".join(ch for ch in (value or "") if ch.isalnum()).lower()


def _classify(fields: dict[str, str]) -> str:
    if _SEGGER_VID in fields["hwid"].upper():
        return JLINK_VCOM
    haystack = normalize_descriptor(
        " ".join(
            fields[name] for name in ("manufacturer", "product", "description", "interface", "hwid")
        )
    )
    if any(marker in haystack for marker in _JLINK_MARKERS):
        return JLINK_VCOM
    if fields["serial_number"].startswith(USB_MARKER_PREFIX):
        return HPX_USB_CDC
    return SERIAL


def _describe_serial_port(info: object) -> SerialPortInfo:
    fields = {
        "device": str(getattr(info, "device", "") or ""),
        "description": str(getattr(info, "description", "") or ""),
        "manufacturer": str(getattr(info, "manufacturer", "") or ""),
        "product": str(getattr(info, "product", "") or ""),
        "serial_number": str(getattr(info, "serial_number", "") or ""),
        "interface": str(getattr(info, "interface", "") or ""),
        "hwid": str(getattr(info, "hwid", "") or ""),
    }
    return SerialPortInfo(kind=_classify(fields), **fields)


def _is_relevant_serial_port(port: SerialPortInfo) -> bool:
    return port.kind != SERIAL or "VID:PID=" in port.hwid.upper()
