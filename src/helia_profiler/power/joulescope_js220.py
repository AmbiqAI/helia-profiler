"""Joulescope JS220 external power measurement driver.

Uses the ``pyjoulescope_driver`` package to communicate with JS220 devices.
The JS220 uses a publish/subscribe streaming model rather than the blocking
``device.read()`` API of the JS110.

Install: ``pip install pyjoulescope_driver``
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..errors import PowerError
from .base import PowerMode, PowerResult, PowerSample, PowerSummary

log = logging.getLogger("hpx")


class JoulescopeJS220Driver:
    """External power driver for Joulescope JS220."""

    @property
    def name(self) -> str:
        return "Joulescope JS220"

    @property
    def mode(self) -> PowerMode:
        return PowerMode.EXTERNAL

    def check_available(self) -> None:
        try:
            import pyjoulescope_driver  # noqa: F401
        except ImportError as exc:
            raise PowerError(
                "pyjoulescope_driver package not installed (required for JS220)",
                hint="Install with: pip install pyjoulescope_driver",
            ) from exc
        except Exception as exc:
            # numpy/pyjls ABI mismatch raises ValueError during import chain.
            raise PowerError(
                f"pyjoulescope_driver failed to import: {exc}",
                hint="This is usually a numpy ABI mismatch. Try: "
                "pip install --force-reinstall 'pyjoulescope_driver' 'pyjls' "
                "or pin 'numpy<2'.",
            ) from exc

    def capture(
        self,
        *,
        duration_s: float,
        io_voltage: float,
        sampling_frequency: int = 1_000_000,
        **kwargs: Any,
    ) -> PowerResult:
        """Capture power data from a connected Joulescope JS220.

        Uses the statistics stream (1 Hz accumulators) for aggregate data,
        avoiding the need to buffer millions of raw samples on the host.
        """
        try:
            import pyjoulescope_driver as jsdrv
        except ImportError as exc:
            raise PowerError(
                "pyjoulescope_driver not installed",
                hint="Install with: pip install pyjoulescope_driver",
            ) from exc

        log.info(
            "Opening Joulescope JS220 (duration=%.1fs)",
            duration_s,
        )

        try:
            driver = jsdrv.Driver()
            driver.open()
        except Exception as exc:
            raise PowerError(
                f"Failed to open pyjoulescope_driver: {exc}",
                hint="Ensure the JS220 is connected via USB.",
            ) from exc

        try:
            device_paths = [
                p for p in driver.device_paths() if "js220" in p.lower()
            ]
            if not device_paths:
                raise PowerError(
                    "No Joulescope JS220 found",
                    hint="Ensure the JS220 is connected. "
                    "Use 'joulescope-js110' driver for a JS110.",
                )

            device_path = device_paths[0]
            log.info("JS220 device: %s", device_path)
            driver.open(device_path)

            stats_accum: list[dict[str, Any]] = []

            def _on_stats(topic: str, value: Any) -> None:
                stats_accum.append(value)

            # Subscribe to 2 Hz statistics (current + voltage summaries)
            stats_topic = f"{device_path}/s/stats/value"
            driver.subscribe(stats_topic, "pub", _on_stats)

            # Enable current and voltage streaming + statistics
            driver.publish(f"{device_path}/s/i/range/mode", "auto")
            driver.publish(f"{device_path}/s/stats/ctrl", 1)

            time.sleep(duration_s)

            driver.publish(f"{device_path}/s/stats/ctrl", 0)
            driver.unsubscribe(stats_topic, _on_stats)

            if not stats_accum:
                raise PowerError(
                    "No statistics received from JS220",
                    hint="Check USB connection and firmware version.",
                )

            # Process statistics packets into aggregate metrics
            samples, summary = _process_js220_stats(
                stats_accum, duration_s, io_voltage
            )

            log.info(
                "JS220: avg=%.3f mA, peak=%.3f mA, energy=%.6f J (%d stat packets)",
                summary.avg_current_a * 1000,
                summary.peak_current_a * 1000,
                summary.energy_j,
                len(stats_accum),
            )

            return PowerResult(
                summary=summary,
                samples=samples,
                metadata={
                    "driver": "joulescope-js220",
                    "device": device_path,
                    "io_voltage": io_voltage,
                    "stat_packets": len(stats_accum),
                },
            )

        except PowerError:
            raise
        except Exception as exc:
            raise PowerError(
                f"JS220 capture failed: {exc}",
                hint="Check USB connection and ensure no other software is using the device.",
            ) from exc
        finally:
            try:
                driver.close()
            except Exception:
                pass

    def power_cycle(self, *, off_time_s: float = 0.5, settle_time_s: float = 1.0) -> None:
        """Cut and restore target power via the JS220 current range control.

        Setting the current range mode to ``off`` opens the input relay,
        disconnecting the target from its power supply.  Restoring to
        ``auto`` re-enables it, giving a clean hardware reset.
        """
        import pyjoulescope_driver as jsdrv

        log.info(
            "Power-cycle reset via JS220 (off=%.1fs, settle=%.1fs)",
            off_time_s,
            settle_time_s,
        )

        try:
            driver = jsdrv.Driver()
            driver.open()
        except Exception as exc:
            raise PowerError(
                f"Failed to open JS220 for power cycle: {exc}",
                hint="Ensure the JS220 is connected via USB.",
            ) from exc

        try:
            device_paths = [
                p for p in driver.device_paths() if "js220" in p.lower()
            ]
            if not device_paths:
                raise PowerError(
                    "No JS220 found for power cycle",
                    hint="Ensure the JS220 is connected.",
                )

            device_path = device_paths[0]
            driver.open(device_path)

            driver.publish(f"{device_path}/s/i/range/mode", "off")
            log.info("Target power OFF")
            time.sleep(off_time_s)
            driver.publish(f"{device_path}/s/i/range/mode", "auto")
            log.info("Target power ON — waiting %.1fs for boot", settle_time_s)
            time.sleep(settle_time_s)
        except PowerError:
            raise
        except Exception as exc:
            raise PowerError(
                f"JS220 power cycle failed: {exc}",
                hint="Check USB connection.",
            ) from exc
        finally:
            try:
                driver.close()
            except Exception:
                pass

        log.info("Power-cycle reset complete")

    def enable_passthrough(self) -> None:
        """Open the JS220 and enable current passthrough (close input relay)."""
        import pyjoulescope_driver as jsdrv

        try:
            self._pt_driver = jsdrv.Driver()
            self._pt_driver.open()
        except Exception as exc:
            raise PowerError(
                f"Failed to open JS220: {exc}",
                hint="Ensure the JS220 is connected via USB.",
            ) from exc

        device_paths = [
            p for p in self._pt_driver.device_paths() if "js220" in p.lower()
        ]
        if not device_paths:
            self._pt_driver.close()
            raise PowerError(
                "No Joulescope JS220 found",
                hint="Ensure the JS220 is connected via USB.",
            )

        self._pt_device_path = device_paths[0]
        self._pt_driver.open(self._pt_device_path)
        self._pt_driver.publish(f"{self._pt_device_path}/s/i/range/mode", "auto")
        log.info("JS220 passthrough enabled on %s", self._pt_device_path)

    def disable_passthrough(self) -> None:
        """Release the JS220 opened by :meth:`enable_passthrough`."""
        driver = getattr(self, "_pt_driver", None)
        if driver is not None:
            try:
                driver.close()
            except Exception:
                pass
            self._pt_driver = None
            log.info("JS220 passthrough released")


def _process_js220_stats(
    stats: list[dict[str, Any]],
    duration_s: float,
    io_voltage: float,
) -> tuple[list[PowerSample], PowerSummary]:
    """Extract current/voltage from JS220 statistics packets.

    JS220 statistics packets contain accumulators with mean, min, max, std
    for current and voltage over each reporting interval.
    """
    import numpy as np

    currents: list[float] = []
    voltages: list[float] = []
    timestamps: list[float] = []
    dt = duration_s / max(len(stats), 1)

    for i, pkt in enumerate(stats):
        # The statistics packet structure varies by pyjoulescope_driver version.
        # Common shapes: dict with 'signals' → 'current'/'voltage' → 'avg'
        # or nested arrays.  Handle both gracefully.
        try:
            if isinstance(pkt, dict):
                sig = pkt.get("signals", pkt)
                i_val = sig.get("current", {}).get("avg", 0.0)
                v_val = sig.get("voltage", {}).get("avg", io_voltage)
            else:
                # numpy structured array fallback
                i_val = float(pkt[0]) if len(pkt) > 0 else 0.0
                v_val = float(pkt[1]) if len(pkt) > 1 else io_voltage
        except (TypeError, IndexError, KeyError):
            i_val = 0.0
            v_val = io_voltage

        currents.append(float(i_val))
        voltages.append(float(v_val))
        timestamps.append(i * dt)

    currents_np = np.array(currents)
    voltages_np = np.array(voltages)
    power_np = currents_np * voltages_np

    avg_current = float(np.nanmean(currents_np))
    avg_power = float(np.nanmean(power_np))
    peak_current = float(np.nanmax(currents_np))
    energy = float(np.nansum(power_np)) * dt

    samples = [
        PowerSample(timestamp_s=t, current_a=c, voltage_v=v)
        for t, c, v in zip(timestamps, currents, voltages)
    ]

    summary = PowerSummary(
        avg_current_a=avg_current,
        avg_power_w=avg_power,
        peak_current_a=peak_current,
        energy_j=energy,
        duration_s=duration_s,
        sample_count=len(stats),
    )

    return samples, summary
