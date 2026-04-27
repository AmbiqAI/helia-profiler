"""Power measurement drivers.

Two measurement modes:

- **external**: An off-chip instrument (e.g. Joulescope) samples current on
  the target's power rail while the firmware toggles a GPIO sync pin to
  bracket inference.  Captures whole-inference energy only.
- **internal**: On-device measurement via SoC power registers or PMU events.
  Can potentially capture per-layer power.  (Future / experimental.)

Driver names:

- ``joulescope``:       Auto-detect — tries JS110 first, then JS220.
- ``joulescope-js110``: Force Joulescope JS110 (``joulescope`` package).
- ``joulescope-js220``: Force Joulescope JS220 (``pyjoulescope_driver``).
- ``ondevice``:         On-device measurement (experimental).

Use :func:`get_driver` to resolve a driver by name.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..errors import PowerError
from .base import PowerDriver, PowerMode, PowerResult, PowerSample, PowerSummary

if TYPE_CHECKING:
    pass

log = logging.getLogger("hpx")

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
    from .joulescope_js220 import JoulescopeJS220Driver
    from .ondevice_driver import OnDeviceDriver

    _DRIVERS["joulescope-js110"] = JoulescopeDriver
    _DRIVERS["joulescope-js220"] = JoulescopeJS220Driver
    _DRIVERS["ondevice"] = OnDeviceDriver


def _auto_detect_joulescope(*, serial: str | None = None) -> PowerDriver:
    """Try JS110 first, then JS220.  Return the first usable driver."""
    _register_builtins()

    # Prefer JS110 (more widely deployed, simpler blocking API)
    js110_cls = _DRIVERS["joulescope-js110"]
    try:
        js110 = js110_cls(serial=serial)  # type: ignore[call-arg]
    except TypeError:
        js110 = js110_cls()
    try:
        js110.check_available()
        log.info("Auto-detected Joulescope JS110")
        return js110
    except PowerError:
        pass

    # Fall back to JS220
    js220_cls = _DRIVERS["joulescope-js220"]
    try:
        js220 = js220_cls(serial=serial)  # type: ignore[call-arg]
    except TypeError:
        js220 = js220_cls()
    try:
        js220.check_available()
        log.info("Auto-detected Joulescope JS220")
        return js220
    except PowerError:
        pass

    raise PowerError(
        "No Joulescope driver available",
        hint="Install 'joulescope' (JS110) or 'pyjoulescope_driver' (JS220).  "
        "Or specify driver explicitly: --power-driver joulescope-js110",
    )


def get_driver(name: str, *, serial: str | None = None) -> PowerDriver:
    """Instantiate and return the named power driver.

    The special name ``"joulescope"`` auto-detects JS110 vs JS220.
    Raises :class:`PowerError` if the name is unknown.
    """
    if name == "joulescope":
        return _auto_detect_joulescope(serial=serial)

    _register_builtins()
    cls = _DRIVERS.get(name)
    if cls is None:
        raise PowerError(
            f"Unknown power driver '{name}'",
            hint=f"Available drivers: joulescope, {', '.join(_DRIVERS)}",
        )
    try:
        return cls(serial=serial)  # type: ignore[call-arg]
    except TypeError:
        # Driver doesn't accept a serial kwarg (e.g. ondevice).
        return cls()


def list_drivers() -> list[str]:
    """Return the names of all registered power drivers."""
    _register_builtins()
    return ["joulescope"] + list(_DRIVERS)
