"""Capture transport backends for heliaPROFILER.

This package owns the wire protocol (:mod:`.protocol`) and — added in later
commits — the :class:`CaptureTransport` backend objects and their registry.

The HPX protocol constants and the shared line-collection loop live in
:mod:`helia_profiler.transport.protocol`; they are re-exported here for
convenience.
"""

from __future__ import annotations

from .protocol import (
    CLEAN_WINDOW_BEGIN_PHASE,
    DEFAULT_TIMEOUT_S,
    HEARTBEAT_TIMEOUT_S,
    HPX_END,
    HPX_PROTOCOL_VERSION,
    HPX_START,
    LINE_TIMEOUT_S,
    WINDOW_BUDGET_MARGIN_S,
    WINDOW_BUDGET_SAFETY,
    collect_lines,
    window_budget_s,
)

__all__ = [
    "CLEAN_WINDOW_BEGIN_PHASE",
    "DEFAULT_TIMEOUT_S",
    "HEARTBEAT_TIMEOUT_S",
    "HPX_END",
    "HPX_PROTOCOL_VERSION",
    "HPX_START",
    "LINE_TIMEOUT_S",
    "WINDOW_BUDGET_MARGIN_S",
    "WINDOW_BUDGET_SAFETY",
    "collect_lines",
    "window_budget_s",
]
