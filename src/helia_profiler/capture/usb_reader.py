"""USB CDC capture transport — reads HPX output via TinyUSB serial port.

USB CDC provides reliable, flow-controlled data transfer using CRC-16
protected USB packets.  It requires the target board to have a USB
connection in addition to the SWD debug connection.

The ``nsx-usb`` module's Timer 3 polls ``tud_task()`` at 1 kHz.  During
PMU measurement, timer bracketing pauses/resumes Timer 3 to eliminate
ISR noise from the counters.

Sequence:
  1. Reset the target via JLinkExe.
  2. Wait for USB CDC device to enumerate on the host.
  3. Open the serial port and assert DTR.
  4. Collect lines until ``--- HPX_END ---`` or timeout.
  5. Close the port.
"""

from __future__ import annotations

import contextlib
import glob
import logging
import time

import serial  # pyserial
from serial.tools import list_ports

from ..errors import CaptureError
from ..jlink import reset_target
from ..usb_identity import USB_MARKER_PREFIX
from .readiness import attached_reset_session
from .timing import READINESS_POLL_INTERVAL_S, USB_REENUM_FLOOR_S
from .transport import DEFAULT_TIMEOUT_S, HPX_END, HPX_START, LINE_TIMEOUT_S

log = logging.getLogger("hpx")

_ENUM_TIMEOUT_S = 15  # max time to wait for USB enumeration
_BAUD = 115200  # CDC ignores baud, but pyserial requires a value
_CDC_PATTERNS = ["/dev/tty.usbmodem*", "/dev/ttyACM*"]
_JLINK_MARKERS = ("segger", "j-link")


def _snapshot_cdc_ports() -> set[str]:
    """Return set of currently-visible CDC serial ports."""
    ports: set[str] = set()
    for pat in _CDC_PATTERNS:
        ports.update(glob.glob(pat))
    return ports


def _is_jlink_port(port: str) -> bool:
    """Return True when a serial port belongs to the SEGGER J-Link VCOM."""
    for info in list_ports.comports():
        if info.device != port:
            continue
        fields = [
            info.manufacturer,
            info.product,
            info.description,
            info.interface,
            info.hwid,
        ]
        text = " ".join(field for field in fields if field).lower()
        return any(marker in text for marker in _JLINK_MARKERS)
    return False


def _app_cdc_ports(ports: set[str] | None = None) -> list[str]:
    """Return the non-J-Link CDC ports (candidate application devices), sorted."""
    if ports is None:
        ports = _snapshot_cdc_ports()
    return sorted(port for port in ports if not _is_jlink_port(port))


def _port_serial_number(port: str) -> str:
    """Return the USB iSerialNumber descriptor for *port* (empty if unknown)."""
    for info in list_ports.comports():
        if info.device == port:
            return info.serial_number or ""
    return ""


def _is_foreign_hpx_port(port: str, expected_marker: str | None) -> bool:
    """Return True when *port* advertises a *different* hpx marker.

    Every hpx-profiled board stamps ``HPX-<jlink_serial>`` into its CDC serial
    descriptor, so a device carrying some *other* ``HPX-*`` marker is provably a
    different board (e.g. another EVB still running its firmware).  Such a device
    must never be used as a heuristic fallback for this target, otherwise the
    capture opens the wrong board and blocks until the read timeout.
    """
    if not expected_marker:
        return False
    serial = _port_serial_number(port)
    return serial.startswith(USB_MARKER_PREFIX) and serial != expected_marker


def _drop_foreign_hpx_ports(
    ports: list[str], expected_marker: str | None
) -> list[str]:
    """Drop ports that belong to a *different* hpx board from *ports*."""
    return [p for p in ports if not _is_foreign_hpx_port(p, expected_marker)]


def _find_port_by_marker(marker: str) -> str | None:
    """Return the CDC port whose USB serial-number descriptor equals *marker*.

    hpx stamps a unique ``iSerialNumber`` into the firmware's USB descriptor at
    build time, so an exact match identifies *this* board's CDC device — even
    when several Ambiq boards are attached.  pyserial exposes ``serial_number``
    from the descriptor on Linux, macOS, and Windows.
    """
    for info in list_ports.comports():
        if (info.serial_number or "") == marker:
            return info.device
    return None


def _describe_port(port: str) -> str:
    """Return a human-readable description of *port* for diagnostics."""
    for info in list_ports.comports():
        if info.device == port:
            bits = [b for b in (info.manufacturer, info.product, info.serial_number) if b]
            return f"{port} ({', '.join(bits)})" if bits else port
    return port


def _ambiguous_cdc_error(candidates: list[str]) -> CaptureError:
    """Build the error raised when the target CDC port cannot be disambiguated."""
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


