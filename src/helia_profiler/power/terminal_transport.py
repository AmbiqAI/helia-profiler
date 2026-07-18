"""Post-power terminal transport abstraction and registry."""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

from ..artifacts import PowerTerminalEnvelope
from ..config import Transport
from ..errors import PowerError
from ..pipeline import PipelineContext


@runtime_checkable
class PowerTerminalTransport(Protocol):
    """Collect a post-run envelope without resetting the target."""

    transport: Transport

    def collect(self, ctx: PipelineContext, *, timeout_s: float) -> PowerTerminalEnvelope:
        ...


_TERMINAL_TRANSPORTS: dict[Transport, type[PowerTerminalTransport]] = {}


def register_power_terminal_transport(
    transport: Transport,
    implementation: type[PowerTerminalTransport],
    *,
    replace: bool = False,
) -> None:
    declared = getattr(implementation, "transport", None)
    if declared is not transport:
        raise ValueError(
            f"Terminal adapter declares {declared!r}, cannot register for {transport.value}."
        )
    if transport in _TERMINAL_TRANSPORTS and not replace:
        raise ValueError(f"Terminal adapter already registered for {transport.value}.")
    _TERMINAL_TRANSPORTS[transport] = implementation


def get_power_terminal_transport(transport: Transport) -> PowerTerminalTransport:
    implementation = _TERMINAL_TRANSPORTS.get(transport)
    if implementation is None:
        raise PowerError(
            f"Post-GATE terminal collection is not implemented for {transport.value}.",
            hint="Use RTT or implement a PowerTerminalTransport adapter for this transport.",
        )
    return implementation()


class RttPowerTerminalTransport:
    transport = Transport.RTT

    def collect(self, ctx: PipelineContext, *, timeout_s: float) -> PowerTerminalEnvelope:
        from ..capture.power_terminal import collect_power_terminal_envelope_rtt

        if ctx.power_run is None or ctx.power_run.firmware is None or ctx.soc is None:
            raise PowerError("RTT terminal collection requires power firmware and platform state.")
        return collect_power_terminal_envelope_rtt(
            build_dir=ctx.power_run.firmware.build_dir,
            toolchain=ctx.config.target.toolchain,
            device=ctx.soc.jlink_device,
            jlink_serial=ctx.resolved_jlink_serial or ctx.config.target.jlink_serial,
            timeout_s=timeout_s,
        )


def _collect_serial_terminal(
    serial_port: object,
    *,
    timeout_s: float,
) -> PowerTerminalEnvelope:
    from ..capture.power_terminal import (
        POWER_TERMINAL_END,
        POWER_TERMINAL_START,
        parse_power_terminal_envelope,
    )

    deadline = time.monotonic() + timeout_s
    lines: list[str] = []
    in_record = False
    last_error: PowerError | None = None
    while time.monotonic() < deadline:
        raw = serial_port.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").strip()
        if line == POWER_TERMINAL_START:
            lines = [line]
            in_record = True
            continue
        if not in_record:
            continue
        lines.append(line)
        if line == POWER_TERMINAL_END:
            try:
                return parse_power_terminal_envelope(lines)
            except PowerError as exc:
                last_error = exc
                lines = []
                in_record = False
    if last_error is not None:
        raise PowerError(
            f"No valid power terminal record received within {timeout_s:.1f}s: {last_error}"
        ) from last_error
    raise PowerError(
        f"No complete power terminal record received within {timeout_s:.1f}s."
    )


class UartPowerTerminalTransport:
    transport = Transport.UART

    def collect(self, ctx: PipelineContext, *, timeout_s: float) -> PowerTerminalEnvelope:
        import serial

        from ..transport.uart import _BAUD, _find_jlink_vcom_port

        port = _find_jlink_vcom_port(
            ctx.resolved_jlink_serial or ctx.config.target.jlink_serial
        )
        try:
            with serial.Serial(port=port, baudrate=_BAUD, timeout=0.1) as stream:
                stream.reset_input_buffer()
                return _collect_serial_terminal(stream, timeout_s=timeout_s)
        except serial.SerialException as exc:
            raise PowerError(
                f"UART power terminal collection failed: {exc}",
                hint="Check the J-Link VCOM connection and that the port is not in use.",
            ) from exc


