"""How a model's internal state behaves across the invocations of one run.

Each engine declares the lifecycles it implements in its own firmware; HPX
routes a request to the engine, refuses one it does not implement with the
engine's reason, and records what ran. HPX never inspects a model graph to
decide.
"""

from __future__ import annotations

from enum import StrEnum

from .engines import EngineType, get_adapter
from .errors import EngineError


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
    """Refuse *lifecycle* when *engine* does not implement it, with the engine's reason.

    Raises:
        EngineError: The engine's adapter does not declare *lifecycle*.
    """
    adapter = get_adapter(engine)
    if lifecycle in adapter.lifecycles:
        return
    supported = ", ".join(sorted(item.value for item in adapter.lifecycles))
    raise EngineError(
        adapter.lifecycle_refusal(lifecycle),
        hint=f"Request a lifecycle {adapter.name} implements: {supported}.",
    )


def lifecycle_invocations(lifecycle: Lifecycle, iterations: int, warmups: int) -> tuple[int, int]:
    """The timed iterations and warmups that run *lifecycle* for a configured run."""
    if lifecycle is Lifecycle.SINGLE:
        return 1, 0
    return iterations, warmups
