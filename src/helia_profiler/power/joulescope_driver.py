"""Joulescope external power measurement driver (unified JS110 + JS220).

Backed by the ``pyjoulescope_driver`` package, which supports both Joulescope
families via the same publish/subscribe API.  The device family is detected
from the device path (``u/js110/...`` vs ``u/js220/...``) and the small
number of family-specific topic names is dispatched internally.

Replaces the previous split driver implementation that used the legacy
``joulescope`` package for JS110 and ``pyjoulescope_driver`` for JS220.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..errors import PowerError
from .base import PowerMode, PowerResult, PowerSample, PowerSummary

log = logging.getLogger("hpx")

_OPEN_RETRY_TIMEOUT_S = 3.0
_OPEN_RETRY_INTERVAL_S = 0.25


# ---------------------------------------------------------------------------
# Family-specific topic / value tables
# ---------------------------------------------------------------------------

#: Stats topic per family.  JS110 has an always-on instrument-side stats
#: stream (``s/sstats/value``) that does not need to be enabled; JS220 uses
#: the host-side ``s/stats/value`` stream gated by ``s/stats/ctrl``.
_STATS_TOPIC = {
    "js110": "s/sstats/value",
    "js220": "s/stats/value",
}

#: ``(topic, on_value, off_value)`` triple for enabling stats streaming.
#: For JS110 the stream is on by default and the entry is ``None`` to mean
#: "no-op".  For JS220 we toggle ``s/stats/ctrl``.
_STATS_CTRL = {
    "js110": None,
    "js220": ("s/stats/ctrl", 1, 0),
}

#: ``(topic, off_value, on_value)`` for cutting / restoring target power.
#: JS110: ``s/i/range/select`` (0 = off, 128 = auto).
#: JS220: ``s/i/range/mode`` ('off' / 'auto').
_POWER_CYCLE = {
    "js110": ("s/i/range/select", 0, 128),
    "js220": ("s/i/range/mode", "off", "auto"),
}


def _family_from_path(device_path: str) -> str:
    if "js110" in device_path.lower():
        return "js110"
    if "js220" in device_path.lower():
        return "js220"
    raise PowerError(
        f"Unsupported Joulescope device path: {device_path}",
        hint="This driver supports JS110 and JS220 only.",
    )


# ---------------------------------------------------------------------------
# Process-wide pyjoulescope_driver.Driver singleton
#
# ``pyjoulescope_driver`` is implemented in C/Cython and is designed for a
# single long-lived ``Driver`` instance per process.  Constructing and
# ``finalize()``-ing it repeatedly (e.g. once per capture, once per power-
# cycle) leads to USB-state confusion and, in practice, hard segfaults on
# macOS.  We keep one shared instance, opened lazily on first use, and
# released only at interpreter shutdown via ``atexit``.
# ---------------------------------------------------------------------------

_shared_driver: Any = None


def _get_shared_driver() -> Any:
    global _shared_driver
    if _shared_driver is not None:
        return _shared_driver
    import atexit

    try:
        import pyjoulescope_driver as jsdrv
    except ImportError as exc:
        raise PowerError(
            "pyjoulescope_driver package not installed",
            hint="pip install pyjoulescope_driver",
        ) from exc

    try:
        drv = jsdrv.Driver()
    except Exception as exc:
        raise PowerError(
            f"Failed to initialise pyjoulescope_driver: {exc}",
            hint="Ensure the Joulescope is connected via USB.",
        ) from exc

    def _finalize() -> None:
        try:
            drv.finalize()
        except Exception:
            pass

    atexit.register(_finalize)
    _shared_driver = drv
    return drv


def _is_device_busy_error(message: str) -> bool:
    message = message.lower()
    return (
        "claim" in message
        or "libusb" in message
        or "-3" in message
        or "access" in message
        or "in_use" in message
        or "busy" in message
    )


def _open_device(serial: str | None) -> tuple[Any, str, str]:
    """Open the selected device on the shared driver, returning ``(driver, path, family)``.

    The caller must release the device with :func:`_close_device` (or ignore
    that step if the device handle should remain open across calls — e.g.
    passthrough).
    """
    drv = _get_shared_driver()

    try:
        paths = list(drv.device_paths())
    except Exception as exc:
        raise PowerError(
            f"Joulescope enumeration failed: {exc}",
            hint="Check USB connection.",
        ) from exc

    if not paths:
        raise PowerError(
            "No Joulescope detected",
            hint="Plug in a Joulescope (JS110 or JS220) and ensure it is powered on.",
        )

    if serial is not None:
        wanted = str(serial).lstrip("0") or "0"
        matched = [p for p in paths if wanted in p]
        if not matched:
            raise PowerError(
                f"Joulescope serial '{serial}' not found among connected devices",
                hint=f"Connected devices: {', '.join(paths)}. "
                "Update power.serial / --js-serial to match.",
            )
        device_path = matched[0]
    elif len(paths) > 1:
        raise PowerError(
            f"{len(paths)} Joulescopes connected — please disambiguate",
            hint=f"Set power.serial / --js-serial to one of: {', '.join(paths)}",
        )
    else:
        device_path = paths[0]

    family = _family_from_path(device_path)

    deadline = time.monotonic() + _OPEN_RETRY_TIMEOUT_S
    while True:
        try:
            drv.open(device_path)
            break
        except Exception as exc:
            msg = str(exc).lower()
            if _is_device_busy_error(msg):
                if time.monotonic() < deadline:
                    log.warning(
                        "Joulescope %s busy during open; retrying in %.2fs",
                        device_path,
                        _OPEN_RETRY_INTERVAL_S,
                    )
                    time.sleep(_OPEN_RETRY_INTERVAL_S)
                    continue
                raise PowerError(
                    f"Joulescope {device_path} is already in use by another process",
                    hint=(
                        "Close the Joulescope desktop app or any other process "
                        "holding the device, then retry. On macOS you can also "
                        "run 'pkill -f jsdrv' to release stuck handles."
                    ),
                ) from exc
            # Idempotent re-open is OK; treat "already open" as success.
            if "already" in msg or "open" in msg:
                log.debug("Joulescope %s already open — reusing handle", device_path)
                break
            raise PowerError(
                f"Failed to open Joulescope {device_path}: {exc}",
                hint="Check USB connection and re-plug the device if needed.",
            ) from exc

    log.info("Joulescope opened: %s (%s)", device_path, family.upper())
    return drv, device_path, family


def _close_device(drv: Any, device_path: str) -> None:
    try:
        drv.close(device_path)
    except Exception:
        pass


def _extract_scalar(node: Any, default: float = 0.0) -> float:
    """Return a float from an stats sub-node.

    The ``pyjoulescope_driver`` stats packet wraps numeric values in a
    ``{'value': <number>, 'units': <str>}`` dict.  Older packets (and the
    JS220 host-side stream variant) use bare floats.  Handle both.
    """
    if isinstance(node, dict):
        v = node.get("value", default)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default
    try:
        return float(node)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


class JoulescopeDriver:
    """External power driver for Joulescope JS110 and JS220.

    Always uses :mod:`pyjoulescope_driver`.  The device family is auto-
    detected from the enumerated device path.
    """

    def __init__(self, *, serial: str | None = None) -> None:
        self._serial = serial

    @property
    def name(self) -> str:
        return "Joulescope"

    @property
    def mode(self) -> PowerMode:
        return PowerMode.EXTERNAL

    # ------------------------------------------------------------------
    # Availability check
    # ------------------------------------------------------------------

    def check_available(self) -> None:
        try:
            import pyjoulescope_driver  # noqa: F401
        except ImportError as exc:
            raise PowerError(
                "pyjoulescope_driver package not installed",
                hint="Install with: pip install 'helia-profiler[power]' "
                "or pip install pyjoulescope_driver",
            ) from exc
        except Exception as exc:
            # numpy/pyjls ABI mismatch surfaces as ValueError during import.
            raise PowerError(
                f"pyjoulescope_driver failed to import: {exc}",
                hint="Likely a numpy ABI mismatch. Try: "
                "pip install --force-reinstall 'pyjoulescope_driver' 'pyjls'.",
            ) from exc


    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def capture(
        self,
        *,
        duration_s: float,
        io_voltage: float,
        sampling_frequency: int = 1_000_000,
        **kwargs: Any,
    ) -> PowerResult:
        """Capture aggregate power statistics for *duration_s* seconds.

        Uses the on-instrument 1–2 Hz statistics stream rather than raw
        samples.  This avoids buffering millions of points on the host and
        gives accurate avg/peak/energy summaries for whole-inference timing.
        The firmware is expected to bracket the inference with a GPIO sync
        toggle but the driver does not gate on it; the entire capture window
        is summarised.
        """
        del sampling_frequency  # streaming stats are at instrument-fixed rate

        driver, device_path, family = _open_device(self._serial)

        stats_topic = f"{device_path}/{_STATS_TOPIC[family]}"
        ctrl = _STATS_CTRL[family]
        cycle_topic, off_value, on_value = _POWER_CYCLE[family]

        packets: list[dict[str, Any]] = []

        def _on_stats(_topic: str, value: Any) -> None:
            if isinstance(value, dict):
                packets.append(value)

        try:
            # Make sure current is flowing through the shunt (auto range).
            try:
                driver.publish(f"{device_path}/{cycle_topic}", on_value)
            except Exception:
                # Some drivers reject re-setting the same value; ignore.
                pass

            driver.subscribe(stats_topic, "pub", _on_stats)
            try:
                if ctrl is not None:
                    ctrl_topic, ctrl_on, _ctrl_off = ctrl
                    driver.publish(f"{device_path}/{ctrl_topic}", ctrl_on)

                time.sleep(duration_s)
            finally:
                if ctrl is not None:
                    ctrl_topic, _ctrl_on, ctrl_off = ctrl
                    try:
                        driver.publish(f"{device_path}/{ctrl_topic}", ctrl_off)
                    except Exception:
                        pass
                try:
                    driver.unsubscribe(stats_topic, _on_stats)
                except Exception:
                    pass

            if not packets:
                raise PowerError(
                    "No statistics received from Joulescope",
                    hint="Check USB connection and that no other tool is holding the device.",
                )

            samples, summary = _process_stats(packets, duration_s, io_voltage)

            log.info(
                "Joulescope: avg=%.3f mA, peak=%.3f mA, energy=%.6f J (%d stat packets, %s)",
                summary.avg_current_a * 1000,
                summary.peak_current_a * 1000,
                summary.energy_j,
                len(packets),
                family.upper(),
            )

            return PowerResult(
                summary=summary,
                samples=samples,
                metadata={
                    "driver": f"joulescope-{family}",
                    "device": device_path,
                    "io_voltage": io_voltage,
                    "stat_packets": len(packets),
                },
            )

        except PowerError:
            raise
        except Exception as exc:
            raise PowerError(
                f"Joulescope capture failed: {exc}",
                hint="Check USB connection and ensure no other software is using the device.",
            ) from exc
        finally:
            _close_device(driver, device_path)

    # ------------------------------------------------------------------
    # Power cycle
    # ------------------------------------------------------------------

    def power_cycle(self, *, off_time_s: float = 0.5, settle_time_s: float = 1.0) -> None:
        """Cut and restore target power via the Joulescope current shunt.

        Uses the family-appropriate current-range topic.  Setting it to the
        "off" value opens the input relay, disconnecting the target from
        its supply; restoring "auto" re-enables it for a clean hardware reset.
        """
        log.info(
            "Power-cycle reset via Joulescope (off=%.1fs, settle=%.1fs)",
            off_time_s,
            settle_time_s,
        )

        driver, device_path, family = _open_device(self._serial)
        topic, off_value, on_value = _POWER_CYCLE[family]

        try:
            driver.publish(f"{device_path}/{topic}", off_value)
            log.info("Target power OFF")
            time.sleep(off_time_s)
            driver.publish(f"{device_path}/{topic}", on_value)
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
            _close_device(driver, device_path)

        log.info("Power-cycle reset complete")

    # ------------------------------------------------------------------
    # Passthrough (used by EnsureBoardPoweredStage to keep target alive)
    # ------------------------------------------------------------------

    def enable_passthrough(self) -> None:
        """Open the Joulescope and enable current passthrough (close relay)."""
        driver, device_path, family = _open_device(self._serial)
        topic, _off_value, on_value = _POWER_CYCLE[family]
        try:
            driver.publish(f"{device_path}/{topic}", on_value)
        except Exception as exc:
            _close_device(driver, device_path)
            raise PowerError(
                f"Failed to enable Joulescope passthrough: {exc}",
                hint="Check USB connection.",
            ) from exc
        self._pt_device_path = device_path
        log.info("Joulescope passthrough enabled (%s)", family.upper())

    def disable_passthrough(self) -> None:
        """Release the Joulescope opened by :meth:`enable_passthrough`."""
        device_path = getattr(self, "_pt_device_path", None)
        if device_path is not None:
            _close_device(_get_shared_driver(), device_path)
            self._pt_device_path = None
            log.info("Joulescope passthrough released")


# ---------------------------------------------------------------------------
# Stats parsing — works for both the JS110 ``s/sstats/value`` shape and the
# JS220 ``s/stats/value`` shape, both of which expose
# ``packet['signals'][<sig>][<stat>] = {'value': <float>, 'units': <str>}``.
# ---------------------------------------------------------------------------


def _process_stats(
    stats: list[dict[str, Any]],
    duration_s: float,
    io_voltage: float,
) -> tuple[list[PowerSample], PowerSummary]:
    import numpy as np

    n = max(len(stats), 1)
    dt = duration_s / n
    currents: list[float] = []
    voltages: list[float] = []
    peaks: list[float] = []

    for pkt in stats:
        sig = pkt.get("signals", {}) if isinstance(pkt, dict) else {}
        cur = sig.get("current", {})
        vol = sig.get("voltage", {})
        currents.append(_extract_scalar(cur.get("avg", 0.0)))
        voltages.append(_extract_scalar(vol.get("avg", io_voltage), default=io_voltage))
        peaks.append(_extract_scalar(cur.get("max", 0.0)))

    if not currents:
        currents = [0.0]
        voltages = [io_voltage]
        peaks = [0.0]

    currents_np = np.asarray(currents, dtype=float)
    voltages_np = np.asarray(voltages, dtype=float)
    peaks_np = np.asarray(peaks, dtype=float)
    power_np = currents_np * voltages_np

    avg_current = float(np.nanmean(currents_np))
    avg_power = float(np.nanmean(power_np))
    peak_current = float(np.nanmax(peaks_np)) if peaks_np.size else avg_current
    energy = float(np.nansum(power_np)) * dt

    samples = [
        PowerSample(timestamp_s=i * dt, current_a=c, voltage_v=v)
        for i, (c, v) in enumerate(zip(currents, voltages))
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


# Optional helper exposed for unit tests; not part of the public surface.
__all__ = ["JoulescopeDriver"]
