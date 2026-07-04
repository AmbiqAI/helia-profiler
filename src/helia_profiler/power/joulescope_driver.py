# DEPRECATED: this module has been split into the ``power.joulescope``
# package (device.py / driver.py / sync.py / stats.py / diagnostics.py).
# Import from ``helia_profiler.power.joulescope`` (or its submodules)
# directly in new code. This shim exists only for backward compatibility
# with any external code still importing ``power.joulescope_driver`` and
# will be removed in a future release.
"""Deprecated shim — re-exports the Joulescope driver public surface.

See :mod:`helia_profiler.power.joulescope` for the current implementation.
"""

from __future__ import annotations

from .joulescope.device import enumerate_devices
from .joulescope.driver import JoulescopeDriver
from .joulescope.sync import JoulescopeSyncController

__all__ = ["JoulescopeDriver", "JoulescopeSyncController", "enumerate_devices"]
