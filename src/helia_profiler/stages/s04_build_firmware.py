"""Stage 4 — Build firmware: invoke NSX configure + build."""

from __future__ import annotations

import logging
import subprocess

from ..errors import BuildError
from ..pipeline import PipelineContext

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
        except subprocess.CalledProcessError as exc:
            stderr_text = (
                exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            )
            raise BuildError(
                f"NSX build failed (exit code {exc.returncode}).",
                returncode=exc.returncode,
                stderr=stderr_text,
                hint="Run 'hpx doctor' to verify toolchain. Use --verbose for full build output.",
            ) from exc
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
