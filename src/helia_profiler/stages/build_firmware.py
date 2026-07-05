"""Stage 4 — Build firmware: invoke NSX configure + build."""

from __future__ import annotations

import logging

from ..errors import BuildError
from ..pipeline import PipelineContext
from ..results import ToolchainInfo
from ..toolchain_probe import binary_sections, cmake_version, compiler_version

log = logging.getLogger("hpx")


class BuildFirmwareStage:
    @property
    def name(self) -> str:
        return "build_firmware"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        if ctx.firmware_dir is None:
            raise BuildError("No firmware directory — firmware generation stage did not run.")

        from ..firmware import build_app

        try:
            build_dir, binary_path = build_app(ctx)
        except BuildError:
            raise
        except Exception as exc:
            raise BuildError(
                f"Build failed: {exc}",
                hint="Run 'hpx doctor' to verify toolchain installation.",
            ) from exc

        ctx.build_dir = build_dir
        ctx.binary_path = binary_path
        log.info("Binary: %s", binary_path)

        # Capture binary section sizes
        toolchain = ctx.config.target.toolchain
        ctx.binary_sections = binary_sections(
            binary_path,
            toolchain,
            timeout_s=ctx.config.timeouts.binary_probe_s,
        )

        # Capture compiler + cmake version banners for run metadata
        probe_s = ctx.config.timeouts.toolchain_probe_s
        ctx.run_metadata.toolchain = ToolchainInfo(
            compiler=toolchain,
            compiler_version=compiler_version(toolchain, timeout_s=probe_s),
            cmake_version=cmake_version(timeout_s=probe_s),
        )
