"""Power measurement drivers.

Two measurement modes:

- **external**: An off-chip instrument (e.g. Joulescope) samples current on
  the target's power rail while the firmware toggles a GPIO sync pin to
  bracket inference.  Captures whole-inference energy only.
- **internal**: On-device measurement via SoC power registers or PMU events.
  Can potentially capture per-layer power.  (Future / experimental.)

Use :func:`get_driver` to resolve a driver by name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..errors import PowerError
from .base import PowerDriver, PowerMode, PowerResult, PowerSample, PowerSummary

if TYPE_CHECKING:
    pass

__all__ = [
    "PowerDriver",
    "PowerMode",
    "PowerResult",
    "PowerSample",
    "PowerSummary",
    "get_driver",
    "list_drivers",
]

# ---------------------------------------------------------------------------
# Driver registry
# ---------------------------------------------------------------------------

_DRIVERS: dict[str, type[PowerDriver]] = {}


def _register_builtins() -> None:
    """Lazily import and register built-in drivers."""
    if _DRIVERS:
        return

    from .joulescope_driver import JoulescopeDriver
    from .ondevice_driver import OnDeviceDriver

    _DRIVERS["joulescope"] = JoulescopeDriver
    _DRIVERS["ondevice"] = OnDeviceDriver


def get_driver(name: str) -> PowerDriver:
    """Instantiate and return the named power driver.

    Raises :class:`PowerError` if the name is unknown.
    """
    _register_builtins()
    cls = _DRIVERS.get(name)
    if cls is None:
        raise PowerError(
            f"Unknown power driver '{name}'",
            hint=f"Available drivers: {', '.join(_DRIVERS)}",
        )
    return cls()


def list_drivers() -> list[str]:
    """Return the names of all registered power drivers."""
    _register_builtins()
    return list(_DRIVERS)
