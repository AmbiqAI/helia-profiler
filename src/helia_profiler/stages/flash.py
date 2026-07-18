"""Stage 5 — Flash firmware: deploy binary to target via NSX / JLink."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..results import DeploymentRecord
from ..errors import BuildError
from ..firmware import _nsx_toolchain
from ..pipeline import PipelineContext
from ..target.probe.jlink import JLinkFlashBackend

log = logging.getLogger("hpx")


def _try_power_cycle(ctx: PipelineContext) -> bool:
    """Attempt a Joulescope power-cycle reset to recover the debug domain.

    Returns *True* if the power cycle succeeded, *False* otherwise.
    """
    if not ctx.config.power.enabled:
        return False
    from ..power import get_driver
    from ..target.lifecycle import try_power_cycle

    try:
        driver = get_driver(ctx.config.power.driver, serial=ctx.config.power.serial)
    except Exception as exc:
        log.debug("Power-cycle recovery driver unavailable: %s", exc)
        return False
    return try_power_cycle(
        driver,
        ctx.config.power.driver,
        strict=False,
        off_time_s=1.0,
        settle_time_s=2.0,
    )


class FlashFirmwareStage:
    @property
    def name(self) -> str:
        return "flash_firmware"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        if ctx.profile_run is None:
            raise BuildError("No profile artifact to flash — build stage did not run.")
        artifact = ctx.profile_run.firmware

        if ctx.firmware_dir is None:
            raise BuildError("No firmware directory to flash — firmware generation did not run.")
        ctx.report_progress(f"Deploying profile firmware to {ctx.config.target.board}")
        backend = ctx.flash_backend or JLinkFlashBackend()
        toolchain = _nsx_toolchain(ctx.config.target.toolchain)
        jlink_serial = ctx.resolved_jlink_serial or ctx.config.target.jlink_serial

        def flash_firmware() -> None:
            backend.flash(
                ctx.firmware_dir,
                toolchain=toolchain,
                jlink_serial=jlink_serial,
                frozen=ctx.config.frozen,
                timeout_s=ctx.config.timeouts.flash_s,
                verbose=ctx.config.verbose,
            )

        try:
            flash_firmware()
        except BuildError as first_exc:
            # Flash can fail when the debug domain is locked (e.g. after a
            # previous run put the chip to sleep).  If a Joulescope is
            # available, power-cycle to recover and retry once.
            if _try_power_cycle(ctx):
                flash_firmware()  # raises BuildError on second failure
            else:
                if ctx.passthrough_skipped:
                    raise BuildError(
                        str(first_exc),
                        hint=(
                            (first_exc.hint + " " if first_exc.hint else "")
                            + "Verify the EVB is powered (USB / bench supply), "
                            "or pass --power-serial <NNNN> to select a "
                            "specific power instrument for passthrough."
                        ),
                    ) from first_exc
                raise
        except Exception as exc:
            hint = "Check that the board is connected via JLink."
            if ctx.passthrough_skipped:
                hint += (
                    " Verify the EVB is powered, or pass --power-serial <NNNN> "
                    "to select a specific power instrument for passthrough."
                )
            raise BuildError(
                f"Flash failed: {exc}",
                hint=hint,
            ) from exc

        log.info("Firmware flashed to %s", ctx.config.target.board)
        ctx.publish_profile_deployment(
            DeploymentRecord(
                firmware=artifact,
                target_id=ctx.config.target.board,
                deployed_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        ctx.report_progress("Profile firmware deployed", kind="checkpoint", min_verbosity=1)
