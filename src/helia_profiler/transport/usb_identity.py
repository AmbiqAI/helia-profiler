"""Deterministic USB CDC identity marker shared by firmware and host.

``hpx`` generates and flashes the profiler firmware on every run, so it can
stamp a unique USB *serial-number* string descriptor into the target's CDC
device.  The host then matches that exact descriptor via pyserial's
``serial_number`` attribute, which is populated from ``iSerialNumber`` on
Linux, macOS, and Windows alike.  This makes USB CDC port selection
unambiguous even when several Ambiq boards are connected to the same host.

The marker is derived from the J-Link probe serial, which both sides know:
the firmware build resolves it before generating sources, and the capture
stage uses the same value to reset the board.  Deriving (rather than
randomly generating) the marker means no extra runtime state has to be
threaded between the build and capture stages.
"""

from __future__ import annotations

# Prefix identifying a CDC device as an hpx-profiled target.  Kept short so the
# full marker fits the firmware's 31 UTF-16 code-unit string-descriptor limit.
USB_MARKER_PREFIX = "HPX-"

# Human-friendly product string stamped alongside the serial marker.
USB_MARKER_PRODUCT = "NSX HPX Profiler"

# Firmware copies at most 31 characters into the USB string descriptor
# (see nsx_usb_descriptors.c: ``desc_str[32 + 1]`` with ``count < 31``).
_USB_STRING_MAX = 31


def usb_marker_serial(jlink_serial: str | None) -> str | None:
    """Return the USB serial-number marker for *jlink_serial*, or ``None``.

    Returns ``None`` when no J-Link serial is known; in that case the firmware
    keeps its default descriptor and the host falls back to heuristic port
    detection.
    """
    if not jlink_serial:
        return None
    return f"{USB_MARKER_PREFIX}{jlink_serial}"[:_USB_STRING_MAX]
