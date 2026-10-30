"""Build firmware: invoke NSX configure + build."""

from __future__ import annotations

import logging

from ..errors import BuildError, ImageTooLargeError
from ..firmware.image import record_build_image
from ..hostenv.memory_measurement import measure_memory_regions, reconcile_memory
from ..hostenv.toolchain_probe import (
    binary_sections,
    cmake_version,
    compiler_version,
    symbol_inventory,
)
from ..pipeline import PipelineContext
from ..platform.placement import MemoryRegion
from ..results import FirmwareArtifact, MeasuredMemoryRegions, ToolchainInfo

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

        ctx.report_progress("Configuring and compiling the profile target")

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

        log.info("Binary: %s", binary_path)

        toolchain = ctx.config.target.toolchain
        ctx.binary_sections = binary_sections(
            binary_path,
            toolchain,
            timeout_s=ctx.config.timeouts.binary_probe_s,
        )
        # Measured memory regions (#133): the ELF classified into the verified
        # per-SoC windows, using the linker profile the binary actually linked
        # with. Best-effort like binary_sections; the soc gate also covers test
        # contexts that never ran ResolvePlatformStage.
        if ctx.soc is not None:
            linker_profile = str(ctx.config.engine.config.get("linker_profile") or "default")
            ctx.memory_regions = measure_memory_regions(
                binary_path,
                toolchain,
                ctx.soc,
                linker_profile=linker_profile,
                timeout_s=ctx.config.timeouts.binary_probe_s,
            )
            check_image_fits(ctx.memory_regions, has_psram=ctx.soc.memory.psram_kb > 0)
        # Symbol inventory + plan-vs-measured reconciliation (#133). A
        # partial nm listing is refused like a partial section inventory —
        # never understate.
        inventory = symbol_inventory(
            binary_path, toolchain, timeout_s=ctx.config.timeouts.binary_probe_s
        )
        if inventory is not None:
            symbols, unparsed = inventory
            if unparsed:
                log.info(
                    "nm listing is partial (%d unparsed rows); refusing symbol attribution.",
                    unparsed,
                )
            else:
                ctx.memory_symbols = symbols
        if (
            ctx.memory_symbols is not None
            and ctx.memory_regions is not None
            and ctx.memory_plan is not None
        ):
            ctx.memory_reconciliation = reconcile_memory(
                ctx.memory_plan, ctx.memory_regions, ctx.memory_symbols
            )
        ctx.publish_profile_firmware(
            FirmwareArtifact(
                role="profile",
                target_name="hpx_profiler",
                app_dir=ctx.firmware_dir,
                build_dir=build_dir,
                binary_path=binary_path,
                binary_sections=ctx.binary_sections,
            )
        )
        record_build_image(
            ctx.run_metadata,
            role="profile",
            target_name="hpx_profiler",
            binary_path=binary_path,
            build_dir=build_dir,
        )
        ready_message = "Profile firmware ready"
        if ctx.binary_sections is not None:
            ready_message += f" · {ctx.binary_sections.total:,} bytes"
        ctx.report_progress(ready_message, kind="checkpoint", min_verbosity=1)
        probe_s = ctx.config.timeouts.toolchain_probe_s
        ctx.run_metadata.toolchain = ToolchainInfo(
            compiler=toolchain,
            compiler_version=compiler_version(toolchain, timeout_s=probe_s),
            cmake_version=cmake_version(timeout_s=probe_s),
        )


def check_image_fits(regions: MeasuredMemoryRegions | None, *, has_psram: bool) -> None:
    """Refuse an image larger than the flashable MRAM.

    An unavailable measurement skips the check rather than guessing.
    """
    mram = regions.region(MemoryRegion.MRAM) if regions is not None else None
    if mram is None or mram.load_image <= mram.app_length:
        return
    raise ImageTooLargeError(
        f"Image exceeds flashable MRAM by {mram.load_image - mram.app_length:,} bytes",
        hint=(
            "Use a smaller model, or place weights in PSRAM."
            if has_psram
            else "Use a smaller model."
        ),
    )
