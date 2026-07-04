"""DEPRECATED shim — the SWO reader moved to ``transport.swo``.

Import from :mod:`helia_profiler.transport.swo` instead.  This module re-exports
the reader for back-compat during the modular refactor.
"""

from __future__ import annotations

# DEPRECATED: re-exported from helia_profiler.transport.swo
from ..transport.swo import capture_swo_output

__all__ = ["capture_swo_output"]
