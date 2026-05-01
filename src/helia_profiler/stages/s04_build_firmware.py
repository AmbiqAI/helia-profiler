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
        ctx.binary_sections = _capture_binary_sections(
            binary_path,
            ctx.config.target.toolchain,
            ctx.config.timeouts.binary_probe_s,
        )

        # Capture compiler version for run metadata
        _capture_toolchain_info(ctx)


def _capture_toolchain_info(ctx: PipelineContext) -> None:
    """Query compiler and cmake versions, store in run_metadata."""
    toolchain = ctx.config.target.toolchain
    probe_s = ctx.config.timeouts.toolchain_probe_s
    compiler_version = ""
    cmake_version = ""

    if toolchain in ("armclang", "atfe"):
        compiler_cmd = toolchain
    elif "gcc" in toolchain:
        compiler_cmd = f"{toolchain}-gcc" if not toolchain.endswith("-gcc") else toolchain
    else:
        compiler_cmd = toolchain

    try:
        result = subprocess.run(
            [compiler_cmd, "--version"], capture_output=True, text=True, timeout=probe_s
        )
        if result.returncode == 0:
            compiler_version = result.stdout.strip().splitlines()[0]
    except Exception:
        pass

    # CMake version
    try:
        result = subprocess.run(
            ["cmake", "--version"], capture_output=True, text=True, timeout=probe_s
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


def _capture_binary_sections(
    binary_path: "Path",
    toolchain: str,
    timeout_s: int,
) -> BinarySections | None:
    """Run size tool on the built ELF and parse section sizes.

    Uses ``arm-none-eabi-size`` for GCC, ``fromelf --text -z`` for armclang.
    """
    if toolchain in ("armclang", "atfe"):
        return _capture_sections_fromelf(binary_path, timeout_s)

    # GCC path: toolchain is e.g. "arm-none-eabi-gcc"; strip the compiler suffix
    prefix = toolchain.rsplit("-gcc", 1)[0] if toolchain.endswith("-gcc") else toolchain
    size_cmd = f"{prefix}-size"
    try:
        result = subprocess.run(
            [size_cmd, str(binary_path)],
            capture_output=True, text=True, timeout=timeout_s,
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


def _capture_sections_fromelf(binary_path: "Path", timeout_s: int) -> BinarySections | None:
    """Parse ``fromelf --text -z`` output for armclang binaries.

    ``fromelf -z`` emits a table ending with a Grand Totals line like:
        Grand Totals: 12345   678   901
    (Code, RO Data, RW Data, ZI Data columns).
    """
    import re

    try:
        result = subprocess.run(
            ["fromelf", "--text", "-z", str(binary_path)],
            capture_output=True, text=True, timeout=timeout_s,
        )
        if result.returncode != 0:
            log.debug("fromelf failed: %s", result.stderr.strip())
            return None

        # Look for the "Grand Totals" line
        for line in result.stdout.splitlines():
            m = re.match(r"\s*Grand Totals?\s*[:\s]+([\d]+)\s+([\d]+)\s+([\d]+)\s+([\d]+)", line)
            if m:
                code, ro_data, rw_data, zi_data = (int(m.group(i)) for i in range(1, 5))
                text = code + ro_data
                data = rw_data
                bss = zi_data
                total = text + data + bss
                log.info("Binary sections (fromelf): text=%d data=%d bss=%d total=%d",
                         text, data, bss, total)
                return BinarySections(text=text, data=data, bss=bss, total=total)

        log.debug("Could not find Grand Totals in fromelf output")
        return None
    except Exception as exc:
        log.debug("Could not capture binary sections via fromelf: %s", exc)
        return None
