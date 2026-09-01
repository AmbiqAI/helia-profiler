"""Capture transport backends for heliaPROFILER.

This package owns the :class:`CaptureTransport` backend objects, their
registry, and the USB identity marker (:mod:`.usb_identity`) the CDC
backend and firmware share. The wire vocabulary itself (keys, sentinels, error codes) lives in
:mod:`helia_profiler.wire`; :mod:`.protocol` here is the shared
line-collection loop that consumes it, re-exported from this package for
convenience.
"""

from __future__ import annotations

from collections.abc import Callable

from ..config import Transport
from ..errors import CaptureError
from .base import BaseCaptureTransport, CaptureArgs, CaptureTransport
from .protocol import (
    CLEAN_WINDOW_BEGIN_PHASE,
    DEFAULT_TIMEOUT_S,
    HEARTBEAT_TIMEOUT_S,
    HPX_END,
    HPX_PROTOCOL_VERSION,
    HPX_START,
    LINE_TIMEOUT_S,
    WINDOW_BUDGET_CAP_S,
    WINDOW_BUDGET_MARGIN_S,
    WINDOW_BUDGET_SAFETY,
    collect_lines,
    window_budget_s,
)
from .rtt import RttTransport
from .swo import SwoTransport
from .uart import UartTransport
from .usb_cdc import UsbCdcTransport

# ---------------------------------------------------------------------------
# CaptureTransport backend registry
# ---------------------------------------------------------------------------
# One factory per Transport enum member — the sole dispatch point for "which
# backend drives this transport".  Adding a transport means registering a
# backend factory here; nothing downstream branches on the transport string.

_TRANSPORTS: dict[Transport, Callable[[], CaptureTransport]] = {}


def register_transport(
    transport: Transport, factory: Callable[[], CaptureTransport]
) -> None:
    """Register (or override) the backend factory used for ``transport``.

    ``factory`` is a zero-argument callable returning a fresh
    :class:`CaptureTransport` instance per capture.
    """
    _TRANSPORTS[Transport(transport)] = factory


def resolve_transport(transport: Transport) -> CaptureTransport:
    """Instantiate the backend registered for ``transport``.

    Raises :class:`~helia_profiler.errors.CaptureError` if the transport has no
    registered backend — this should only happen if a new ``Transport`` member
    is added without a matching backend registration.
    """
    try:
        return _TRANSPORTS[Transport(transport)]()
    except (KeyError, ValueError) as exc:
        raise CaptureError(
            f"Unknown capture transport '{transport}'",
            hint=(
                "Available transports: "
                f"{', '.join(sorted(t.value for t in _TRANSPORTS))}"
            ),
        ) from exc


register_transport(Transport.USB_CDC, UsbCdcTransport)
register_transport(Transport.RTT, RttTransport)
register_transport(Transport.UART, UartTransport)
register_transport(Transport.SWO, SwoTransport)


__all__ = [
    "CLEAN_WINDOW_BEGIN_PHASE",
    "DEFAULT_TIMEOUT_S",
    "HEARTBEAT_TIMEOUT_S",
    "HPX_END",
    "HPX_PROTOCOL_VERSION",
    "HPX_START",
    "LINE_TIMEOUT_S",
    "WINDOW_BUDGET_CAP_S",
    "WINDOW_BUDGET_MARGIN_S",
    "WINDOW_BUDGET_SAFETY",
    "BaseCaptureTransport",
    "CaptureArgs",
    "CaptureTransport",
    "RttTransport",
    "SwoTransport",
    "UartTransport",
    "UsbCdcTransport",
    "collect_lines",
    "register_transport",
    "resolve_transport",
    "window_budget_s",
]
