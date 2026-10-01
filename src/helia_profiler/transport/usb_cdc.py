"""USB CDC capture transport — reads HPX output via TinyUSB serial port.

USB CDC provides reliable, flow-controlled data transfer using CRC-16
protected USB packets.  It requires the target board to have a USB
connection in addition to the SWD debug connection.

The ``nsx-usb`` module's Timer 3 polls ``tud_task()`` at 1 kHz.  During
PMU measurement, timer bracketing pauses/resumes Timer 3 to eliminate
ISR noise from the counters.

Sequence:
  1. Reset the target via SEGGER commander.
  2. Wait for USB CDC device to enumerate on the host.
  3. Open the serial port and assert DTR.
  4. Collect lines until ``--- HPX_END ---`` or timeout.
  5. Close the port.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Mapping

import serial  # pyserial

from ..errors import CaptureError
from ..target.probe.base import ResetController
from ..target.probe.jlink import JLinkResetController
from ..vocab import Transport
from ..wire import HPX_END_SENTINEL, HPX_START_SENTINEL
from .base import BaseCaptureTransport
from .ports import JLINK_VCOM, SerialPortInfo, list_serial_ports
from .protocol import (
    DEFAULT_TIMEOUT_S,
    HEARTBEAT_TIMEOUT_S,
    collect_lines,
)
from .timing import READINESS_POLL_INTERVAL_S, USB_REENUM_FLOOR_S, CaptureTimingTracker
from .usb_identity import USB_MARKER_PREFIX

log = logging.getLogger("hpx")

_ENUM_TIMEOUT_S = 15
_READ_CHUNK = 4096
BAUD = 115200  # CDC ignores baud, but pyserial requires a value


def _snapshot_cdc_ports() -> dict[str, SerialPortInfo]:
    """Return the host's USB serial ports keyed by device, from one enumeration."""
    return {port.device: port for port in list_serial_ports()}


def _is_foreign_hpx_port(port: SerialPortInfo, expected_marker: str | None) -> bool:
    """Return True when *port* advertises a *different* hpx marker.

    Every hpx-profiled board stamps ``HPX-<jlink_serial>`` into its CDC serial
    descriptor, so a device carrying some *other* ``HPX-*`` marker is provably a
    different board (e.g. another EVB still running its firmware).  Such a device
    must never be used as a heuristic fallback for this target, otherwise the
    capture opens the wrong board and blocks until the read timeout.
    """
    if not expected_marker:
        return False
    return (
        port.serial_number.startswith(USB_MARKER_PREFIX) and port.serial_number != expected_marker
    )


def _app_cdc_ports(
    ports: Mapping[str, SerialPortInfo], expected_marker: str | None
) -> list[SerialPortInfo]:
    """Return the candidate application CDC devices in *ports*, sorted by device.

    Drops SEGGER J-Link VCOMs and devices carrying another board's hpx marker.
    """
    return [
        ports[device]
        for device in sorted(ports)
        if ports[device].kind != JLINK_VCOM
        and not _is_foreign_hpx_port(ports[device], expected_marker)
    ]


def _find_port_by_marker(marker: str) -> str | None:
    """Return the CDC port whose USB serial-number descriptor equals *marker*.

    hpx stamps a unique ``iSerialNumber`` into the firmware's USB descriptor at
    build time, so an exact match identifies *this* board's CDC device — even
    when several Ambiq boards are attached.  pyserial exposes ``serial_number``
    from the descriptor on Linux, macOS, and Windows.
    """
    for port in _snapshot_cdc_ports().values():
        if port.serial_number == marker:
            return port.device
    return None


def _describe_port(port: SerialPortInfo) -> str:
    bits = [b for b in (port.manufacturer, port.product, port.serial_number) if b]
    return f"{port.device} ({', '.join(bits)})" if bits else port.device


def _ambiguous_cdc_error(candidates: list[SerialPortInfo]) -> CaptureError:
    listing = ", ".join(_describe_port(port) for port in candidates)
    return CaptureError(
        "Multiple application USB CDC devices are present and the target could "
        f"not be identified automatically: {listing}",
        hint=(
            "Another USB CDC board is connected. Rebuild so the firmware USB "
            "marker is applied, or pin the port explicitly with --usb-port "
            "(target.usb_port in YAML), e.g. --usb-port /dev/ttyACM1."
        ),
    )


