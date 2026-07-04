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
  3. Reset the target via JLinkExe.
  4. Collect lines until ``--- HPX_END ---`` or timeout.
  5. Close the port.

Caveat: UART at 115200 baud has no flow control (~11.5 KB/s).  For large
captures prefer RTT; UART is the fallback for boards without USB CDC.
"""

from __future__ import annotations

import logging
import time

import serial  # pyserial
from serial.tools import list_ports

from ..errors import CaptureError
from ..jlink import reset_target
from .readiness import attached_reset_session
from .transport import (
    DEFAULT_TIMEOUT_S,
    HEARTBEAT_TIMEOUT_S,
    HPX_END,
    HPX_START,
    collect_lines,
)

log = logging.getLogger("hpx")

_BAUD = 115200  # firmware COM UART runs 115200 8N1, no flow control
_JLINK_MARKERS = ("segger", "j-link")
_READ_CHUNK = 4096


def _norm(value: str | None) -> str:
    """Lowercased alphanumeric-only form of *value* for tolerant matching."""
    return "".join(ch for ch in (value or "") if ch.isalnum()).lower()


def _is_jlink_vcom(info: object) -> bool:
    """True if *info* describes a SEGGER J-Link virtual COM port."""
    haystack = _norm(
        " ".join(
            str(getattr(info, attr, "") or "")
            for attr in ("hwid", "manufacturer", "product", "description")
        )
    )
    return any(_norm(marker) in haystack for marker in _JLINK_MARKERS)


def _find_jlink_vcom_port(jlink_serial: str | None) -> str:
    """Locate the J-Link VCOM serial device for *jlink_serial*.

    When a probe serial is known, prefer the VCOM whose descriptor carries
    that serial so the correct board is selected with several probes
    attached.  Otherwise fall back to the single J-Link VCOM present.
    """
    vcom_ports = [info for info in list_ports.comports() if _is_jlink_vcom(info)]

    if jlink_serial:
        target = _norm(jlink_serial)
        matched = [
            info
            for info in vcom_ports
            if target in _norm(getattr(info, "serial_number", ""))
            or target in _norm(getattr(info, "hwid", ""))
        ]
        if len(matched) == 1:
            log.info("Found J-Link VCOM for probe %s: %s", jlink_serial, matched[0].device)
            return matched[0].device
        if len(matched) > 1:
            listing = ", ".join(info.device for info in matched)
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
                "its VCOM interface is enabled. Check 'ls /dev/ttyACM*'."
            ),
        )

    listing = ", ".join(info.device for info in vcom_ports)
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
            :func:`~helia_profiler.capture.readiness.attached_reset_session`.
        timing_out: Optional dict populated with capture-timing telemetry.

    Returns:
        List of captured text lines.
    """
    capture_started_s = time.monotonic()
    hpx_start_s: float | None = None
    hpx_end_s: float | None = None

    def on_line(line: str, line_ts: float) -> None:
        nonlocal hpx_start_s, hpx_end_s
        if line == HPX_START and hpx_start_s is None:
            hpx_start_s = line_ts
        elif line == HPX_END:
            hpx_end_s = line_ts

    port = _find_jlink_vcom_port(jlink_serial)

    log.info("Opening J-Link VCOM port: %s @ %d 8N1", port, _BAUD)
    ser: serial.Serial | None = None
    try:
        ser = serial.Serial(port=port, baudrate=_BAUD, timeout=0)
        ser.reset_input_buffer()

        def read_fn() -> bytes:
            waiting = ser.in_waiting
            return ser.read(waiting if waiting else _READ_CHUNK)

        def _collect() -> list[str]:
            ser.reset_input_buffer()
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
            with attached_reset_session(
                device=jlink_device, jlink_serial=jlink_serial
            ):
                lines = _collect()
        else:
            reset_target(device=jlink_device, jlink_serial=jlink_serial)
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
        timing_out["capture_duration_s"] = time.monotonic() - capture_started_s
        if hpx_start_s is not None:
            timing_out["hpx_start_latency_s"] = hpx_start_s - capture_started_s
        if hpx_start_s is not None and hpx_end_s is not None:
            timing_out["protocol_duration_s"] = hpx_end_s - hpx_start_s

    return lines
