"""Joulescope external power measurement driver.

Captures current/voltage from a Joulescope instrument while the target
firmware toggles a GPIO sync pin to bracket inference.  Only supports
whole-inference capture (not per-layer).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..errors import PowerError
from .base import PowerMode, PowerResult, PowerSample, PowerSummary

log = logging.getLogger("hpx")


def _scan_and_open(serial: str | None = None):
    """Scan for a Joulescope, optionally filtered by serial, and open it.

    Returns an *opened* device. Translates the common failure modes into
    actionable :class:`PowerError` messages:

    * No Joulescope plugged in.
    * Multiple Joulescopes plugged in but no ``serial`` specified.
    * The requested ``serial`` is not present.
    * The device is plugged in but already claimed by another process
      (Joulescope GUI, a stuck ``jsdrv``, a leaked Python handle, etc.) —
      surfaced via the libusb ``-3`` / "claim_interface" error.
    """
    import joulescope

    try:
        devices = joulescope.scan(name="Joulescope")
    except Exception as exc:  # pragma: no cover - depends on env
        raise PowerError(
            f"Joulescope scan failed: {exc}",
            hint="Check USB connection and pyjoulescope_driver install.",
        ) from exc

    if not devices:
        raise PowerError(
            "No Joulescope detected",
            hint="Plug in the Joulescope and ensure it is powered on.",
        )

    if serial is not None:
        wanted = str(serial).lstrip("0") or "0"
        matched = [
            d for d in devices
            if wanted in (getattr(d, "device_path", "") or "")
            or wanted in str(getattr(d, "serial_number", "") or "")
        ]
        if not matched:
            paths = [getattr(d, "device_path", "?") for d in devices]
            raise PowerError(
                f"Joulescope serial '{serial}' not found among connected devices",
                hint=f"Connected devices: {', '.join(paths)}. "
                "Update power.serial / --js-serial to match.",
            )
        device = matched[0]
    elif len(devices) > 1:
        paths = [getattr(d, "device_path", "?") for d in devices]
        raise PowerError(
            f"{len(devices)} Joulescopes connected — please disambiguate",
            hint=(
                f"Set power.serial (or --js-serial) to one of: {', '.join(paths)}."
            ),
        )
    else:
        device = devices[0]

    try:
        device.open()
    except Exception as exc:
        msg = str(exc).lower()
        path = getattr(device, "device_path", "?")
        if (
            "claim_interface" in msg
            or "libusb" in msg
            or "jsdrv_open" in msg
            or "-3" in msg
            or "access" in msg
        ):
            raise PowerError(
                f"Joulescope {path} is already in use by another process",
                hint=(
                    "Close the Joulescope desktop app or any other process "
                    "holding the device, then retry. On macOS you can also "
                    "run 'pkill -f jsdrv' to release stuck handles."
                ),
            ) from exc
        raise PowerError(
            f"Failed to open Joulescope {path}: {exc}",
            hint="Check USB connection and re-plug the device if needed.",
        ) from exc

    return device


class JoulescopeDriver:
    """External power driver using the Joulescope JS110/JS220."""

    def __init__(self, *, serial: str | None = None) -> None:
        self._serial = serial

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
        except Exception as exc:
            # Common case: pyjls/numpy ABI mismatch surfaces as ValueError
            # "numpy.dtype size changed..." during import chain.
            raise PowerError(
                f"Joulescope package failed to import: {exc}",
                hint="This is usually a numpy ABI mismatch. Try: "
                "pip install --force-reinstall 'joulescope' 'pyjls' "
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

        device = _scan_and_open(self._serial)

        samples: list[PowerSample] = []
        total_current = 0.0
        total_power = 0.0
        peak_current = 0.0

        try:
            try:
                device.parameter_set("sampling_frequency", sampling_frequency)
                device.parameter_set("i_range", "auto")

                # read() returns an ndarray of shape (N, 2): col 0 = current, col 1 = voltage
                data = device.read(duration=duration_s)
                import numpy as np

                current_data = data[:, 0]  # amps
                voltage_data = data[:, 1]  # volts
                n = len(current_data)
                dt = duration_s / n

                power_data = current_data * voltage_data
                avg_current = float(np.nanmean(current_data))
                avg_power = float(np.nanmean(power_data))
                peak_current = float(np.nanmax(current_data))
                energy = float(np.nansum(power_data)) * dt

                # Build sparse samples list (subsample for memory)
                step = max(1, n // 10000)
                for i in range(0, n, step):
                    t = i * dt
                    samples.append(PowerSample(
                        timestamp_s=t,
                        current_a=float(current_data[i]),
                        voltage_v=float(voltage_data[i]),
                    ))

            except PowerError:
                raise
            except Exception as exc:
                raise PowerError(
                    f"Joulescope capture failed: {exc}",
                    hint="Check USB connection and ensure no other software is using the device.",
                ) from exc
        finally:
            try:
                device.close()
            except Exception:
                pass

        n = len(samples)
        if n == 0:
            raise PowerError(
                "No samples captured",
                hint="Joulescope returned empty data — check the connection.",
            )

        summary = PowerSummary(
            avg_current_a=avg_current,
            avg_power_w=avg_power,
            peak_current_a=peak_current,
            energy_j=energy,
            duration_s=duration_s,
            sample_count=int(current_data.shape[0]) if 'current_data' in dir() else n,
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

    def power_cycle(self, *, off_time_s: float = 0.5, settle_time_s: float = 1.0) -> None:
        """Cut and restore target power via the Joulescope current shunt.

        Setting ``i_range`` to ``off`` opens the relay on the Joulescope,
        disconnecting the target from its power supply.  Restoring to
        ``auto`` re-enables it, giving a clean hardware reset with no
        debug-domain overhead.
        """
        import joulescope

        log.info(
            "Power-cycle reset via Joulescope (off=%.1fs, settle=%.1fs)",
            off_time_s,
            settle_time_s,
        )

        device = _scan_and_open(self._serial)

        try:
            device.parameter_set("i_range", "off")
            log.info("Target power OFF")
            time.sleep(off_time_s)
            device.parameter_set("i_range", "auto")
            log.info("Target power ON — waiting %.1fs for boot", settle_time_s)
            time.sleep(settle_time_s)
        except PowerError:
            raise
        except Exception as exc:
            raise PowerError(
                f"Joulescope power cycle failed: {exc}",
                hint="Check USB connection.",
            ) from exc
        finally:
            try:
                device.close()
            except Exception:
                pass

        log.info("Power-cycle reset complete")

    def enable_passthrough(self) -> None:
        """Open the Joulescope and enable current passthrough (close relay)."""
        device = _scan_and_open(self._serial)
        try:
            device.parameter_set("i_range", "auto")
        except Exception as exc:
            try:
                device.close()
            except Exception:
                pass
            raise PowerError(
                f"Failed to enable Joulescope passthrough: {exc}",
                hint="Check USB connection.",
            ) from exc
        self._pt_device = device
        log.info("Joulescope passthrough enabled")

    def disable_passthrough(self) -> None:
        """Release the Joulescope opened by :meth:`enable_passthrough`."""
        device = getattr(self, "_pt_device", None)
        if device is not None:
            try:
                device.close()
            except Exception:
                pass
            self._pt_device = None
            log.info("Joulescope passthrough released")
