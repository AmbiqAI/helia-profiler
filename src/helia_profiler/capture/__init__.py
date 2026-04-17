"""Data capture from target hardware.

Provides two capture interfaces:
- ``capture_pmu``: Read PMU / DWT counters and per-layer breakdown from the
  target via serial (USB-CDC) or SWO.
- ``capture_power``: Record current/voltage traces via the configured power
  driver (external Joulescope, on-device, etc.).
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
    """Record a power trace using the configured power driver.

    Returns a dict with:
    - ``"result"``: :class:`PowerResult` from the driver
    - ``"driver"``: driver name
    - ``"mode"``:   "external" or "internal"
    """
    from ..power import get_driver

    driver_name = ctx.config.power.driver
    driver = get_driver(driver_name)

    # Verify driver is usable
    driver.check_available()

    result = driver.capture(
        duration_s=ctx.config.power.duration_s,
        io_voltage=ctx.config.power.io_voltage,
    )

    return {
        "result": result,
        "driver": driver.name,
        "mode": driver.mode.value,
        "summary": {
            "avg_current_a": result.summary.avg_current_a,
            "avg_power_w": result.summary.avg_power_w,
            "peak_current_a": result.summary.peak_current_a,
            "energy_j": result.summary.energy_j,
            "duration_s": result.summary.duration_s,
            "sample_count": result.summary.sample_count,
        },
    }
