"""RTT capture transport backend.

Wraps the SEGGER RTT reader.  ``prepare`` recovers the linked RTT control-block
address from the build artifacts so capture can attach directly and skip the
slow SWD discovery sweep; ``collect`` runs the reader (which owns its own
reset).
"""

from __future__ import annotations

from ..config import Transport
from .base import BaseCaptureTransport, CaptureArgs, log


class RttTransport(BaseCaptureTransport):
    transport = Transport.RTT
    #: RTT always resets and re-attaches — it never holds the probe attached.
    honors_keep_attached = False

    def prepare(self, ctx, args: CaptureArgs) -> None:
        super().prepare(ctx, args)
        from ..capture.rtt_symbol import resolve_rtt_control_block_address

        # Recover the linked RTT control block address from the build artifacts
        # so capture can attach directly and skip the slow SWD discovery sweep.
        self._known_block_address = resolve_rtt_control_block_address(
            args.build_dir, ctx.config.target.toolchain
        )
        if self._known_block_address is not None:
            log.info(
                "Using known RTT control block address 0x%08X (skipping host-side scan)",
                self._known_block_address,
            )

    def collect(self, ctx) -> list[str]:
        from ..capture.rtt_reader import capture_rtt_output
        from ..placement import Placement

        args = self._args
        return capture_rtt_output(
            jlink_serial=args.jlink_serial,
            jlink_device=args.jlink_device,
            rtt_scan_ranges=ctx.soc.rtt_scan_ranges,
            known_block_address=self._known_block_address,
            model_path=ctx.config.model.path,
            weights_region=ctx.weights_region or Placement.MRAM,
            timeout_s=args.overall_timeout_s,
            heartbeat_timeout_s=args.heartbeat_timeout_s,
            timing_out=args.timing_raw,
            reset_controller=args.reset_controller,
        )
