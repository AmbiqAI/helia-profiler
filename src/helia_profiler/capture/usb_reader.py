"""DEPRECATED shim — the USB CDC reader moved to ``transport.usb_cdc``.

Import from :mod:`helia_profiler.transport.usb_cdc` instead.  This module
re-exports the reader for back-compat during the modular refactor.
"""

from __future__ import annotations

# DEPRECATED: re-exported from helia_profiler.transport.usb_cdc
from ..transport.usb_cdc import capture_usb_output

__all__ = ["capture_usb_output"]
