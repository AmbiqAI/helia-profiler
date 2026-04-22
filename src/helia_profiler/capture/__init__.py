"""Data capture from target hardware.

- ``capture_pmu``: Read PMU / DWT counters and per-layer breakdown from the
  target via SWO.
- ``capture_power``: Record current/voltage traces via the configured power
  driver (external Joulescope, on-device, etc.).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..errors import CaptureError

if TYPE_CHECKING:
    from ..pipeline import PipelineContext
    from ..power.base import PowerResult
    from ..results import PmuResult

log = logging.getLogger("hpx")


def capture_pmu(ctx: PipelineContext) -> PmuResult:
    """Read PMU data from the target via serial port.

    Returns a :class:`PmuResult` with firmware metadata, per-preset breakdowns,
    and merged per-layer results.
    """
    from .parser import parse_firmware_output
    from .serial_reader import capture_swo_output

    # Use build_dir from context (set by stage 4) — no re-derivation
    build_dir = ctx.build_dir

    jlink_serial = ctx.config.target.jlink_serial

    # Resolve J-Link device string from the SoC registry — hard error if missing
    if ctx.soc is None or not ctx.soc.jlink_device:
        raise CaptureError(
            "No J-Link device string — platform resolution did not run.",
            hint="Ensure stage 1 (resolve_platform) runs before capture.",
        )
    jlink_device = ctx.soc.jlink_device

    lines = capture_swo_output(
        build_dir=build_dir,
        jlink_serial=jlink_serial,
        jlink_device=jlink_device,
    )
    if not lines:
        raise CaptureError(
            "No data captured from serial port",
            hint="Ensure the firmware is running. Try resetting the board.",
        )

    result = parse_firmware_output(lines)
    if not result.layers:
        raise CaptureError(
            "No layer data parsed from firmware output",
            hint="Check that the firmware is printing HPX protocol data.",
        )

    return result


def capture_power(ctx: PipelineContext) -> PowerResult:
    """Record a power trace using the configured power driver.

    Returns a :class:`PowerResult` directly — no intermediate dict wrapping.
    """
    from ..power import get_driver

    driver_name = ctx.config.power.driver
    driver = get_driver(driver_name)

    # Verify driver is usable
    driver.check_available()

    return driver.capture(
        duration_s=ctx.config.power.duration_s,
        io_voltage=ctx.config.power.io_voltage,
    )
