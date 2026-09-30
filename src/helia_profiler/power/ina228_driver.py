"""INA228 on-target power measurement driver.

The INA228 sits in series with the target rail and integrates energy/charge
in hardware. The firmware — not the host — owns the measurement: it resets
the accumulators immediately before the fixed-N inference window, reads them
immediately after, and reports the result inside the post-run
``PowerTerminalEnvelope`` (see ``capture/power_terminal.py``). The host side
therefore has no capture loop at all; ``CollectPowerTerminalStage`` builds
the :class:`~helia_profiler.power.base.PowerResult` from the envelope's
``OnDevicePowerSummary`` payload.

This driver class exists so the standard power plumbing has an object to
reason about: mode/ownership checks in ``plan_power``, lifecycle no-ops, and
the ``supports_firmware_measurement`` capability flag that unlocks internal
mode.
"""

from __future__ import annotations

import logging
from typing import Any

from ..errors import PowerError
from .base import PowerMode, PowerResult

log = logging.getLogger("hpx")


class Ina228Driver:
    """On-target INA228 (I2C) energy/charge accumulator measurement.

    The generated dedicated power binary initialises the INA228 over
    ``nsx-i2c``, brackets the fixed-N window with accumulator reset/read, and
    emits the measurement keys of the power terminal envelope.

    There is no host-side instrument to arm, no GPIO gate to watch, and no
    rail to power-cycle. Monitor presence is verified on-target via the INA228
    manufacturer/device ID registers, so a missing or mis-wired part surfaces
    as a typed ``ina228_init`` terminal failure rather than a host-side
    availability error.
    """

    #: The generated power firmware emits a complete measurement payload
    #: (energy/charge/bus-voltage) for this driver — this is what allows
    #: ``power.mode: internal`` to pass planning.
    supports_firmware_measurement = True
    #: No instrument-side stats stream to integrate over a GPIO gate.
    supports_gated_capture = False

    def __init__(self, *, serial: str | None = None) -> None:
        del serial

    @property
    def name(self) -> str:
        return "INA228 (on-device)"

    @property
    def mode(self) -> PowerMode:
        return PowerMode.INTERNAL

    def check_available(self) -> None:
        pass

    def capture(self, *, duration_s: float, io_voltage: float, **kwargs: Any) -> PowerResult:
        del duration_s, io_voltage, kwargs
        raise PowerError(
            "INA228 power is measured by the firmware, not captured by the host",
            hint="The result arrives in the power terminal envelope of the dedicated power run.",
        )

    def capture_gated(
        self,
        *,
        duration_s: float,
        io_voltage: float,
        sync_input_index: int,
        **kwargs: Any,
    ) -> PowerResult:
        del duration_s, io_voltage, sync_input_index, kwargs
        raise PowerError(
            "INA228 power measurement has no host-side GPIO gating path",
            hint="Host-side gated capture is supported only for external Joulescope drivers.",
        )

    def power_cycle(self, *, off_time_s: float = 0.5, settle_time_s: float = 1.0) -> None:
        raise PowerError(
            "INA228 driver cannot power-cycle the target",
            hint="Use an external Joulescope driver for power-cycle reset.",
        )

    def enable_passthrough(self) -> None:
        raise PowerError(
            "INA228 driver has no external relay to control",
            hint="Use a Joulescope driver for passthrough control.",
        )

    def disable_passthrough(self) -> None:
        pass

    def ensure_target_powered(self, *, required: bool) -> bool:
        """No-op — the INA228 driver does not control the rail.

        The target is expected to already be powered by USB, bench supply,
        or an external instrument the user manages directly. Always
        succeeds (returns ``True``) so the pipeline proceeds.
        """
        log.debug(
            "Ina228Driver.ensure_target_powered: no rail control; "
            "assuming target is on bench/USB supply."
        )
        return True
