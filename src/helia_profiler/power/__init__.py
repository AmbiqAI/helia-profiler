"""Power measurement drivers.

Two measurement modes:

- **external**: An off-chip instrument (e.g. Joulescope) samples current on
  the target's power rail while the firmware toggles a GPIO sync pin to
  bracket inference.  Captures whole-inference energy only.
- **internal**: On-device measurement. The firmware reads an on-target
  monitor around the fixed-N window and reports it through the power
  terminal.

Driver names:

- ``joulescope``:       Joulescope JS110, JS220, or JS320 (auto-detected via
  ``pyjoulescope_driver`` device enumeration).
- ``ina228``:           On-target INA228 energy/charge accumulator.

Use :func:`get_driver` to resolve a driver by name.
"""

from __future__ import annotations

import logging

from ..errors import PowerError
from .base import (
    GatedPowerWindow,
    PowerDriver,
    PowerMode,
    PowerResult,
    PowerSample,
    PowerSummary,
)
from .metadata import (
    MeasurementScope,
    ObservationMode,
    PowerIntegrity,
    PowerMetadata,
)

log = logging.getLogger("hpx")

__all__ = [
    "PowerDriver",
    "GatedPowerWindow",
    "MeasurementScope",
    "ObservationMode",
    "PowerIntegrity",
    "PowerMetadata",
    "PowerMode",
    "PowerResult",
    "PowerSample",
    "PowerSummary",
    "get_driver",
    "list_drivers",
    "register_driver",
    "resolve_driver_class",
]


_DRIVERS: dict[str, type[PowerDriver]] = {}


def _register_builtins() -> None:
    """Lazily import and register built-in drivers."""
    if _DRIVERS:
        return

    from .ina228_driver import Ina228Driver
    from .joulescope.driver import JoulescopeDriver

    # Single unified Joulescope driver — handles JS110, JS220, and JS320.
    register_driver("joulescope", JoulescopeDriver)
    register_driver("ina228", Ina228Driver)


def register_driver(name: str, driver_cls: type[PowerDriver]) -> None:
    """Register (or override) the driver class used for ``name``.

    Exposed so tests (or future built-ins) can add a driver without reaching
    into the private ``_DRIVERS`` dict. The class is constructed as
    ``driver_cls(serial=...)``, so it must accept that keyword.
    """
    _DRIVERS[name] = driver_cls


def resolve_driver_class(name: str) -> type[PowerDriver]:
    """Look up the driver class registered for ``name``.

    Raises :class:`PowerError` if the name is unknown.
    """
    _register_builtins()
    cls = _DRIVERS.get(name)
    if cls is None:
        raise PowerError(
            f"Unknown power driver '{name}'",
            hint=f"Available drivers: {', '.join(sorted(_DRIVERS))}",
        )
    return cls


def get_driver(name: str, *, serial: str | None = None) -> PowerDriver:
    """Instantiate and return the named power driver.

    The unified ``joulescope`` driver auto-detects JS110 vs JS220 from the
    enumerated USB device path.
    Raises :class:`PowerError` if the name is unknown.
    """
    return resolve_driver_class(name)(serial=serial)


def list_drivers() -> list[str]:
    _register_builtins()
    return sorted(_DRIVERS)
