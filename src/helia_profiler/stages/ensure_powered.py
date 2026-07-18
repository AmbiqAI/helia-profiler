"""Stage 0b — Ensure target board powered.

Vendor-neutral wrapper around :meth:`PowerDriver.ensure_target_powered`.
Whatever the configured power driver is (Joulescope relay passthrough,
programmable bench supply, on-device sensor with no rail control, …), the
stage just asks the driver to make the board powered and records whether
it succeeded.

Behavior:

* ``target.ensure_board_powered = False`` (default) — the stage is skipped
  unless power capture is requested (``power.enabled = True``). Opt in with
  ``--ensure-power`` when the board's power genuinely comes from the
  driver's rail (e.g. Joulescope passthrough) and no measurement is needed.
* ``target.ensure_board_powered = True`` — best-effort: ask the driver,
  fall back to "skip" if the driver can't do it.
* ``power.enabled = True`` implies strict mode: the driver MUST succeed
  (otherwise downstream power capture can't run) — runs regardless of
  ``ensure_board_powered``.

If the driver skipped, :attr:`PipelineContext.passthrough_skipped` is set
to ``True`` so the flash stage can include "is your EVB powered?" in its
hint when flash fails.
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
            log.debug("power module unavailable: %s — skipping.", exc)
            ctx.passthrough_skipped = True
            return

        driver_name = cfg.power.driver or "joulescope"

        try:
            driver = get_driver(driver_name, serial=cfg.power.serial)
        except PowerError:
            if cfg.power.enabled:
                raise
            ctx.passthrough_skipped = True
            return

        # Delegate the full decision matrix to the driver itself; the
        # pipeline stays driver-agnostic.
        powered = driver.ensure_target_powered(required=cfg.power.enabled)
        ctx.passthrough_skipped = not powered
        ctx.target_power_ensured = powered
