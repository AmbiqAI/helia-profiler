"""Data capture from target hardware.

Provides two capture interfaces:
- ``capture_pmu``: Read PMU / DWT counters and per-layer breakdown from the
  target via serial (USB-CDC) or SWO.
- ``capture_power``: Record current/voltage traces via Joulescope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..errors import CaptureError, PowerError

if TYPE_CHECKING:
    from ..pipeline import PipelineContext


def capture_pmu(ctx: PipelineContext) -> dict[str, Any]:
    """Read PMU data from the target after firmware completes profiling.

    Returns a dict with at minimum:
    - ``"summary"``: dict of aggregate counters (total cycles, instructions, etc.)
    - ``"layers"``:  list of per-layer dicts (one per operator invocation)
    - ``"meta"``:    dict with board, SoC, engine, model info
    """
    assert ctx.soc is not None

    # TODO: Open serial port (ctx.config.target.board → channel mapping)
    # TODO: Send start command / wait for firmware to complete
    # TODO: Parse structured output (JSON lines or binary protocol)
    # TODO: Validate data integrity (expected layer count, checksum, etc.)

    raise CaptureError(
        "PMU data capture not yet implemented.",
        hint="This feature is under development.",
    )


def capture_power(ctx: PipelineContext) -> dict[str, Any]:
    """Record a power trace via Joulescope for the configured duration.

    Returns a dict with:
    - ``"samples"``: list or array of (timestamp_s, current_a, voltage_v) tuples
    - ``"summary"``: dict with avg_current_a, avg_power_w, energy_j, duration_s
    """
    try:
        import joulescope  # noqa: F401
    except ImportError as exc:
        raise PowerError(
            "Joulescope package not installed.",
            hint="Install with: pip install joulescope",
        ) from exc

    # TODO: Open Joulescope device
    # TODO: Configure sampling rate and voltage
    # TODO: Record for ctx.config.power.duration_s seconds
    # TODO: Compute summary statistics

    raise PowerError(
        "Power capture not yet implemented.",
        hint="This feature is under development.",
    )
