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

import glob
import logging
import time

import serial  # pyserial
from serial.tools import list_ports

from ..errors import CaptureError
from ..jlink import reset_target
from .readiness import poll_until
from .timing import USB_REENUM_FLOOR_S
from .transport import DEFAULT_TIMEOUT_S, HPX_END, HPX_START, LINE_TIMEOUT_S

log = logging.getLogger("hpx")

_ENUM_TIMEOUT_S = 15  # max time to wait for USB enumeration
_DISAPPEAR_TIMEOUT_S = 5  # max time to wait for the old CDC device to drop
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


def _find_cdc_port(
    pre_existing: set[str] | None = None,
    timeout_s: float = _ENUM_TIMEOUT_S,
) -> str:
    """Wait for a **new** USB CDC device to appear and return its path.

    If *pre_existing* is given, only devices NOT in that set are considered.
    This filters out the JLink VCOM that is already present before the
    firmware boots.

    Falls back to the first available device if no new device appears but
    at least one device exists.
    """
    deadline = time.monotonic() + timeout_s
    if pre_existing is None:
        pre_existing = set()

    while time.monotonic() < deadline:
        current = _snapshot_cdc_ports()
        new_ports = sorted(current - pre_existing)
        for port in new_ports:
            if _is_jlink_port(port):
                log.info("Ignoring J-Link VCOM candidate: %s", port)
                continue
            log.info("Found new USB CDC port: %s", port)
            return port
        time.sleep(0.5)

    # Fallback: if no *new* port appeared but there are existing non-J-Link
    # devices, reuse one of those. Refuse to open SEGGER VCOM, which only
    # causes a long timeout and hides the real enumeration failure.
    all_ports = sorted(_snapshot_cdc_ports())
    fallback_ports = [port for port in all_ports if not _is_jlink_port(port)]
    if fallback_ports:
        log.warning(
            "No new USB CDC device appeared; falling back to existing USB CDC %s",
            fallback_ports[-1],
        )
        return fallback_ports[-1]

    if all_ports:
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
    jlink_device: str = "AP510NFA-CBR",
    timeout_s: float = DEFAULT_TIMEOUT_S,
    usb_port: str | None = None,
    timing_out: dict[str, float] | None = None,
) -> list[str]:
    """Capture firmware output via USB CDC until HPX_END or timeout.

    USB CDC provides CRC-protected, flow-controlled delivery.  The
    firmware waits for DTR assertion before printing, so there is no
    fixed startup delay.

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
    reset_target(device=jlink_device, jlink_serial=jlink_serial)

    # --- Step 2: wait for TinyUSB device to disappear after reset ---
    # The old TinyUSB CDC device vanishes briefly after the target resets.
    # Use a small floor to avoid racing the host USB enumerator, then *poll*
    # for the old application device(s) to drop rather than sleeping a fixed
    # window.  Snapshot again afterwards so the new enumeration is detected as
    # a fresh device.
    time.sleep(USB_REENUM_FLOOR_S)
    app_ports_before = {p for p in pre_existing if not _is_jlink_port(p)}
    if app_ports_before:
        poll_until(
            lambda: not (app_ports_before & _snapshot_cdc_ports()),
            timeout_s=_DISAPPEAR_TIMEOUT_S,
            description="old USB CDC device to drop",
        )
    post_reset = _snapshot_cdc_ports()
    log.info("Post-reset CDC ports: %s", sorted(post_reset) or "(none)")

    # --- Step 3: find the NEW USB CDC port ---
    port = usb_port or _find_cdc_port(pre_existing=post_reset)

    # --- Step 3: open port with DTR ---
    log.info("Opening USB CDC port: %s", port)
    ser: serial.Serial | None = None
    lines: list[str] = []

    try:
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

    log.warning(
        "USB CDC capture timed out after %.0fs (%d lines captured)",
        timeout_s,
        len(lines),
    )
    finalize_timing()
    return lines
