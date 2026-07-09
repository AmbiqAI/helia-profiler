"""Host/device synchronisation contract for gated power capture.

This is the vendor-neutral lock-step handshake between the host (running ``hpx``
on a power monitor) and the device firmware. It generalises the original
single-wire "gate" sync into a 3-wire protocol so the host can deterministically
release the device and observe a clean window instead of racing a free-running
firmware loop:

    gate  (device -> host)  : asserted high for the duration of the timed window
    state (device -> host)  : ready / fault flag the host can read out-of-window
    go    (host -> device)  : host tells firmware "I am armed, you may run"

A :class:`SyncController` owns the host side of those three wires. Power
monitors with at least one GPO advertise themselves as lock-step capable and
return a real controller; monitors with gate-only (1-wire) wiring return a
:class:`NullSyncController` that degrades to the historical free-run behaviour.

The contract is deliberately monitor- and engine-agnostic: Joulescope JS110 /
JS220 wire it to GPI/GPO, but a UART-only or another instrument can implement
the same protocol. The matching firmware side lives in the ``_sync.j2`` macros.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class DeviceState(Enum):
    """Coarse device state inferred from the state/gate wires."""

    UNKNOWN = "unknown"
    READY = "ready"
    RUNNING = "running"
    DONE = "done"
    FAULT = "fault"


@dataclass(frozen=True)
class SyncWiring:
    """Pin/channel assignment for the 3-wire lock-step handshake.

    Indices are host-side instrument channels (e.g. Joulescope INPUT0/INPUT1
    and OUTPUT0). ``go_output_index < 0`` or ``lockstep=False`` selects the
    degraded gate-only path.
    """

    lockstep: bool = False
    gate_input_index: int = 0
    state_input_index: int = 1
    go_output_index: int = 0


@runtime_checkable
class SyncController(Protocol):
    """Host side of the lock-step handshake with the device firmware."""

    @property
    def lockstep(self) -> bool:
        """True if the host can release/observe the device over 3 wires."""
        ...

    def arm(self) -> None:
        """Prepare the host side (de-assert GO) before the device may run."""
        ...

    def wait_ready(self, *, timeout_s: float) -> bool:
        """Block until the device signals READY; return False on timeout."""
        ...

    def signal_go(self) -> None:
        """Tell the device the host is armed and observing; release the window."""
        ...

    def release_go(self) -> None:
        """Drop the GO wire once the device has latched it (gate observed high).

        A host-driven GO line held high through the measured window can
        parasitically backfeed the target through the GPIO pad network,
        displacing real supply current around the instrument's sense path
        (several mA observed on an AP510 EVB — enough to drive the net
        measured current negative).  The firmware only level-samples GO
        before the window, so dropping it at gate-rise is always safe.
        """
        ...

    def read_state(self) -> DeviceState:
        """Sample the device state wire (ready/fault) outside the window."""
        ...

    def release(self) -> None:
        """De-assert GO and free instrument resources."""
        ...


class NullSyncController:
    """Gate-only fallback: no GO/state wires, device free-runs (legacy path)."""

    @property
    def lockstep(self) -> bool:
        return False

    def arm(self) -> None:  # pragma: no cover - trivial
        pass

    def wait_ready(self, *, timeout_s: float) -> bool:  # pragma: no cover - trivial
        return True

    def signal_go(self) -> None:  # pragma: no cover - trivial
        pass

    def release_go(self) -> None:  # pragma: no cover - trivial
        pass

    def read_state(self) -> DeviceState:  # pragma: no cover - trivial
        return DeviceState.UNKNOWN

    def release(self) -> None:  # pragma: no cover - trivial
        pass
