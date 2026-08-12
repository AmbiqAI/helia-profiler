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

from .base import PowerMode
from .ondevice_driver import OnDeviceDriver

log = logging.getLogger("hpx")


class Ina228Driver(OnDeviceDriver):
    """On-target INA228 (I2C) energy/charge accumulator measurement.

    Unlike the generic :class:`OnDeviceDriver` stub, this driver has a real
    firmware-side producer: the generated dedicated power binary initialises
    the INA228 over ``nsx-i2c``, brackets the fixed-N window with accumulator
    reset/read, and emits the measurement keys of the power terminal
    envelope. Everything host-side is inherited no-op behaviour — there is no
    instrument to arm, no GPIO gate to watch, and no rail to power-cycle.
    """

    #: The generated power firmware emits a complete measurement payload
    #: (energy/charge/bus-voltage) for this driver — this is what allows
    #: ``power.mode: internal`` to pass planning.
    supports_firmware_measurement = True

    @property
    def name(self) -> str:
        return "INA228 (on-device)"

    @property
    def mode(self) -> PowerMode:
        return PowerMode.INTERNAL

    def check_available(self) -> None:
        # No host-side dependencies: the monitor hangs off the target's own
        # I2C bus. Presence is verified by firmware at runtime via the
        # INA228 manufacturer/device ID registers; a missing or mis-wired
        # part surfaces as a typed ina228_init terminal failure.
        pass
