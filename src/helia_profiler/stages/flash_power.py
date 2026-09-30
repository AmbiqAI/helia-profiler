"""Deploy the dedicated power firmware as an explicit pipeline step."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..config import PowerFirmware
from ..results import DeploymentRecord
from ..errors import BuildError
from ..pipeline import PipelineContext
from .flash import deploy_firmware

log = logging.getLogger("hpx")


class FlashPowerFirmwareStage:
    @property
    def name(self) -> str:
        return "flash_power_firmware"

    def should_skip(self, ctx: PipelineContext) -> bool:
        firmware_mode = (
            ctx.power_run.plan.firmware_mode
            if ctx.power_run is not None
            else ctx.config.power.firmware
        )
        return not ctx.config.power.enabled or firmware_mode != PowerFirmware.DEDICATED

    def run(self, ctx: PipelineContext) -> None:
        if ctx.power_run is None or ctx.power_run.firmware is None:
            raise BuildError(
                "Dedicated power firmware was requested but no power artifact was built.",
                hint="Run the power firmware build step before deployment.",
            )
        artifact = ctx.power_run.firmware

        deploy_firmware(ctx, artifact.binary_path, role="power")

        ctx.publish_power_deployment(
            DeploymentRecord(
                firmware=artifact,
                target_id=ctx.config.target.board,
                deployed_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        log.info("Power firmware deployed: %s", artifact.binary_path)
        ctx.report_progress("Power firmware deployed", kind="checkpoint", min_verbosity=1)
