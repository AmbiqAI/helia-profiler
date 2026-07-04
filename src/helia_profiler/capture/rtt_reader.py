"""DEPRECATED shim — the RTT reader moved to ``transport.rtt``.

Import from :mod:`helia_profiler.transport.rtt` instead.  This module re-exports
the reader for back-compat during the modular refactor.
"""

from __future__ import annotations

# DEPRECATED: re-exported from helia_profiler.transport.rtt
from ..transport.rtt import capture_rtt_output

__all__ = ["capture_rtt_output"]