def _collect_chunked_terminal(read_fn: object, *, timeout_s: float) -> PowerTerminalEnvelope:
    from ..capture.power_terminal import (
        POWER_TERMINAL_END,
        POWER_TERMINAL_START,
        parse_power_terminal_envelope,
    )

    deadline = time.monotonic() + timeout_s
    buffer = bytearray()
    last_error: PowerError | None = None
    while time.monotonic() < deadline:
        chunk = read_fn()
        if not chunk:
            time.sleep(0.001)
            continue
        buffer.extend(chunk)
        text = buffer.decode("utf-8", errors="replace")
        start = text.rfind(POWER_TERMINAL_START)
        if start < 0:
            continue
        end = text.find(POWER_TERMINAL_END, start)
        if end < 0:
            continue
        end += len(POWER_TERMINAL_END)
        try:
            return parse_power_terminal_envelope(text[start:end].splitlines())
        except PowerError as exc:
            last_error = exc
            del buffer[:end]
    if last_error is not None:
        raise PowerError(
            f"No valid power terminal record received within {timeout_s:.1f}s: {last_error}"
        ) from last_error
    raise PowerError(
        f"No complete power terminal record received within {timeout_s:.1f}s."
    )


class SwoPowerTerminalTransport:
    transport = Transport.SWO

    def collect(self, ctx: PipelineContext, *, timeout_s: float) -> PowerTerminalEnvelope:
        from ..target.probe.jlink import attached_session

        if ctx.soc is None or ctx.run_metadata.platform is None:
            raise PowerError("SWO terminal collection requires resolved platform clocks.")
        cpu_clock_mhz = ctx.run_metadata.platform.cpu_clock_mhz
        swo_ref_mhz = ctx.soc.swo_trace_clock_mhz or cpu_clock_mhz
        if swo_ref_mhz <= 0:
            raise PowerError("SWO terminal collection requires a resolved trace clock.")

        with attached_session(
            device=ctx.soc.jlink_device,
            jlink_serial=ctx.resolved_jlink_serial or ctx.config.target.jlink_serial,
            attach_timeout_s=timeout_s,
        ) as jlink:
            try:
                jlink.swo_enable(
                    cpu_speed=swo_ref_mhz * 1_000_000,
                    swo_speed=1_000_000,
                    port_mask=0x01,
                )
                return _collect_chunked_terminal(
                    lambda: bytes(jlink.swo_read_stimulus(0, 4096)),
                    timeout_s=timeout_s,
                )
            finally:
                try:
                    jlink.swo_stop()
                except Exception:
                    pass


class UsbCdcPowerTerminalTransport:
    transport = Transport.USB_CDC

    def collect(self, ctx: PipelineContext, *, timeout_s: float) -> PowerTerminalEnvelope:
        import serial

        from ..transport.usb_cdc import _BAUD, _resolve_cdc_port
        from ..usb_identity import usb_marker_serial

        marker = usb_marker_serial(
            ctx.resolved_jlink_serial or ctx.config.target.jlink_serial
        )
        port = (
            ctx.config.target.usb_port
            if ctx.config.target.usb_port is not None
            else _resolve_cdc_port(marker=marker, timeout_s=timeout_s)
        )
        try:
            with serial.Serial(
                port=port,
                baudrate=_BAUD,
                timeout=0.1,
                dsrdtr=True,
            ) as stream:
                stream.dtr = True
                return _collect_serial_terminal(stream, timeout_s=timeout_s)
        except serial.SerialException as exc:
            raise PowerError(
                f"USB CDC power terminal collection failed: {exc}",
                hint="Check target USB enumeration and that the CDC port is not in use.",
            ) from exc


register_power_terminal_transport(Transport.RTT, RttPowerTerminalTransport)
register_power_terminal_transport(Transport.UART, UartPowerTerminalTransport)
register_power_terminal_transport(Transport.SWO, SwoPowerTerminalTransport)
register_power_terminal_transport(Transport.USB_CDC, UsbCdcPowerTerminalTransport)


__all__ = [
    "PowerTerminalTransport",
    "RttPowerTerminalTransport",
    "SwoPowerTerminalTransport",
    "UartPowerTerminalTransport",
    "UsbCdcPowerTerminalTransport",
    "get_power_terminal_transport",
    "register_power_terminal_transport",
]
