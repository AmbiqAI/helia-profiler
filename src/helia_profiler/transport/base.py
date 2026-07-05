"""``CaptureTransport`` protocol and shared backend plumbing.

A capture transport backend owns everything ``capture_pmu`` needs to turn a
running firmware image into a list of protocol lines: transport-specific setup
(arena/clock/marker resolution), the reset-and-read sequence, and teardown.

The protocol deliberately mirrors the phases ``capture_pmu`` already drives:

* :meth:`~CaptureTransport.prepare` — resolve transport-specific inputs from the
  :class:`~helia_profiler.pipeline.PipelineContext` and the shared
  :class:`CaptureArgs` (e.g. the RTT control-block address, the SWO trace
  clock, the USB marker).  Never touches hardware.
* :meth:`~CaptureTransport.start` — optional pre-collection hook.  The baseline
  readers fuse reset+go into their single blocking read, so this is a no-op for
  every current backend and exists only to keep the lifecycle explicit.
* :meth:`~CaptureTransport.collect` — run the reader: reset the target (the
  backend still owns its own reset, exactly as before) and block until the HPX
  stream ends.  Returns the captured lines.
* :meth:`~CaptureTransport.close` — release any resources held past
  :meth:`collect`.  A no-op for the current readers, which clean up internally.

Constraint attributes
---------------------
``honors_keep_attached`` records whether the transport forwards the SoC's
``requires_attached_probe_for_cycles`` requirement to its reader.  RTT and SWO
always reset-and-reattach (they release the probe), so they neither accept nor
forward ``keep_attached``.  UART and USB CDC hold the probe attached for the
whole capture when the SoC gates DWT->CYCCNT behind the debug power domain
(Apollo3/Apollo4), so they forward it.  This is exactly the invariant the
reset-ownership contract pins — it is not new policy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..config import Transport

if TYPE_CHECKING:
    from ..pipeline import PipelineContext

log = logging.getLogger("hpx")


@dataclass
class CaptureArgs:
    """Common inputs every transport backend needs from ``capture_pmu``.

    Bundled so backends share one uniform call signature instead of an if/elif
    ladder that threads a different kwarg subset through each transport.
    """

    jlink_serial: str | None
    jlink_device: str
    keep_debugger_attached: bool
    overall_timeout_s: float
    heartbeat_timeout_s: float
    build_dir: object
    timing_raw: dict[str, float] = field(default_factory=dict)
    reset_controller: object | None = None


@runtime_checkable
class CaptureTransport(Protocol):
    """Uniform backend interface ``capture_pmu`` drives, one per transport."""

    #: The ``config.Transport`` member this backend handles.
    transport: Transport
    #: Whether the backend forwards the SoC keep-attached requirement (see
    #: module docstring).  ``True`` for UART/USB CDC, ``False`` for RTT/SWO.
    honors_keep_attached: bool

    def prepare(self, ctx: PipelineContext, args: CaptureArgs) -> None:
        """Resolve transport-specific inputs.  No hardware access."""
        ...

    def start(self, ctx: PipelineContext) -> None:
        """Optional pre-collection hook (no-op for current readers)."""
        ...

    def collect(self, ctx: PipelineContext) -> list[str]:
        """Reset the target and block until the HPX stream ends."""
        ...

    def close(self) -> None:
        """Release resources held past :meth:`collect` (no-op today)."""
        ...


class BaseCaptureTransport:
    """Convenience base implementing the trivial lifecycle phases.

    Subclasses set :attr:`transport` / :attr:`honors_keep_attached` and
    implement :meth:`collect`; ``prepare`` stashes the shared args and
    ``start`` / ``close`` default to no-ops.
    """

    transport: Transport
    honors_keep_attached: bool = False

    def __init__(self) -> None:
        self._args: CaptureArgs | None = None

    def prepare(self, ctx: PipelineContext, args: CaptureArgs) -> None:
        self._args = args

    def start(self, ctx: PipelineContext) -> None:
        return None

    def collect(self, ctx: PipelineContext) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:
        return None
