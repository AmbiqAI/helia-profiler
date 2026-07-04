"""DEPRECATED shim — the UART reader moved to ``transport.uart``.

Import from :mod:`helia_profiler.transport.uart` instead.  This module
re-exports the reader for back-compat during the modular refactor.
"""

from __future__ import annotations

# DEPRECATED: re-exported from helia_profiler.transport.uart
from ..transport.uart import capture_uart_output

__all__ = ["capture_uart_output"]
