"""Stage 1a — Resolve the J-Link probe to use for this run."""

from __future__ import annotations

import logging

from ..jlink import resolve_probe_serial
from ..pipeline import PipelineContext

log = logging.getLogger("hpx")


class ResolveJLinkProbeStage:
    @property
    def name(self) -> str:
        return "resolve_jlink_probe"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return ctx.soc is None or not ctx.soc.jlink_device

    def run(self, ctx: PipelineContext) -> None:
        assert ctx.soc is not None
        serial = resolve_probe_serial(
            device=ctx.soc.jlink_device,
            expected_core=ctx.soc.core,
            requested_serial=ctx.config.target.jlink_serial,
        )
        ctx.resolved_jlink_serial = serial
        log.info("Using J-Link serial: %s", serial)
