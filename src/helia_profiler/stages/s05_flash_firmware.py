"""Stage 5 — Flash firmware: deploy binary to target via NSX / JLink."""

from __future__ import annotations

import logging

from ..errors import BuildError, PowerError
from ..pipeline import PipelineContext

log = logging.getLogger("hpx")


def _try_power_cycle(ctx: PipelineContext) -> bool:
    """Attempt a Joulescope power-cycle reset to recover the debug domain.

    Returns *True* if the power cycle succeeded, *False* otherwise.
    """
    if not ctx.config.power.enabled:
        return False
    try:
        from ..power import get_driver

        driver = get_driver(ctx.config.power.driver)
        driver.power_cycle(off_time_s=1.0, settle_time_s=2.0)
        log.info("Power-cycle reset succeeded — retrying flash")
        return True
    except (PowerError, Exception) as exc:
        log.debug("Power-cycle recovery not available: %s", exc)
        return False


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
        except BuildError as first_exc:
            # Flash can fail when the debug domain is locked (e.g. after a
            # previous run put the chip to sleep).  If a Joulescope is
            # available, power-cycle to recover and retry once.
            if _try_power_cycle(ctx):
                flash_app(ctx)  # raises BuildError on second failure
            else:
                raise
        except Exception as exc:
            raise BuildError(
                f"Flash failed: {exc}",
                hint="Check that the board is connected via JLink.",
            ) from exc

        log.info("Firmware flashed to %s", ctx.config.target.board)
