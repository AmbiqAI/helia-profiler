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
    "GATED_CAPTURE_DRIVER_NAMES",
    "get_driver",
    "list_drivers",
    "register_driver",
    "resolve_driver_class",
]


# ---------------------------------------------------------------------------
# Driver registry
# ---------------------------------------------------------------------------

_DRIVERS: dict[str, type[PowerDriver]] = {}

#: Names that resolve to a driver supporting host-side GPIO-gated capture
#: (Joulescope family). ``capture_power`` uses this — instead of duplicating
#: the alias list — to decide whether to arm the sync/DTR gating path.
GATED_CAPTURE_DRIVER_NAMES = frozenset({"joulescope", "joulescope-js110", "joulescope-js220"})


def _register_builtins() -> None:
    """Lazily import and register built-in drivers."""
    if _DRIVERS:
        return

    from .joulescope.driver import JoulescopeDriver
    from .ondevice_driver import OnDeviceDriver

    # Single unified Joulescope driver — handles JS110 and JS220.
    register_driver("joulescope", JoulescopeDriver)
    # Back-compat aliases so existing configs / docs keep working.
    register_driver("joulescope-js110", JoulescopeDriver)
    register_driver("joulescope-js220", JoulescopeDriver)
    register_driver("ondevice", OnDeviceDriver)


def register_driver(name: str, driver_cls: type[PowerDriver]) -> None:
    """Register (or override) the driver class used for ``name``.

    Exposed so tests (or future built-ins) can add a driver without reaching
    into the private ``_DRIVERS`` dict.
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
    enumerated USB device path; the ``-js110`` / ``-js220`` suffixes are
    accepted as aliases for backwards compatibility.
    Raises :class:`PowerError` if the name is unknown.
    """
    cls = resolve_driver_class(name)
    try:
        return cls(serial=serial)  # type: ignore[call-arg]
    except TypeError:
        # Driver doesn't accept a serial kwarg (e.g. ondevice).
        return cls()


def list_drivers() -> list[str]:
    """Return the names of all registered power drivers."""
    _register_builtins()
    return sorted(_DRIVERS)