def resolve_cdc_port(
    *,
    marker: str | None,
    pre_existing: set[str] | None = None,
    timeout_s: float = _ENUM_TIMEOUT_S,
) -> str:
    """Locate the target's USB CDC port after a reset.

    Selection order:
      1. The CDC device whose USB serial-number descriptor equals *marker*
         (stamped into the firmware by hpx) — authoritative and unambiguous.
      2. Heuristic fallback: a freshly enumerated, single non-J-Link CDC device
         (see :func:`_find_cdc_port`).
    """
    time.sleep(USB_REENUM_FLOOR_S)

    if marker:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            port = _find_port_by_marker(marker)
            if port is not None:
                log.info("Matched USB CDC device by marker %r -> %s", marker, port)
                return port
            time.sleep(READINESS_POLL_INTERVAL_S)
        log.warning(
            "No USB CDC device advertised the expected marker %r within %.1fs; "
            "falling back to heuristic detection.",
            marker,
            timeout_s,
        )

    return _find_cdc_port(
        pre_existing=pre_existing,
        timeout_s=timeout_s if not marker else 0,
        expected_marker=marker,
    )


def resolve_target_cdc_port(
    *,
    usb_port: str | None,
    marker: str | None,
    timeout_s: float = _ENUM_TIMEOUT_S,
) -> str:
    """Return the pinned *usb_port*, else :func:`resolve_cdc_port` by *marker*.

    A pinned port is returned at once, without the re-enumeration floor; a
    caller that has just reset the target must wait that out itself.
    """
    if usb_port is not None:
        return usb_port
    return resolve_cdc_port(marker=marker, timeout_s=timeout_s)


def open_cdc_port(port: str, *, timeout: float | None) -> serial.Serial:
    """Open *port* with DTR asserted, which releases ``nsx_usb_connected()``.

    The firmware spins until the host opens its CDC port and raises DTR.
    """
    ser = serial.Serial(port=port, baudrate=BAUD, timeout=timeout, dsrdtr=True)
    try:
        ser.dtr = True
    except BaseException:
        ser.close()
        raise
    return ser


def _find_cdc_port(
    pre_existing: set[str] | None = None,
    timeout_s: float = _ENUM_TIMEOUT_S,
    expected_marker: str | None = None,
) -> str:
    """Wait for a non-J-Link USB CDC device and return its path.

    *pre_existing* lets callers prefer a freshly enumerated device over ports
    that were already present (e.g. another board's CDC).  When the wait
    expires the current non-J-Link devices are weighed: exactly one is used,
    more than one is rejected as ambiguous (the caller should rely on the
    firmware marker or pass an explicit ``--usb-port``), and none raises.

    *expected_marker* (when known) drops any device advertising a *different*
    ``HPX-*`` marker, so a stale CDC device from another attached board is never
    mistaken for this target.
    """
    deadline = time.monotonic() + timeout_s
    if pre_existing is None:
        pre_existing = set()

    while time.monotonic() < deadline:
        present = _snapshot_cdc_ports()
        new_ports = _app_cdc_ports(
            {device: port for device, port in present.items() if device not in pre_existing},
            expected_marker,
        )
        if len(new_ports) == 1:
            log.info("Found new USB CDC port: %s", new_ports[0].device)
            return new_ports[0].device
        time.sleep(0.5)

    # No single fresh device appeared — fall back to currently present
    # non-J-Link devices. Refuse to open SEGGER VCOM, which only causes a long
    # timeout and hides the real enumeration failure.  Also refuse a CDC device
    # that advertises a different board's hpx marker.
    present = _snapshot_cdc_ports()
    candidates = _app_cdc_ports(present, expected_marker)
    if len(candidates) == 1:
        log.warning(
            "No new USB CDC device appeared; using the only application CDC device present: %s",
            candidates[0].device,
        )
        return candidates[0].device
    if len(candidates) > 1:
        raise _ambiguous_cdc_error(candidates)

    foreign = [port for port in present.values() if _is_foreign_hpx_port(port, expected_marker)]
    if foreign:
        listing = ", ".join(_describe_port(port) for port in foreign)
        raise CaptureError(
            "No application USB CDC device appeared after reset; only CDC devices "
            f"stamped for another board are visible: {listing}",
            hint=(
                f"Those devices do not carry this board's marker {expected_marker!r}. "
                "Check this board's USB data connection and that nsx_usb is "
                "enumerating, or pin the port with --usb-port."
            ),
        )

    if present:
        raise CaptureError(
            "No application USB CDC device appeared after reset",
            hint=(
                "Only SEGGER/J-Link serial ports are visible on the host. "
                "Check the board USB data connection and that nsx_usb is "
                "enumerating the target CDC device."
            ),
        )

    raise CaptureError(
        f"No USB CDC device found within {timeout_s}s",
        hint=(
            "Ensure the board is connected via USB and the firmware "
            "initialises nsx_usb.  Run 'hpx ports list --all' to see every "
            "serial port the host enumerates."
        ),
    )


