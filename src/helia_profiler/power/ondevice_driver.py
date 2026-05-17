"""On-device (internal) power measurement driver.

Uses SoC-level power monitoring (e.g. Apollo5 AUDADC power rails, PMU
energy counters, or temperature-compensated estimation).  This mode can
potentially capture per-layer power data since the measurement is done
on-chip alongside inference.

This is experimental — most SoCs don't expose fine-grained power data.
"""

from __future__ import annotations

import logging
from typing import Any

from ..errors import PowerError
from .base import PowerMode, PowerResult

log = logging.getLogger("hpx")


class OnDeviceDriver:
    """On-device internal power measurement (experimental).

    When fully implemented, this driver reads power data from the firmware's
    serial output, where the target reports energy counters or rail voltages
    per inference (or per layer).
    """

    @property
    def name(self) -> str:
        return "On-Device"

    @property
    def mode(self) -> PowerMode:
        return PowerMode.INTERNAL

    def check_available(self) -> None:
        # No host-side dependencies — measurement is firmware-side.
        # The firmware must be built with internal power monitoring enabled.
        pass

    def capture(
        self,
        *,
        duration_s: float,
        io_voltage: float,
        serial_port: str | None = None,
        per_layer: bool = False,
        **kwargs: Any,
    ) -> PowerResult:
        """Read on-device power data from the target's serial output.

        Not yet implemented — raises :class:`PowerError`.
        """
        raise PowerError(
            "On-device power measurement is not yet implemented",
            hint=(
                "This is an experimental feature. Use '--power-driver joulescope' "
                "for external power measurement via Joulescope."
            ),
        )

    def _parse_power_output(self, raw: str) -> PowerResult:
        """Parse firmware serial output containing power measurements.

        Expected format (future):
          HPX_POWER_SAMPLE <layer_idx>,<rail>,<current_ua>,<voltage_mv>
          HPX_POWER_SUMMARY avg_current_ua=...,energy_uj=...
        """
        raise PowerError(
            "On-device power output parsing not implemented",
            hint="This feature is under development.",
        )

    def power_cycle(self, *, off_time_s: float = 0.5, settle_time_s: float = 1.0) -> None:
        """Not supported for on-device driver."""
        raise PowerError(
            "On-device driver cannot power-cycle the target",
            hint="Use an external Joulescope driver for power-cycle reset.",
        )

    def enable_passthrough(self) -> None:
        """Not applicable for on-device driver."""
        raise PowerError(
            "On-device driver has no external relay to control",
            hint="Use a Joulescope driver for passthrough control.",
        )

    def disable_passthrough(self) -> None:
        """Not applicable for on-device driver."""
        pass

    def ensure_target_powered(self, *, required: bool) -> bool:
        """No-op — the on-device driver does not control the rail.

        The target is expected to already be powered by USB, bench supply,
        or an external instrument the user manages directly. Always
        succeeds (returns ``True``) so the pipeline proceeds.
        """
        log.debug("OnDeviceDriver.ensure_target_powered: no rail control — assuming target is on bench/USB supply.")
        return True
