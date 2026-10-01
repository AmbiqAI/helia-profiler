"""Post-power terminal transport abstraction and registry."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..errors import PowerError
from ..results import PowerTerminalEnvelope
from ..vocab import Transport

if TYPE_CHECKING:
    from ..pipeline import PipelineContext


@runtime_checkable
class PowerTerminalTransport(Protocol):
    """Collect a post-run envelope without resetting the target."""

    transport: Transport

    def collect(self, ctx: PipelineContext, *, timeout_s: float) -> PowerTerminalEnvelope: ...


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
        from .power_terminal import collect_power_terminal_envelope_rtt

        if ctx.power_run is None or ctx.power_run.firmware is None or ctx.soc is None:
            raise PowerError("RTT terminal collection requires power firmware and platform state.")
        return collect_power_terminal_envelope_rtt(
            build_dir=ctx.power_run.firmware.build_dir,
            toolchain=ctx.config.target.toolchain,
            device=ctx.soc.jlink_device,
            jlink_serial=ctx.effective_jlink_serial,
            timeout_s=timeout_s,
        )


class _SerialByteStream(Protocol):
    """The slice of ``serial.Serial`` the serial terminal collectors need."""

    @property
    def in_waiting(self) -> int: ...

    def read(self, size: int = 1) -> bytes: ...


def _collect_serial_terminal(
    stream: _SerialByteStream, *, timeout_s: float
) -> PowerTerminalEnvelope:
    from .power_terminal import collect_power_terminal_envelope_from_chunks

    # read() blocks up to the port timeout when idle, so no extra poll sleep.
    return collect_power_terminal_envelope_from_chunks(
        lambda: stream.read(stream.in_waiting or 1), timeout_s=timeout_s, poll_interval_s=0.0
    )


class UartPowerTerminalTransport:
    transport = Transport.UART

    def collect(self, ctx: PipelineContext, *, timeout_s: float) -> PowerTerminalEnvelope:
        import serial

        from ..transport.uart import BAUD, find_jlink_vcom_port

        port = find_jlink_vcom_port(ctx.effective_jlink_serial)
        try:
            with serial.Serial(port=port, baudrate=BAUD, timeout=0.1) as stream:
                stream.reset_input_buffer()
                return _collect_serial_terminal(stream, timeout_s=timeout_s)
        except serial.SerialException as exc:
            raise PowerError(
                f"UART power terminal collection failed: {exc}",
                hint="Check the J-Link VCOM connection and that the port is not in use.",
            ) from exc


class SwoPowerTerminalTransport:
    transport = Transport.SWO

    def collect(self, ctx: PipelineContext, *, timeout_s: float) -> PowerTerminalEnvelope:
        from ..target.probe.jlink import attached_session
        from ..transport.swo import enable_swo, read_swo_chunk, swo_reference_clock_hz
        from .power_terminal import collect_power_terminal_envelope_from_chunks

        if ctx.soc is None:
            raise PowerError("SWO terminal collection requires resolved platform state.")
        cpu_speed_hz = swo_reference_clock_hz(ctx)

        with attached_session(
            device=ctx.soc.jlink_device,
            jlink_serial=ctx.effective_jlink_serial,
            attach_timeout_s=timeout_s,
        ) as jlink:
            try:
                enable_swo(jlink, cpu_speed_hz=cpu_speed_hz)
                return collect_power_terminal_envelope_from_chunks(
                    lambda: read_swo_chunk(jlink),
                    timeout_s=timeout_s,
                    poll_interval_s=0.001,
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

        from ..transport.usb_cdc import open_cdc_port, resolve_target_cdc_port
        from ..transport.usb_identity import usb_marker_serial

        port = resolve_target_cdc_port(
            usb_port=ctx.config.target.usb_port,
            marker=usb_marker_serial(ctx.effective_jlink_serial),
            timeout_s=timeout_s,
        )
        try:
            with open_cdc_port(port, timeout=0.1) as stream:
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