def capture_usb_output(
    *,
    jlink_serial: str | None = None,
    jlink_device: str,
    timeout_s: float | None = DEFAULT_TIMEOUT_S,
    heartbeat_timeout_s: float = HEARTBEAT_TIMEOUT_S,
    usb_port: str | None = None,
    usb_marker: str | None = None,
    keep_attached: bool = False,
    timing_out: dict[str, float] | None = None,
    reset_controller: ResetController | None = None,
) -> list[str]:
    """Capture firmware output via USB CDC until HPX_END or timeout.

    USB CDC provides CRC-protected, flow-controlled delivery.  The
    firmware waits for DTR assertion before printing, so there is no
    fixed startup delay.

    When *keep_attached* is set, a pylink debugger session is held open for the
    whole capture (reset+go through pylink) instead of releasing the probe.
    This is required on SoCs that gate the DWT cycle counter behind the debug
    power domain (Apollo4) — see
    :func:`~helia_profiler.target.probe.jlink.attached_reset_session`.

    *timeout_s* is the absolute capture ceiling (``None`` = unbounded) and
    *heartbeat_timeout_s* the max gap between received lines.

    Returns:
        List of captured text lines.
    """
    timing = CaptureTimingTracker(start_marker=HPX_START_SENTINEL, end_marker=HPX_END_SENTINEL)
    pre_existing = set(_snapshot_cdc_ports())
    log.info("Pre-existing CDC ports: %s", sorted(pre_existing) or "(none)")

    # --- Step 1: reset the target ---
    ser: serial.Serial | None = None
    # On SoCs that gate the DWT cycle counter behind the debug power domain
    # (Apollo4), a debugger must stay attached for the whole capture or every
    # per-layer cycle reads back 0.  Hold the pylink session open across reset,
    # re-enumeration, and the read; it is released in the finally block.
    reset_stack = contextlib.ExitStack()
    controller = reset_controller or JLinkResetController()

    try:
        if keep_attached:
            reset_stack.enter_context(
                controller.attached_reset_session(device=jlink_device, jlink_serial=jlink_serial)
            )
        else:
            controller.debug_reset(device=jlink_device, jlink_serial=jlink_serial)

        # --- Step 2: locate the target's USB CDC port ---
        # An explicit --usb-port always wins.  Otherwise prefer the unique USB
        # serial-number marker that hpx stamped into this build's descriptor: it
        # identifies *this* board unambiguously even when several Ambiq boards
        # are attached.  Fall back to host heuristics only when no marker is
        # available or it never enumerates.
        if usb_port is not None:
            port = usb_port
            log.info("Using pinned USB CDC port: %s", port)
            time.sleep(USB_REENUM_FLOOR_S)
        else:
            port = resolve_cdc_port(marker=usb_marker, pre_existing=pre_existing)

        log.info("Opening USB CDC port: %s", port)
        ser = opened = open_cdc_port(port, timeout=0)
        opened.reset_input_buffer()

        def read_fn() -> bytes:
            return opened.read(opened.in_waiting or _READ_CHUNK)

        lines = collect_lines(
            read_fn,
            transport_name="USB CDC",
            overall_timeout_s=timeout_s,
            heartbeat_timeout_s=heartbeat_timeout_s,
            on_line=timing.observe_line,
        )
    except CaptureError:
        raise
    except serial.SerialException as exc:
        raise CaptureError(
            f"USB CDC serial error: {exc}",
            hint="Check USB cable connection and that the port is not in use.",
        ) from exc
    except Exception as exc:
        raise CaptureError(
            f"USB CDC capture error: {exc}",
            hint="Check USB connection to the board.",
        ) from exc
    finally:
        if ser is not None and ser.is_open:
            ser.close()
        reset_stack.close()

    timing.finalize(timing_out)
    return lines


class UsbCdcTransport(BaseCaptureTransport):
    """USB CDC capture backend.

    Forwards the SoC keep-attached requirement so the probe is held across
    capture on SoCs that gate DWT->CYCCNT behind the debug power domain
    (Apollo3/Apollo4).  ``collect`` runs :func:`capture_usb_output`, which owns
    its own reset.
    """

    transport = Transport.USB_CDC

    def collect(self, ctx) -> list[str]:
        from .usb_identity import usb_marker_serial

        args = self.prepared_args
        return capture_usb_output(
            jlink_serial=args.jlink_serial,
            jlink_device=args.jlink_device,
            usb_port=ctx.config.target.usb_port,
            usb_marker=usb_marker_serial(args.jlink_serial),
            timeout_s=args.overall_timeout_s,
            heartbeat_timeout_s=args.heartbeat_timeout_s,
            keep_attached=args.keep_debugger_attached,
            timing_out=args.timing_raw,
            reset_controller=args.reset_controller,
        )
