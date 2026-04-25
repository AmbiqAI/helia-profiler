"""Power driver protocol and shared types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class PowerMode(str, Enum):
    """Power measurement mode."""

    EXTERNAL = "external"
    INTERNAL = "internal"


@dataclass(frozen=True)
class PowerSample:
    """A single power measurement sample."""

    timestamp_s: float
    current_a: float
    voltage_v: float

    @property
    def power_w(self) -> float:
        return self.current_a * self.voltage_v


@dataclass(frozen=True)
class PowerSummary:
    """Aggregate statistics from a power capture."""

    avg_current_a: float
    avg_power_w: float
    peak_current_a: float
    energy_j: float
    duration_s: float
    sample_count: int


@dataclass(frozen=True)
class PowerResult:
    """Complete result of a power capture."""

    summary: PowerSummary
    samples: list[PowerSample] = field(default_factory=list)
    per_layer: dict[str, Any] | None = None  # internal mode only
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class PowerDriver(Protocol):
    """Interface that each power measurement driver must implement."""

    @property
    def name(self) -> str:
        """Human-readable driver name."""
        ...

    @property
    def mode(self) -> PowerMode:
        """Whether this is an external or internal measurement driver."""
        ...

    def check_available(self) -> None:
        """Verify the driver's dependencies and hardware are available.

        Raises :class:`PowerError` if something is missing.
        """
        ...

    def capture(
        self,
        *,
        duration_s: float,
        io_voltage: float,
        **kwargs: Any,
    ) -> PowerResult:
        """Run a power capture for *duration_s* seconds.

        For external drivers, the firmware is expected to toggle a GPIO
        sync pin during the capture window.

        Raises :class:`PowerError` on failure.
        """
        ...

    def power_cycle(self, *, off_time_s: float = 0.5, settle_time_s: float = 1.0) -> None:
        """Cut and restore target power for a clean hardware reset.

        Only meaningful for external instruments that sit on the power rail
        (e.g. Joulescope).  Drivers that cannot power-cycle should raise
        :class:`PowerError`.

        Parameters
        ----------
        off_time_s : float
            How long to keep power off (seconds).
        settle_time_s : float
            How long to wait after restoring power for the target to boot.
        """
        ...

    def enable_passthrough(self) -> None:
        """Open the instrument and enable current passthrough.

        Closes the input relay so current flows through to the target board.
        The driver holds the device open until :meth:`disable_passthrough`
        is called.  Useful when no power capture is needed but the board
        must be powered via the instrument.
        """
        ...

    def disable_passthrough(self) -> None:
        """Release the instrument opened by :meth:`enable_passthrough`."""
        ...
