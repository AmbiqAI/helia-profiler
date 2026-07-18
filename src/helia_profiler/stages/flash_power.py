"""Deploy the dedicated power firmware as an explicit pipeline step."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..artifacts import DeploymentRecord
from ..errors import BuildError, CaptureError
from ..pipeline import PipelineContext

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
        return (
            not ctx.config.power.enabled
            or firmware_mode != "dedicated"
        )

    def run(self, ctx: PipelineContext) -> None:
        from ..target.probe.jlink import flash_binary

        if ctx.power_run is None or ctx.power_run.firmware is None:
            raise BuildError(
                "Dedicated power firmware was requested but no power artifact was built.",
                hint="Run the power firmware build step before deployment.",
            )
        artifact = ctx.power_run.firmware
        binary_path = artifact.binary_path
        if ctx.soc is None:
            raise BuildError("Cannot flash power firmware before platform resolution.")

        ctx.report_progress(f"Deploying power firmware to {ctx.config.target.board}")

        jlink_serial = ctx.resolved_jlink_serial or ctx.config.target.jlink_serial
        try:
            flash_binary(
                binary_path,
                device=ctx.soc.jlink_device,
                jlink_serial=jlink_serial,
                timeout_s=ctx.config.timeouts.flash_s,
            )
        except CaptureError as exc:
            raise BuildError(
                f"Power firmware deployment failed: {exc}",
                hint=exc.hint,
            ) from exc
        ctx.publish_power_deployment(
            DeploymentRecord(
                firmware=artifact,
                target_id=ctx.config.target.board,
                deployed_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        log.info("Power firmware deployed: %s", binary_path)
        ctx.report_progress("Power firmware deployed", kind="checkpoint", min_verbosity=1)
