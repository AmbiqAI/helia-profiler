"""Stage 1a — Resolve the J-Link probe to use for this run."""

from __future__ import annotations

import logging
import time

from ..errors import ConfigError
from ..pipeline import PipelineContext
from ..target.probe.jlink import (
    JLinkFlashBackend,
    JLinkProbe,
    JLinkResetController,
    resolve_probe_serial,
)

log = logging.getLogger("hpx")

_POST_POWER_PROBE_TIMEOUT_S = 8.0
_POST_POWER_PROBE_RETRY_S = 0.25


class ResolveJLinkProbeStage:
    @property
    def name(self) -> str:
        return "resolve_jlink_probe"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return ctx.soc is None or not ctx.soc.jlink_device

    def run(self, ctx: PipelineContext) -> None:
        assert ctx.soc is not None
        deadline = (
            time.monotonic() + _POST_POWER_PROBE_TIMEOUT_S
            if ctx.target_power_ensured
            else None
        )
        while True:
            try:
                serial = resolve_probe_serial(
                    device=ctx.soc.jlink_device,
                    expected_core=ctx.soc.core,
                    requested_serial=ctx.config.target.jlink_serial,
                )
                break
            except ConfigError as exc:
                if deadline is None or time.monotonic() >= deadline:
                    raise
                log.info("Waiting for J-Link to re-enumerate after target power-on: %s", exc)
                time.sleep(_POST_POWER_PROBE_RETRY_S)
        ctx.resolved_jlink_serial = serial
        ctx.probe = JLinkProbe(serial=serial)
        ctx.flash_backend = JLinkFlashBackend()
        ctx.reset_controller = JLinkResetController()
        log.info("Using J-Link serial: %s", serial)
