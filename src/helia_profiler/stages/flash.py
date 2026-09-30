"""Flash firmware: deploy the built image directly via J-Link.

Runs the NSX-generated per-target flash recipe (the address-explicit
``LoadFile <target>.bin`` script the NSX build emits next to the binary)
through :func:`~helia_profiler.target.probe.flash.flash_binary`, the same
path the dedicated power firmware deploys through.  ``nsx flash`` is
deliberately not used here: passing a probe serial forces it to re-run
CMake configure on a build tree the build stage configured moments
earlier (see :func:`helia_profiler.deps.nsx.flash`), which costs several
seconds per run and verifies nothing the pipeline doesn't already know.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from ..results import DeploymentRecord
from ..errors import BuildError, CaptureError, DeterministicCaptureError
from ..pipeline import PipelineContext
from ..target.lifecycle import try_power_cycle_for_context

log = logging.getLogger("hpx")


def deploy_firmware(ctx: PipelineContext, binary_path: Path, *, role: str) -> None:
    """Flash *binary_path*, power-cycling once to recover a locked debug domain.

    *role* names the image (``"profile"`` / ``"power"``) in progress and
    error messages.  Wrapped messages use ``args[0]``, never ``str(exc)``:
    ``HpxError.__str__`` appends the hint, which ``hint=`` re-attaches.
    """
    from ..target.probe.flash import flash_binary

    if ctx.soc is None:
        raise BuildError(f"Cannot flash {role} firmware before platform resolution.")
    soc = ctx.soc
    jlink_serial = ctx.resolved_jlink_serial or ctx.config.target.jlink_serial
    failed = f"{role.capitalize()} firmware deployment failed"

    def flash() -> None:
        flash_binary(
            binary_path,
            device=soc.jlink_device,
            load_addr=soc.capabilities.memory.app_flash_load_addr,
            jlink_serial=jlink_serial,
            timeout_s=ctx.config.timeouts.flash_s,
        )

    ctx.report_progress(f"Deploying {role} firmware to {ctx.config.target.board}")
    try:
        flash()
    except DeterministicCaptureError as exc:
        # Missing image / unknown load address: a power cycle cannot change
        # these, so retrying would frame a config gap as flaky hardware.
        raise DeterministicCaptureError(f"{failed}: {exc.args[0]}", hint=exc.hint) from exc
    except CaptureError as first_exc:
        # The debug domain can be locked (e.g. a previous run put the chip to
        # sleep); when a power instrument can cycle the rail, retry once.
        if try_power_cycle_for_context(ctx):
            try:
                flash()
            except CaptureError as retry_exc:
                raise BuildError(
                    f"{failed} after power-cycle recovery: {retry_exc.args[0]}",
                    hint=retry_exc.hint,
                ) from retry_exc
        else:
            hint = first_exc.hint or "Check that the board is connected via JLink."
            if ctx.passthrough_skipped:
                hint += (
                    " Verify the EVB is powered (USB / bench supply), "
                    "or pass --power-serial <NNNN> to select a "
                    "specific power instrument for passthrough."
                )
            raise BuildError(f"{failed}: {first_exc.args[0]}", hint=hint) from first_exc


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

        deploy_firmware(ctx, artifact.binary_path, role="profile")

        log.info("Firmware flashed to %s", ctx.config.target.board)
        ctx.publish_profile_deployment(
            DeploymentRecord(
                firmware=artifact,
                target_id=ctx.config.target.board,
                deployed_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        ctx.report_progress("Profile firmware deployed", kind="checkpoint", min_verbosity=1)
