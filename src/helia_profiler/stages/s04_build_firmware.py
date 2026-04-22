"""Stage 4 — Build firmware: invoke NSX configure + build."""

from __future__ import annotations

import logging
import subprocess

from ..errors import BuildError
from ..pipeline import PipelineContext
from ..results import BinarySections, ToolchainInfo

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
        ctx.binary_sections = _capture_binary_sections(binary_path, ctx.config.target.toolchain)

        # Capture compiler version for run metadata
        _capture_toolchain_info(ctx)


def _capture_toolchain_info(ctx: PipelineContext) -> None:
    """Query compiler and cmake versions, store in run_metadata."""
    toolchain = ctx.config.target.toolchain
    compiler_version = ""
    cmake_version = ""

    # GCC version
    gcc_cmd = f"{toolchain}-gcc" if "gcc" in toolchain else toolchain
    try:
        result = subprocess.run(
            [gcc_cmd, "--version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            compiler_version = result.stdout.strip().splitlines()[0]
    except Exception:
        pass

    # CMake version
    try:
        result = subprocess.run(
            ["cmake", "--version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            cmake_version = result.stdout.strip().splitlines()[0]
    except Exception:
        pass

    ctx.run_metadata.toolchain = ToolchainInfo(
        compiler=toolchain,
        compiler_version=compiler_version,
        cmake_version=cmake_version,
    )


def _capture_binary_sections(binary_path: "Path", toolchain: str) -> BinarySections | None:
    """Run ``arm-none-eabi-size`` on the built ELF and parse section sizes."""
    # toolchain is e.g. "arm-none-eabi-gcc"; strip the compiler suffix to get the prefix
    prefix = toolchain.rsplit("-gcc", 1)[0] if toolchain.endswith("-gcc") else toolchain
    size_cmd = f"{prefix}-size"
    try:
        result = subprocess.run(
            [size_cmd, str(binary_path)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            log.debug("size command failed: %s", result.stderr.strip())
            return None
        # Output format: "   text\t   data\t    bss\t    dec\t    hex\tfilename"
        lines = result.stdout.strip().splitlines()
        if len(lines) < 2:
            return None
        parts = lines[1].split()
        if len(parts) < 4:
            return None
        text, data, bss, total = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
        log.info("Binary sections: text=%d data=%d bss=%d total=%d", text, data, bss, total)
        return BinarySections(text=text, data=data, bss=bss, total=total)
    except Exception as exc:
        log.debug("Could not capture binary sections: %s", exc)
        return None
