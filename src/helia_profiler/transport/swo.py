"""SWO/ITM capture transport backend.

Wraps the SWO reader.  ``collect`` derives the SWO baud from the resolved
platform trace/CPU clock (never a hardcoded guess) and runs the reader (which
owns its own reset).
"""

from __future__ import annotations

from ..config import Transport
from ..errors import CaptureError
from .base import BaseCaptureTransport


class SwoTransport(BaseCaptureTransport):
    transport = Transport.SWO
    #: SWO always resets and re-attaches — it never holds the probe attached.
    honors_keep_attached = False

    def collect(self, ctx) -> list[str]:
        from ..capture.serial_reader import capture_swo_output

        args = self._args

        # SWO baud is derived from the trace clock, so it MUST come from the
        # resolved platform — never a hardcoded guess.  A wrong assumption here
        # halves/doubles the ITM baud and yields an undecodable stream (this is
        # exactly how the Apollo3 96-vs-48 MHz registry bug manifested).  Most
        # SoCs clock the TPIU from the CPU, but Apollo3 uses a dedicated,
        # CPU-independent trace clock that does not change with TurboSPOT burst —
        # so honor swo_trace_clock_mhz when set.
        cpu_clock_mhz = ctx.run_metadata.platform.cpu_clock_mhz
        swo_ref_mhz = ctx.soc.swo_trace_clock_mhz or cpu_clock_mhz
        if swo_ref_mhz <= 0:
            raise CaptureError(
                "SWO capture requires a resolved trace clock, but none was set.",
                hint=(
                    "Stage 1 (resolve_platform) must run before capture so the "
                    "selected target.clock.cpu frequency (or the SoC's fixed SWO "
                    "trace clock) drives the SWO baud rate."
                ),
            )
        cpu_freq_hz = swo_ref_mhz * 1_000_000

        return capture_swo_output(
            build_dir=args.build_dir,
            jlink_serial=args.jlink_serial,
            jlink_device=args.jlink_device,
            cpu_freq=cpu_freq_hz,
            timing_out=args.timing_raw,
            reset_controller=args.reset_controller,
        )
