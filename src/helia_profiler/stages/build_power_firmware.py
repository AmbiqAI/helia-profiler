"""Rerender and incrementally rebuild the dedicated power firmware target."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from ..artifacts import FirmwareArtifact
from ..errors import BuildError, FirmwareError
from ..pipeline import PipelineContext

log = logging.getLogger("hpx")


def _remove_stale_power_outputs(build_dir: Path) -> None:
    """Require the explicit target build to produce this run's artifact."""
    for filename in (
        "hpx_profiler_power",
        "hpx_profiler_power.bin",
        "hpx_profiler_power.elf",
        "hpx_profiler_power.axf",
    ):
        for candidate in build_dir.rglob(filename):
            if candidate.is_file():
                candidate.unlink()


class BuildPowerFirmwareStage:
    @property
    def name(self) -> str:
        return "build_power_firmware"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return (
            not ctx.config.power.enabled
            or ctx.config.power.firmware != "dedicated"
            or ctx.power_run is None
            or ctx.power_run.plan.inference_count is None
        )

    def run(self, ctx: PipelineContext) -> None:
        from ..firmware import (
            _find_target_binary,
            _nsx_toolchain,
            render_power_source,
        )
        from .. import nsx as nsx_cli

        if ctx.power_run is None or ctx.power_run.plan.inference_count is None:
            raise BuildError("Cannot build fixed-N power firmware without a resolved power plan.")
        if ctx.firmware_dir is None or ctx.build_dir is None:
            raise BuildError("Profile firmware workspace must be configured before power rebuild.")

        ctx.report_progress(
            f"Rendering fixed-N source for {ctx.power_run.plan.inference_count:,} inferences"
        )

        try:
            render_power_source(ctx, inference_count=ctx.power_run.plan.inference_count)
            ctx.power_firmware = None
            ctx.deployed_power_firmware = None
            ctx.power_binary_path = None
            ctx.power_result = None
            if ctx.power_run is not None:
                ctx.power_run = replace(
                    ctx.power_run,
                    firmware=None,
                    deployment=None,
                    observation=None,
                )
            _remove_stale_power_outputs(ctx.build_dir)
            nsx_cli.build(
                ctx.firmware_dir,
                toolchain=_nsx_toolchain(ctx.config.target.toolchain),
                target="hpx_profiler_power",
                timeout_s=ctx.config.timeouts.build_s,
                verbose=ctx.config.verbose,
            )
        except (BuildError, FirmwareError):
            raise
        except Exception as exc:
            raise BuildError(
                f"Power firmware incremental build failed: {exc}",
                hint="The profile build remains valid; inspect the power target build output.",
            ) from exc

        binary_path = _find_target_binary(ctx.build_dir, "hpx_profiler_power")
        if binary_path is None:
            raise BuildError(
                "Incremental build succeeded but hpx_profiler_power was not found."
            )
        ctx.power_binary_path = binary_path
        ctx.publish_power_firmware(FirmwareArtifact(
            role="power",
            target_name="hpx_profiler_power",
            app_dir=ctx.firmware_dir,
            build_dir=ctx.build_dir,
            binary_path=binary_path,
        ))
        log.info(
            "Power firmware rebuilt: %s (N=%d, source=%s)",
            binary_path,
            ctx.power_run.plan.inference_count,
            ctx.power_run.plan.count_source,
        )
        ctx.report_progress(
            "Power firmware ready",
            kind="checkpoint",
            min_verbosity=1,
        )
