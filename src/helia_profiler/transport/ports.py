"""Typed host serial-port discovery for interactive and CLI use."""

from __future__ import annotations

from dataclasses import dataclass


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
    """Return host serial ports, filtering unrelated devices by default."""
    from serial.tools import list_ports

    ports = tuple(_describe_serial_port(info) for info in list_ports.comports())
    if include_all:
        return ports
    return tuple(port for port in ports if _is_relevant_serial_port(port))


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
    text = " ".join(fields.values()).lower()
    if "segger" in text or "j-link" in text or "jlink" in text:
        kind = "jlink-vcom"
    elif fields["serial_number"].startswith("HPX-"):
        kind = "hpx-usb-cdc"
    else:
        kind = "serial"
    return SerialPortInfo(kind=kind, **fields)


def _is_relevant_serial_port(port: SerialPortInfo) -> bool:
    if port.kind in ("jlink-vcom", "hpx-usb-cdc"):
        return True
    return any(token in port.device for token in ("ttyACM", "ttyUSB", "tty.usbmodem"))
