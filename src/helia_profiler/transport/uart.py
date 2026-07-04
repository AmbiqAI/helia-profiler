"""UART (J-Link OB VCOM) capture transport backend.

Wraps the UART reader.  Forwards the SoC keep-attached requirement so the probe
is held across capture on SoCs that gate DWT->CYCCNT behind the debug power
domain (Apollo3/Apollo4).
"""

from __future__ import annotations

from ..config import Transport
from .base import BaseCaptureTransport


class UartTransport(BaseCaptureTransport):
    transport = Transport.UART
    #: UART holds the probe attached when the SoC requires it (AP3/AP4).
    honors_keep_attached = True

    def collect(self, ctx) -> list[str]:
        from ..capture.uart_reader import capture_uart_output

        args = self._args
        return capture_uart_output(
            jlink_serial=args.jlink_serial,
            jlink_device=args.jlink_device,
            timeout_s=args.overall_timeout_s,
            heartbeat_timeout_s=args.heartbeat_timeout_s,
            keep_attached=args.keep_debugger_attached,
            timing_out=args.timing_raw,
            reset_controller=args.reset_controller,
        )
