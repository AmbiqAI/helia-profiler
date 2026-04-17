"""Joulescope external power measurement driver.

Captures current/voltage from a Joulescope instrument while the target
firmware toggles a GPIO sync pin to bracket inference.  Only supports
whole-inference capture (not per-layer).
"""

from __future__ import annotations

import logging
from typing import Any

from ..errors import PowerError
from .base import PowerMode, PowerResult, PowerSample, PowerSummary

log = logging.getLogger("hpx")


class JoulescopeDriver:
    """External power driver using the Joulescope JS110/JS220."""

    @property
    def name(self) -> str:
        return "Joulescope"

    @property
    def mode(self) -> PowerMode:
        return PowerMode.EXTERNAL

    def check_available(self) -> None:
        try:
            import joulescope  # noqa: F401
        except ImportError as exc:
            raise PowerError(
                "Joulescope package not installed",
                hint="Install with: pip install 'helia-profiler[power]' or pip install joulescope",
            ) from exc

    def capture(
        self,
        *,
        duration_s: float,
        io_voltage: float,
        sampling_frequency: int = 1_000_000,
        **kwargs: Any,
    ) -> PowerResult:
        """Capture power data from a connected Joulescope for *duration_s*.

        The firmware should toggle the GPIO sync pin HIGH before inference
        and LOW after.  This driver records the full window and computes
        aggregate statistics.
        """
        try:
            import joulescope
        except ImportError as exc:
            raise PowerError(
                "Joulescope package not installed",
                hint="Install with: pip install joulescope",
            ) from exc

        log.info(
            "Opening Joulescope (duration=%.1fs, sample_rate=%d Hz)",
            duration_s,
            sampling_frequency,
        )

        try:
            device = joulescope.scan_require_one(name="Joulescope")
        except Exception as exc:
            raise PowerError(
                f"Failed to find Joulescope device: {exc}",
                hint="Ensure the Joulescope is connected via USB and powered on.",
            ) from exc

        samples: list[PowerSample] = []
        total_current = 0.0
        total_power = 0.0
        peak_current = 0.0

        try:
            with device:
                device.parameter_set("sampling_frequency", sampling_frequency)
                device.parameter_set("i_range", "auto")

                # Collect samples for the specified duration
                data = device.read(
                    duration=duration_s,
                    fields=["current", "voltage"],
                )

                current_data = data["signals"]["current"]["value"]
                voltage_data = data["signals"]["voltage"]["value"]
                sample_rate = data["signals"]["current"]["sample_frequency"]
                dt = 1.0 / sample_rate

                for i in range(len(current_data)):
                    t = i * dt
                    c = float(current_data[i])
                    v = float(voltage_data[i])
                    samples.append(PowerSample(timestamp_s=t, current_a=c, voltage_v=v))
                    total_current += c
                    total_power += c * v
                    if c > peak_current:
                        peak_current = c

        except PowerError:
            raise
        except Exception as exc:
            raise PowerError(
                f"Joulescope capture failed: {exc}",
                hint="Check USB connection and ensure no other software is using the device.",
            ) from exc

        n = len(samples)
        if n == 0:
            raise PowerError(
                "No samples captured",
                hint="Joulescope returned empty data — check the connection.",
            )

        avg_current = total_current / n
        avg_power = total_power / n
        energy = total_power * (duration_s / n)  # sum * dt

        summary = PowerSummary(
            avg_current_a=avg_current,
            avg_power_w=avg_power,
            peak_current_a=peak_current,
            energy_j=energy,
            duration_s=duration_s,
            sample_count=n,
        )

        log.info(
            "Joulescope: avg=%.3f mA, peak=%.3f mA, energy=%.6f J (%d samples)",
            avg_current * 1000,
            peak_current * 1000,
            energy,
            n,
        )

        return PowerResult(
            summary=summary,
            samples=samples,
            metadata={
                "driver": "joulescope",
                "sampling_frequency": sampling_frequency,
                "io_voltage": io_voltage,
            },
        )
