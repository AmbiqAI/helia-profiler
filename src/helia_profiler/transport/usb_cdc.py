"""USB CDC capture transport backend.

Wraps the USB CDC reader.  Forwards the SoC keep-attached requirement so the
probe is held across capture on SoCs that gate DWT->CYCCNT behind the debug
power domain (Apollo3/Apollo4).
"""

from __future__ import annotations

from ..config import Transport
from .base import BaseCaptureTransport


class UsbCdcTransport(BaseCaptureTransport):
    transport = Transport.USB_CDC
    #: USB CDC holds the probe attached when the SoC requires it (AP3/AP4).
    honors_keep_attached = True

    def collect(self, ctx) -> list[str]:
        from ..capture.usb_reader import capture_usb_output
        from ..usb_identity import usb_marker_serial

        args = self._args
        return capture_usb_output(
            jlink_serial=args.jlink_serial,
            jlink_device=args.jlink_device,
            usb_port=ctx.config.target.usb_port,
            usb_marker=usb_marker_serial(args.jlink_serial),
            keep_attached=args.keep_debugger_attached,
            timing_out=args.timing_raw,
            reset_controller=args.reset_controller,
        )
