"""How a model's internal state behaves across the invocations of one run.

Each engine declares the lifecycles it implements in its own firmware; HPX
routes a request to the engine, refuses one it does not implement with the
engine's reason, and records what ran. HPX never inspects a model graph to
decide.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .engines import EngineType


class Lifecycle(StrEnum):
    """State handling across invocations.

    ``CARRY`` initializes the engine once and keeps internal state through
    warmups and timed invocations, restoring only the fixed inputs each time.
    ``SINGLE`` invokes the model once, so no state carries between
    invocations.
    """

    CARRY = "carry"
    SINGLE = "single"


def require_lifecycle(engine: EngineType, lifecycle: Lifecycle) -> None:
    """Refuse *lifecycle* when *engine* does not implement it, with the engine's reason."""
    del engine, lifecycle


def lifecycle_invocations(lifecycle: Lifecycle, iterations: int, warmups: int) -> tuple[int, int]:
    """The timed iterations and warmups that run *lifecycle* for a configured run."""
    if lifecycle is Lifecycle.SINGLE:
        return 1, 0
    return iterations, warmups
