"""Power measurement drivers.

Two measurement modes:

- **external**: An off-chip instrument (e.g. Joulescope) samples current on
  the target's power rail while the firmware toggles a GPIO sync pin to
  bracket inference.  Captures whole-inference energy only.
- **internal**: On-device measurement via SoC power registers or PMU events.
  Can potentially capture per-layer power.  (Future / experimental.)

Driver names:

- ``joulescope``:       Joulescope JS110 or JS220 (auto-detected via
  ``pyjoulescope_driver`` device enumeration).
- ``joulescope-js110``: Alias for ``joulescope`` (kept for back-compat).
- ``joulescope-js220``: Alias for ``joulescope`` (kept for back-compat).
- ``ondevice``:         On-device measurement (experimental).

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

log = logging.getLogger("hpx")

__all__ = [
    "PowerDriver",
    "GatedPowerWindow",
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

    # Single unified Joulescope driver — handles JS110 and JS220.
    _DRIVERS["joulescope"] = JoulescopeDriver
    # Back-compat aliases so existing configs / docs keep working.
    _DRIVERS["joulescope-js110"] = JoulescopeDriver
    _DRIVERS["joulescope-js220"] = JoulescopeDriver
    _DRIVERS["ondevice"] = OnDeviceDriver


def get_driver(name: str, *, serial: str | None = None) -> PowerDriver:
    """Instantiate and return the named power driver.

    The unified ``joulescope`` driver auto-detects JS110 vs JS220 from the
    enumerated USB device path; the ``-js110`` / ``-js220`` suffixes are
    accepted as aliases for backwards compatibility.
    Raises :class:`PowerError` if the name is unknown.
    """
    _register_builtins()
    cls = _DRIVERS.get(name)
    if cls is None:
        raise PowerError(
            f"Unknown power driver '{name}'",
            hint=f"Available drivers: {', '.join(sorted(_DRIVERS))}",
        )
    try:
        return cls(serial=serial)  # type: ignore[call-arg]
    except TypeError:
        # Driver doesn't accept a serial kwarg (e.g. ondevice).
        return cls()


def list_drivers() -> list[str]:
    """Return the names of all registered power drivers."""
    _register_builtins()
    return sorted(_DRIVERS)