def _resolve_cdc_port(
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
        new_ports = _drop_foreign_hpx_ports(
            _app_cdc_ports(_snapshot_cdc_ports() - pre_existing), expected_marker
        )
        if len(new_ports) == 1:
            log.info("Found new USB CDC port: %s", new_ports[0])
            return new_ports[0]
        time.sleep(0.5)

    # No single fresh device appeared — fall back to currently present
    # non-J-Link devices. Refuse to open SEGGER VCOM, which only causes a long
    # timeout and hides the real enumeration failure.  Also refuse a CDC device
    # that advertises a different board's hpx marker.
    candidates = _drop_foreign_hpx_ports(_app_cdc_ports(), expected_marker)
    if len(candidates) == 1:
        log.warning(
            "No new USB CDC device appeared; using the only application CDC "
            "device present: %s",
            candidates[0],
        )
        return candidates[0]
    if len(candidates) > 1:
        raise _ambiguous_cdc_error(candidates)

    if _snapshot_cdc_ports():
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
            "initialises nsx_usb.  Check 'ls /dev/tty.usbmodem*'."
        ),
    )


def capture_usb_output(
    *,
    build_dir: None = None,  # unused — kept for interface parity with SWO
    jlink_serial: str | None = None,
    jlink_device: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    usb_port: str | None = None,
    usb_marker: str | None = None,
    keep_attached: bool = False,
    timing_out: dict[str, float] | None = None,
) -> list[str]:
    """Capture firmware output via USB CDC until HPX_END or timeout.

    USB CDC provides CRC-protected, flow-controlled delivery.  The
    firmware waits for DTR assertion before printing, so there is no
    fixed startup delay.

    When *keep_attached* is set, a pylink debugger session is held open for the
    whole capture (reset+go through pylink) instead of releasing the probe.
    This is required on SoCs that gate the DWT cycle counter behind the debug
    power domain (Apollo4) — see
    :func:`~helia_profiler.capture.readiness.attached_reset_session`.

    Returns:
        List of captured text lines.
    """
    capture_started_s = time.monotonic()
    hpx_start_s: float | None = None
    hpx_end_s: float | None = None

    def finalize_timing() -> None:
        if timing_out is None:
            return
        timing_out["capture_duration_s"] = time.monotonic() - capture_started_s
        if hpx_start_s is not None:
            timing_out["hpx_start_latency_s"] = hpx_start_s - capture_started_s
        if hpx_start_s is not None and hpx_end_s is not None:
            timing_out["protocol_duration_s"] = hpx_end_s - hpx_start_s

    # --- Step 0: snapshot existing CDC ports before reset ---
    pre_existing = _snapshot_cdc_ports()
    log.info("Pre-existing CDC ports: %s", sorted(pre_existing) or "(none)")

    # --- Step 1: reset the target ---
    ser: serial.Serial | None = None
    lines: list[str] = []
    # On SoCs that gate the DWT cycle counter behind the debug power domain
    # (Apollo4), a debugger must stay attached for the whole capture or every
    # per-layer cycle reads back 0.  Hold the pylink session open across reset,
    # re-enumeration, and the read; it is released in the finally block.
    reset_stack = contextlib.ExitStack()

    try:
        if keep_attached:
            reset_stack.enter_context(
                attached_reset_session(
                    device=jlink_device, jlink_serial=jlink_serial
                )
            )
        else:
            reset_target(device=jlink_device, jlink_serial=jlink_serial)

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
            port = _resolve_cdc_port(marker=usb_marker, pre_existing=pre_existing)

        # --- Step 3: open port with DTR ---
        log.info("Opening USB CDC port: %s", port)
        ser = serial.Serial(
            port=port,
            baudrate=_BAUD,
            timeout=LINE_TIMEOUT_S,
            dsrdtr=True,  # assert DTR so nsx_usb_connected() returns true
        )
        ser.dtr = True
        ser.reset_input_buffer()

        # --- Step 4: collect lines ---
        deadline = time.monotonic() + timeout_s

        while time.monotonic() < deadline:
            ser.timeout = min(deadline - time.monotonic(), LINE_TIMEOUT_S)
            raw = ser.readline()

            if not raw:
                # Timeout on readline — no data for LINE_TIMEOUT_S
                if lines and any(HPX_START in l for l in lines[:20]):
                    log.warning(
                        "No USB data for %ds after receiving %d lines — HPX_END may have been lost",
                        LINE_TIMEOUT_S,
                        len(lines),
                    )
                    break
                continue

            try:
                line = raw.decode("utf-8", errors="replace").strip()
            except Exception:
                continue
            if not line:
                continue

            line_ts = time.monotonic()
            lines.append(line)
            log.debug("USB: %s", line)
            if line == HPX_START and hpx_start_s is None:
                hpx_start_s = line_ts

            if line == HPX_END:
                hpx_end_s = line_ts
                log.info("Captured %d lines (HPX_END received)", len(lines))
                finalize_timing()
                return lines

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

    log.warning(
        "USB CDC capture timed out after %.0fs (%d lines captured)",
        timeout_s,
        len(lines),
    )
    finalize_timing()
    return lines
