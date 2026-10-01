"""UART capture transport — reads HPX output via the J-Link OB VCOM.

Apollo EVBs bridge an on-board COM UART to the SEGGER J-Link OB virtual
COM port.  The firmware retargets ``nsx_printf`` to that UART
(``NSX_DEBUG_UART`` / ``am_bsp_uart_printf_enable``), so profiling output
appears on the host as the J-Link VCOM serial device — no target USB
device stack and no SWO pin required.  This is the path that unblocks
boards (e.g. Apollo3) where ``nsx-ambiq-usb`` has no TinyUSB DCD port.

The VCOM port is provided by the J-Link probe itself and is present
regardless of target state, so capture opens it *before* resetting the
target to avoid racing the firmware's boot-time attach delay.

Sequence:
  1. Locate the J-Link VCOM serial port for this probe.
  2. Open the port (115200 8N1) and flush stale bytes.
  3. Reset the target via SEGGER commander.
  4. Collect lines until ``--- HPX_END ---`` or timeout.
  5. Close the port.

Caveat: UART at 115200 baud has no flow control (~11.5 KB/s).  For large
captures prefer RTT; UART is the fallback for boards without USB CDC.
"""

from __future__ import annotations

import logging

import serial  # pyserial

from ..errors import CaptureError
from ..target.probe.base import ResetController
from ..target.probe.jlink import JLinkResetController
from ..vocab import Transport
from ..wire import HPX_END_SENTINEL, HPX_START_SENTINEL
from .base import BaseCaptureTransport
from .ports import JLINK_VCOM, list_serial_ports, normalize_descriptor
from .protocol import (
    DEFAULT_TIMEOUT_S,
    HEARTBEAT_TIMEOUT_S,
    collect_lines,
)
from .timing import CaptureTimingTracker

log = logging.getLogger("hpx")

BAUD = 115200  # firmware COM UART runs 115200 8N1, no flow control
_READ_CHUNK = 4096


def find_jlink_vcom_port(jlink_serial: str | None) -> str:
    """Locate the J-Link VCOM serial device for *jlink_serial*.

    When a probe serial is known, prefer the VCOM whose descriptor carries
    that serial so the correct board is selected with several probes
    attached.  Otherwise fall back to the single J-Link VCOM present.
    """
    vcom_ports = [port for port in list_serial_ports() if port.kind == JLINK_VCOM]

    if jlink_serial:
        target = normalize_descriptor(jlink_serial)
        matched = [
            port
            for port in vcom_ports
            if target in normalize_descriptor(port.serial_number)
            or target in normalize_descriptor(port.hwid)
        ]
        if len(matched) == 1:
            log.info("Found J-Link VCOM for probe %s: %s", jlink_serial, matched[0].device)
            return matched[0].device
        if len(matched) > 1:
            listing = ", ".join(port.device for port in matched)
            raise CaptureError(
                f"Multiple J-Link VCOM ports match probe serial {jlink_serial}: {listing}",
                hint="Disconnect the duplicate probe or pin the port explicitly.",
            )

    if len(vcom_ports) == 1:
        log.info("Using the only J-Link VCOM port present: %s", vcom_ports[0].device)
        return vcom_ports[0].device

    if not vcom_ports:
        raise CaptureError(
            "No SEGGER J-Link virtual COM port found on the host.",
            hint=(
                "The UART transport reads firmware output from the J-Link OB "
                "VCOM. Ensure the board's J-Link probe is connected and that "
                "its VCOM interface is enabled. Run 'hpx ports list --all' to "
                "see every serial port the host enumerates."
            ),
        )

    listing = ", ".join(port.device for port in vcom_ports)
    raise CaptureError(
        f"Multiple J-Link VCOM ports present and no probe serial to disambiguate: {listing}",
        hint="Pass --jlink-serial to select the probe whose VCOM to read.",
    )


