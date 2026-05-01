"""Stage 0b — Ensure board powered: enable Joulescope passthrough early.

When a Joulescope sits in series with the EVB power rail, the board is
unpowered until the JS internal relay closes ("current passthrough").
Without passthrough J-Link can't see the target → flash fails → users
have to launch the Joulescope desktop app first.

This stage scans for any Joulescope (JS110/JS220) and, if found, enables
passthrough so the board comes alive *before* the flash stage runs.  If no
Joulescope is connected, the stage is a no-op — handy for users running on
bench supplies.

Behavior:

* ``target.ensure_board_powered = True`` (default) — auto-detect & enable.
* ``target.ensure_board_powered = False`` — skip entirely.
* ``power.enabled = True`` implies passthrough is required, so the stage
  runs even when ``ensure_board_powered`` is False.

The driver handle is stashed on ``ctx._power_driver_handle`` so
:class:`~helia_profiler.pipeline.PipelineRunner` can release the relay in
its ``finally`` block.
"""

from __future__ import annotations

import logging

from ..errors import PowerError
from ..pipeline import PipelineContext

log = logging.getLogger("hpx")


class EnsureBoardPoweredStage:
    @property
    def name(self) -> str:
        return "ensure_board_powered"

    def should_skip(self, ctx: PipelineContext) -> bool:
        cfg = ctx.config
        # Skip only when both opt-out and no power capture is requested.
        return not (cfg.target.ensure_board_powered or cfg.power.enabled)

    def run(self, ctx: PipelineContext) -> None:
        cfg = ctx.config
        try:
            from ..power import get_driver
        except ImportError as exc:  # pragma: no cover
            log.debug("power module unavailable: %s — skipping passthrough", exc)
            return

        driver_name = cfg.power.driver or "joulescope"

        try:
            driver = get_driver(driver_name, serial=cfg.power.serial)
        except PowerError as exc:
            if cfg.power.enabled:
                # Power capture explicitly requested — re-raise so the user
                # gets a hard error instead of a silent failure later.
                raise
            log.info(
                "No Joulescope detected (%s) — assuming board is on bench supply.",
                exc,
            )
            return

        try:
            driver.enable_passthrough()
        except PowerError as exc:
            if cfg.power.enabled:
                raise
            log.warning(
                "Joulescope present but passthrough failed: %s — continuing.",
                exc,
            )
            return

        # Release the USB handle immediately. The JS110/JS220 relay state
        # (i_range=auto) is latched in hardware and persists after close, so
        # the board stays powered while later stages (flash, capture_power)
        # are free to open the device themselves without libusb conflicts.
        try:
            driver.disable_passthrough()
        except Exception:  # pragma: no cover - defensive
            log.debug("disable_passthrough after enable failed (ignored)")

        log.info("Joulescope passthrough enabled (driver=%s, relay latched).", driver.name)
