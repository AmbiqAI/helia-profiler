"""A capture guard for an operator at the bench, who vouches for the board behind the probe."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..errors import ConfigError

if TYPE_CHECKING:
    from ..fixtures.target import FixtureTarget


@dataclass(frozen=True)
class OperatorGuard:
    """Capture authority from an operator who names the board and the J-Link probe on it.

    HPX cannot prove which physical board a probe is wired to: the core's
    CPUID and the probe list name a SoC and a serial, not a board. With this
    guard the operator vouches for the board and holds it for the whole
    capture, as ``hpx profile`` assumes. :meth:`verify_target` refuses a
    capture whose board or probe serial differs from the ones the operator
    named, and :meth:`check` allows every step. A harness that can prove the
    board and lock it passes its own
    :class:`~helia_profiler.FixtureCaptureGuard` instead.
    """

    board: str
    jlink_serial: str

    def __post_init__(self) -> None:
        for name in ("board", "jlink_serial"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ConfigError(f"OperatorGuard requires an explicit {name}")

    def check(self, *, require_free: bool, remaining_s: float) -> None:
        """Allow the step: the operator holds the board for the whole capture."""
        del require_free, remaining_s

    def verify_target(self, *, target: FixtureTarget, jlink_serial: str) -> None:
        """Refuse a target board or probe serial other than the operator's.

        Raises:
            ConfigError: The board or the serial differs from the operator's.
        """
        if target.board != self.board:
            raise ConfigError(
                f"The build targets board {target.board}; the operator named {self.board}",
                hint="Name the board the build was made for, and wire that board to the probe.",
            )
        if jlink_serial != self.jlink_serial:
            raise ConfigError(
                f"The capture uses J-Link {jlink_serial}; the operator named {self.jlink_serial}"
            )