def capture_uart_output(
    *,
    jlink_serial: str | None = None,
    jlink_device: str,
    timeout_s: float | None = DEFAULT_TIMEOUT_S,
    heartbeat_timeout_s: float = HEARTBEAT_TIMEOUT_S,
    keep_attached: bool = False,
    timing_out: dict[str, float] | None = None,
    reset_controller: ResetController | None = None,
) -> list[str]:
    """Capture firmware output via the J-Link OB VCOM UART.

    Args:
        jlink_serial: Probe serial used to select the matching VCOM port.
        jlink_device: J-Link device string for the reset command.
        timeout_s: Absolute capture ceiling (``None`` = unbounded).
        heartbeat_timeout_s: Max gap between received lines before giving up.
        keep_attached: Hold a pylink debugger attached for the whole capture
            (reset+go via pylink) instead of releasing the probe.  Required on
            SoCs that gate the DWT cycle counter behind the debug power domain
            (Apollo4) or per-layer cycles read back as 0.  See
            :func:`~helia_profiler.target.probe.jlink.attached_reset_session`.
        timing_out: Optional dict populated with capture-timing telemetry.

    Returns:
        List of captured text lines.
    """
    timing = CaptureTimingTracker(start_marker=HPX_START_SENTINEL, end_marker=HPX_END_SENTINEL)
    on_line = timing.observe_line

    port = find_jlink_vcom_port(jlink_serial)
    controller = reset_controller or JLinkResetController()

    log.info("Opening J-Link VCOM port: %s @ %d 8N1", port, BAUD)
    ser: serial.Serial | None = None
    try:
        ser = serial.Serial(port=port, baudrate=BAUD, timeout=0)
        ser.reset_input_buffer()

        def read_fn() -> bytes:
            waiting = ser.in_waiting
            return ser.read(waiting if waiting else _READ_CHUNK)

        def _collect() -> list[str]:
            return collect_lines(
                read_fn,
                transport_name="UART",
                overall_timeout_s=timeout_s,
                heartbeat_timeout_s=heartbeat_timeout_s,
                on_line=on_line,
            )

        # Reset the target only after the VCOM is open so the firmware's
        # boot-time attach delay cannot outrun the host.
        if keep_attached:
            # The Cortex-M4F families (Apollo3/3P and Apollo4/4P) gate
            # DWT->CYCCNT behind the debug power domain, which only stays
            # powered while a debugger is attached.  Hold a pylink session open
            # across the capture instead of releasing the probe, or every
            # per-layer cycle reads back 0.
            with controller.attached_reset_session(device=jlink_device, jlink_serial=jlink_serial):
                lines = _collect()
        else:
            controller.debug_reset(device=jlink_device, jlink_serial=jlink_serial)
            lines = _collect()
    except CaptureError:
        raise
    except serial.SerialException as exc:
        raise CaptureError(
            f"UART serial error: {exc}",
            hint="Check the J-Link USB connection and that the VCOM port is not in use.",
        ) from exc
    finally:
        if ser is not None and ser.is_open:
            ser.close()

    if timing_out is not None:
        timing.finalize(timing_out)

    return lines


class UartTransport(BaseCaptureTransport):
    """UART (J-Link OB VCOM) capture backend.

    Forwards the SoC keep-attached requirement so the probe is held across
    capture on SoCs that gate DWT->CYCCNT behind the debug power domain
    (Apollo3/Apollo4).  ``collect`` runs :func:`capture_uart_output`, which owns
    its own reset.
    """

    transport = Transport.UART

    def collect(self, ctx) -> list[str]:
        args = self.prepared_args
        return capture_uart_output(
            jlink_serial=args.jlink_serial,
            jlink_device=args.jlink_device,
            timeout_s=args.overall_timeout_s,
            heartbeat_timeout_s=args.heartbeat_timeout_s,
            keep_attached=args.keep_debugger_attached,
            timing_out=args.timing_raw,
            reset_controller=args.reset_controller,
        )
