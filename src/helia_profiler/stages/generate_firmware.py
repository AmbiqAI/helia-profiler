"""Generate firmware: render Jinja templates into an NSX app."""

from __future__ import annotations

import logging

from ..errors import DependencyError, FirmwareError
from ..pipeline import PipelineContext

log = logging.getLogger("hpx")


class GenerateFirmwareStage:
    @property
    def name(self) -> str:
        return "generate_firmware"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        if ctx.engine_artifacts is None:
            raise FirmwareError(
                "No engine artifacts available — engine preparation stage did not run.",
            )

        from ..deps.dependencies import create_workspace, workspace_mutex
        from ..firmware import generate_app

        try:
            workspace = create_workspace(ctx)
            ctx.dependency_workspace = workspace
            with workspace_mutex(workspace):
                firmware_dir = generate_app(ctx)
        except (DependencyError, FirmwareError):
            raise
        except Exception as exc:
            raise FirmwareError(
                f"Firmware generation failed: {exc}",
                hint="Check Jinja templates and engine artifacts.",
            ) from exc

        ctx.firmware_dir = firmware_dir
        log.info("Firmware app generated at: %s", firmware_dir)
