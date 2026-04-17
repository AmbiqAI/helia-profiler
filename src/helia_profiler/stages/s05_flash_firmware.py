"""Stage 5 — Flash firmware: deploy binary to target via NSX / JLink."""

from __future__ import annotations

import logging
import subprocess

from ..errors import BuildError
from ..pipeline import PipelineContext

log = logging.getLogger("hpx")


class FlashFirmwareStage:

    @property
    def name(self) -> str:
        return "flash_firmware"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        if ctx.binary_path is None:
            raise BuildError("No binary to flash — build stage did not run.")

        from ..firmware import flash_app

        try:
            flash_app(ctx)
        except subprocess.CalledProcessError as exc:
            stderr_text = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            raise BuildError(
                f"Flash failed (exit code {exc.returncode}).",
                returncode=exc.returncode,
                stderr=stderr_text,
                hint="Check that the board is connected and JLink is available "
                     "(run 'hpx doctor').",
            ) from exc
        except BuildError:
            raise
        except Exception as exc:
            raise BuildError(
                f"Flash failed: {exc}",
                hint="Check that the board is connected via JLink.",
            ) from exc

        log.info("Firmware flashed to %s", ctx.config.target.board)
