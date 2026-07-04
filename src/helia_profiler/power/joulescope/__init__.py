"""Joulescope external power measurement driver package (unified JS110 + JS220).

Split into focused submodules for maintainability; this ``__init__`` re-exports
the public surface so callers can keep using
``helia_profiler.power.joulescope_driver`` (deprecated shim) or the new
``helia_profiler.power.joulescope`` package path interchangeably.

- :mod:`.device` — shared ``pyjoulescope_driver`` handle, open/close/enumerate.
- :mod:`.driver` — :class:`JoulescopeDriver` (capture, gating, power-cycle).
- :mod:`.sync` — :class:`JoulescopeSyncController` (GPI/GPO lock-step sync).
- :mod:`.stats` — stats-array processing, window segmentation, energy sums.
- :mod:`.diagnostics` — Joulescope-local gated-window diagnostics.
"""

from __future__ import annotations

from .device import enumerate_devices
from .driver import JoulescopeDriver
from .sync import JoulescopeSyncController

__all__ = ["JoulescopeDriver", "JoulescopeSyncController", "enumerate_devices"]
