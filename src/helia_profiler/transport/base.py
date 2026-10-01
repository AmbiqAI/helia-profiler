"""``CaptureTransport`` protocol and shared backend plumbing.

A capture transport backend owns everything ``capture_pmu`` needs to turn a
running firmware image into a list of protocol lines: transport-specific setup
(arena/clock/marker resolution) and the reset-and-read sequence.  Each reader
cleans up its own resources before returning.

The protocol has two phases:

* :meth:`~CaptureTransport.prepare` — resolve transport-specific inputs from the
  :class:`~helia_profiler.pipeline.PipelineContext` and the shared
  :class:`CaptureArgs` (e.g. the RTT control-block address, the SWO trace
  clock, the USB marker).  Never touches hardware.
* :meth:`~CaptureTransport.collect` — run the reader: reset the target (the
  backend owns its own reset) and block until the HPX stream ends.  Returns
  the captured lines.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..errors import CaptureError
from ..vocab import Transport

if TYPE_CHECKING:
    from pathlib import Path

    from ..pipeline import PipelineContext
    from ..target.probe.base import ResetController

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
    #: ``None`` = unbounded (rely on heartbeats), matching ``HeartbeatConfig``.
    overall_timeout_s: float | None
    heartbeat_timeout_s: float
    build_dir: Path | None
    timing_raw: dict[str, float] = field(default_factory=dict)
    reset_controller: ResetController | None = None


@runtime_checkable
class CaptureTransport(Protocol):
    """Uniform backend interface ``capture_pmu`` drives, one per transport."""

    #: The ``vocab.Transport`` member this backend handles.
    transport: Transport

    def prepare(self, ctx: PipelineContext, args: CaptureArgs) -> None:
        """Resolve transport-specific inputs.  No hardware access."""
        ...

    def collect(self, ctx: PipelineContext) -> list[str]:
        """Reset the target and block until the HPX stream ends."""
        ...


class BaseCaptureTransport:
    """Convenience base that stashes the shared args in :meth:`prepare`.

    Subclasses set :attr:`transport` and implement :meth:`collect`.
    """

    transport: Transport

    def __init__(self) -> None:
        self._args: CaptureArgs | None = None

    def prepare(self, ctx: PipelineContext, args: CaptureArgs) -> None:
        self._args = args

    @property
    def prepared_args(self) -> CaptureArgs:
        """The shared capture args (present once :meth:`prepare` has run)."""
        if self._args is None:
            raise CaptureError(f"{type(self).__name__}.collect() requires prepare() to run first.")
        return self._args

    def collect(self, ctx: PipelineContext) -> list[str]:  # pragma: no cover
        raise NotImplementedError
