"""Shared host-side readiness helpers for capture transports.

The capture transports (RTT, SWO, USB) all face the same problem: after the
target is reset it is *not* immediately ready, and the host has no single
"firmware booted" signal to wait on.  Historically each reader solved this
with its own fixed ``time.sleep()``, which is brittle — too short and the
attach fails, too long and every run pays the cost.

This module provides the bounded, signal-driven primitives those readers
share so the behaviour is consistent and the few genuinely-blind delays stay
isolated in ``timing.py``.

The contract is always the same: **poll a cheap predicate until it succeeds or
a deadline elapses**, never sleep blindly hoping the target caught up.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from ..transport.timing import READINESS_POLL_INTERVAL_S
from ..target.probe.jlink import (
    attached_reset_session,
    open_jlink_with_retry,
    resume_if_halted,
)

log = logging.getLogger("hpx")


def poll_until(
    predicate: Callable[[], bool],
    *,
    timeout_s: float,
    interval_s: float = READINESS_POLL_INTERVAL_S,
    description: str = "condition",
) -> bool:
    """Poll *predicate* until it returns True or *timeout_s* elapses.

    Prefer this over ``time.sleep()`` whenever there is an observable signal
    to wait for.

    Args:
        predicate: Exceptions propagate to the caller unchanged — use
            ``open_jlink_with_retry`` for the case where the probe call
            itself may raise while not-yet-ready.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            log.debug("poll_until(%s) timed out after %.1fs", description, timeout_s)
            return False
        time.sleep(interval_s)


__all__ = [
    "attached_reset_session",
    "open_jlink_with_retry",
    "poll_until",
    "resume_if_halted",
]
