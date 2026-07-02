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
import os
import threading
import time
from collections.abc import Callable
from typing import Any

from ..errors import PowerError
from .base import GatedPowerWindow, PowerMode, PowerResult, PowerSample, PowerSummary
from .sync import DeviceState, NullSyncController, SyncController, SyncWiring

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

#: Native full-rate sampling frequency per family (Hz).  Used to convert a
#: desired host-side stats rate into the device ``s/stats/scnt`` sample count
#: (``scnt = native_rate / stats_rate_hz``).
_NATIVE_SAMPLE_RATE = {
    "js110": 2_000_000,
    "js220": 1_000_000,
}

#: Host-side configurable statistics topics ``(scnt, ctrl, value)``.  Unlike the
#: fixed-2 Hz JS110 sensor-side ``s/sstats`` used by :meth:`capture`, this stream
#: is rate-settable and each packet carries the instrument's full-rate charge and
#: energy integrals — exactly what the gated window needs, with KB (not MB) of
#: data and no raw sample streaming.
_HOST_STATS = {
    "js110": ("s/stats/scnt", "s/stats/ctrl", "s/stats/value"),
    "js220": ("s/stats/scnt", "s/stats/ctrl", "s/stats/value"),
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


def enumerate_devices() -> list[tuple[str, str]]:
    """Return ``[(device_path, family), ...]`` for connected Joulescopes.

    Lightweight discovery: opens the shared :mod:`pyjoulescope_driver`
    handle but does **not** open any individual device. Raises
    :class:`PowerError` if the driver package is missing or the underlying
    enumeration call fails (e.g. libusb permissions).
    """
    drv = _get_shared_driver()
    try:
        paths = list(drv.device_paths())
    except Exception as exc:
        raise PowerError(
            f"Joulescope enumeration failed: {exc}",
            hint="Check USB connection.",
        ) from exc
    out: list[tuple[str, str]] = []
    for p in paths:
        try:
            out.append((p, _family_from_path(p)))
        except PowerError:
            # Unknown family — skip silently rather than fail enumeration.
            log.debug("Skipping unknown Joulescope device path: %s", p)
    return out


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

    @property
    def num_gpi(self) -> int:
        return 2  # JS110/JS220 expose 2 general-purpose inputs

    @property
    def has_gpo(self) -> bool:
        return True  # JS110/JS220 expose 2 general-purpose outputs

    def make_sync_controller(self, wiring: SyncWiring) -> SyncController:
        """Return a 3-wire lock-step controller, or a gate-only fallback."""
        if not wiring.lockstep or not self.has_gpo:
            return NullSyncController()
        return JoulescopeSyncController(serial=self._serial, wiring=wiring)


    # ------------------------------------------------------------------
    # Availability check
    # ------------------------------------------------------------------

    def check_available(self) -> None:
        try:
            import pyjoulescope_driver  # noqa: F401
        except ImportError as exc:
            raise PowerError(
                "pyjoulescope_driver package not installed",
                hint="This should be installed automatically with helia-profiler. "
                "Reinstall with: pip install --force-reinstall helia-profiler",
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
                packet = dict(value)
                packet["_host_time64"] = time64.now()
                packets.append(packet)

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

    def capture_gated(
        self,
        *,
        duration_s: float,
        io_voltage: float,
        sync_input_index: int,
        stats_rate_hz: int = 1000,
        clean_infer_count: int | None = None,
        poll_interval_s: float = 0.004,
        min_high_windows: int = 1,
        guard_s: float = 0.15,
        on_started: Callable[[], None] | None = None,
        **kwargs: Any,
    ) -> PowerResult:
        """Capture GPIO-gated power using on-device-integrated host stats.

        Instead of streaming raw current/voltage at the instrument's native
        ~1-2 MSPS and integrating on the host (MB/s of data), this configures
        the device's host-side statistics stream at ``stats_rate_hz`` (via
        ``s/stats/scnt``).  Each stat packet carries the instrument's *full-rate*
        charge and energy integrals over a ~1 ms sub-window, so summing the
        packets that fall inside the GPIO-high window yields exact window energy
        with only a few KB of data.  The per-packet avg/min/max/std also give a
        spike-robust current/power distribution for reporting.

        The gated clean pass runs first in the firmware, so once the poller sees
        ``min_high_windows`` complete GPIO-high windows plus a ``guard_s`` settle
        we early-stop; ``duration_s`` is only a safety upper bound.
        """
        del kwargs

        try:
            from pyjoulescope_driver import time64
        except Exception as exc:
            raise PowerError(
                f"Joulescope gated capture requires pyjoulescope_driver: {exc}",
                hint="Reinstall pyjoulescope_driver and pyjls in the active environment.",
            ) from exc

        driver, device_path, family = _open_device(self._serial)

        cycle_topic, _off_value, on_value = _POWER_CYCLE[family]
        native_rate = _NATIVE_SAMPLE_RATE[family]
        scnt_topic, sctrl_topic, sval_topic = _HOST_STATS[family]
        scnt = max(1, round(native_rate / max(1, int(stats_rate_hz))))

        packets: list[dict[str, Any]] = []
        stop = threading.Event()
        poll_samples: list[tuple[int, int]] = []
        bit = 1 << sync_input_index
        windows_done = 0

        def _on_stats(_topic: str, value: Any) -> None:
            if isinstance(value, dict):
                packets.append(value)

        # Opt-in full-rate cross-check (AutoDeploy-equivalent reference method).
        # Set HPX_POWER_FULLRATE_XCHECK=1 to also stream raw s/i + s/v at the
        # instrument's native rate and integrate energy over the GPI windows,
        # logged alongside the 1 kHz stats-sum for direct comparison.
        fr_xcheck = os.environ.get("HPX_POWER_FULLRATE_XCHECK") == "1"
        fr_cur: list[Any] = []
        fr_volt: list[Any] = []
        fr_anchors: list[tuple[int, int, float]] = []
        fr_n = [0]

        def _on_fr_current(_topic: str, value: Any) -> None:
            import numpy as np

            data = np.asarray(value["data"], dtype=np.float32)
            sr = value["sample_rate"] / max(1, value.get("decimate_factor", 1))
            utc = value.get("utc")
            if utc is not None:
                fr_anchors.append((fr_n[0], int(utc), float(sr)))
            fr_cur.append(data.copy())
            fr_n[0] += len(data)

        def _on_fr_voltage(_topic: str, value: Any) -> None:
            import numpy as np

            fr_volt.append(np.asarray(value["data"], dtype=np.float32).copy())

        def _poller() -> None:
            nonlocal windows_done
            prev_level = 0
            high_seen = False
            complete_at: float | None = None
            while not stop.is_set():
                try:
                    gpi_value = driver.publish_and_wait(
                        f"{device_path}/s/gpi/+/!req",
                        0,
                        f"{device_path}/s/gpi/+/!value",
                        timeout=0.5,
                    )
                    level = 1 if (int(gpi_value) & bit) else 0
                    poll_samples.append((time64.now(), level))
                    if level and not prev_level:
                        high_seen = True
                    elif prev_level and not level and high_seen:
                        windows_done += 1
                        if windows_done >= min_high_windows and complete_at is None:
                            complete_at = time.monotonic()
                    prev_level = level
                except Exception:
                    pass
                # Early-stop once we have the gated window(s) plus a settle guard
                # so the trailing stat packets covering the window arrive.
                if complete_at is not None and (time.monotonic() - complete_at) >= guard_s:
                    stop.set()
                    break
                time.sleep(poll_interval_s)

        capture_start = time.monotonic()
        try:
            try:
                driver.publish(f"{device_path}/{cycle_topic}", on_value)
            except Exception:
                pass

            driver.publish(f"{device_path}/{scnt_topic}", scnt)
            driver.publish(f"{device_path}/{sctrl_topic}", 1)
            driver.subscribe(f"{device_path}/{sval_topic}", "pub", _on_stats)
            if fr_xcheck:
                try:
                    driver.subscribe(f"{device_path}/s/i/!data", ["pub"], _on_fr_current)
                    driver.subscribe(f"{device_path}/s/v/!data", ["pub"], _on_fr_voltage)
                    driver.publish(f"{device_path}/s/i/ctrl", 1, timeout=0)
                    driver.publish(f"{device_path}/s/v/ctrl", 1, timeout=0)
                    log.info("Joulescope full-rate energy cross-check enabled (s/i + s/v streaming)")
                except Exception:
                    log.warning("Failed to enable full-rate cross-check streaming", exc_info=True)
                    fr_xcheck = False
            try:
                thread = threading.Thread(target=_poller, daemon=True)
                thread.start()
                # The poller is now sampling GPI.  For transports whose firmware
                # blocks until the host attaches (USB CDC waits on DTR), release
                # it now — *after* the poller is watching — so the gated GPIO
                # window cannot fire before we are ready to see it.
                if on_started is not None:
                    try:
                        on_started()
                    except Exception:
                        log.warning("on_started hook failed", exc_info=True)
                # Block until the poller early-stops or the safety bound elapses.
                stop.wait(timeout=duration_s)
            finally:
                stop.set()
                try:
                    thread.join(timeout=1.0)
                except Exception:
                    pass
                try:
                    driver.publish(f"{device_path}/{sctrl_topic}", 0)
                except Exception:
                    pass
                try:
                    driver.unsubscribe(f"{device_path}/{sval_topic}", _on_stats)
                except Exception:
                    pass
                if fr_xcheck:
                    try:
                        driver.publish(f"{device_path}/s/i/ctrl", 0, timeout=0)
                        driver.publish(f"{device_path}/s/v/ctrl", 0, timeout=0)
                        driver.unsubscribe(f"{device_path}/s/i/!data", _on_fr_current)
                        driver.unsubscribe(f"{device_path}/s/v/!data", _on_fr_voltage)
                    except Exception:
                        pass

            windows, gated_summary = _process_gated_stats(
                packets=packets,
                poll_samples=poll_samples,
                io_voltage=io_voltage,
            )
            if not windows:
                raise PowerError(
                    "No GPIO-high windows detected during Joulescope gated capture",
                    hint=(
                        "Check the sync wiring between the target GPIO and Joulescope INPUT"
                        f"{sync_input_index}, confirm the firmware enabled power sync, and "
                        "ensure the clean window contains >=1 stat packet at "
                        f"stats_rate_hz={stats_rate_hz}."
                    ),
                )

            captured_s = time.monotonic() - capture_start
            metadata: dict[str, Any] = {
                "driver": f"joulescope-{family}",
                "device": device_path,
                "io_voltage": io_voltage,
                "measurement_scope": "gpio_gated_clean_window",
                "gating_method": "gpi_snapshot_poll+host_stats_integral",
                "sync_input_index": sync_input_index,
                "stats_rate_hz": stats_rate_hz,
                "stats_scnt": scnt,
                "window_count": len(windows),
                "gpi_poll_count": len(poll_samples),
                "stat_packets": len(packets),
                "early_stopped": windows_done >= min_high_windows,
                "capture_window_s": round(captured_s, 4),
                "capture_safety_bound_s": duration_s,
            }
            if clean_infer_count is not None:
                metadata["clean_infer_count"] = clean_infer_count
            if fr_xcheck:
                fr = _fullrate_energy_over_windows(
                    cur_chunks=fr_cur,
                    volt_chunks=fr_volt,
                    anchors=fr_anchors,
                    poll_samples=poll_samples,
                )
                if fr:
                    metadata["fullrate_xcheck"] = fr
                    stats_energy_per = (
                        gated_summary.energy_j / len(windows) if windows else 0.0
                    )
                    fr_energy_per = fr["energy_per_window_j"]
                    log.info(
                        "Joulescope FULL-RATE xcheck: mean=%.3f mA, power=%.3f mW, "
                        "energy/window=%.1f uJ over %.2f ms @ %.0f Hz (%d samples) | "
                        "stats-sum: mean=%.3f mA, energy/window=%.1f uJ | ratio(full/stats)=%.2fx",
                        fr["mean_current_a"] * 1000.0,
                        fr["mean_power_w"] * 1000.0,
                        fr_energy_per * 1e6,
                        fr["duration_s"] / max(1, fr["window_count"]) * 1000.0,
                        fr["sample_rate_hz"],
                        fr["sample_count"],
                        gated_summary.avg_current_a * 1000.0,
                        stats_energy_per * 1e6,
                        (fr_energy_per / stats_energy_per) if stats_energy_per else float("nan"),
                    )
                else:
                    log.warning(
                        "Full-rate cross-check requested but produced no result "
                        "(chunks=%d, anchors=%d)",
                        len(fr_cur),
                        len(fr_anchors),
                    )
            if packets:
                whole_summary = _whole_summary_from_stats(packets)
                metadata["whole_capture_summary"] = _summary_to_dict(whole_summary)
                diagnostics = _gated_stats_diagnostics(
                    packets=packets,
                    poll_samples=poll_samples,
                )
                metadata["gating_diagnostics"] = diagnostics
                sane_window = gated_summary.avg_current_a > whole_summary.avg_current_a
                metadata["gated_vs_whole_current_ok"] = sane_window
                if not sane_window:
                    log.warning(
                        "Joulescope gated avg current %.3f mA <= whole-capture avg %.3f mA; "
                        "gate/stats timing may be misaligned or the firmware may be sleeping "
                        "inside the asserted window",
                        gated_summary.avg_current_a * 1000.0,
                        whole_summary.avg_current_a * 1000.0,
                    )
                if diagnostics["selected_packets"] == 0:
                    log.warning(
                        "Joulescope gated window selected zero stat packets; check GPI/stat "
                        "timestamp alignment and stats_rate_hz=%d",
                        stats_rate_hz,
                    )
                else:
                    log.info(
                        "Joulescope gated packet mask: selected=%d rejected=%d, "
                        "selected median=%.3f mA rejected median=%.3f mA",
                        diagnostics["selected_packets"],
                        diagnostics["rejected_packets"],
                        diagnostics["selected_median_current_a"] * 1000.0,
                        diagnostics["rejected_median_current_a"] * 1000.0,
                    )

            log.info(
                "Joulescope gated: windows=%d, gated_dur=%.3f ms, energy=%.6f J, "
                "%d stat packets @ ~%d Hz (%s)",
                len(windows),
                gated_summary.duration_s * 1000.0,
                gated_summary.energy_j,
                len(packets),
                stats_rate_hz,
                family.upper(),
            )

            return PowerResult(
                summary=gated_summary,
                gated_windows=windows,
                metadata=metadata,
            )
        except PowerError:
            raise
        except Exception as exc:
            raise PowerError(
                f"Joulescope gated capture failed: {exc}",
                hint="Check USB connection, sync wiring, and that no other software is using the device.",
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

    # ------------------------------------------------------------------
    # High-level vendor-neutral hook
    # ------------------------------------------------------------------

    def ensure_target_powered(self, *, required: bool) -> bool:
        """Best-effort or strict passthrough enable, per the decision matrix.

        Owns *all* Joulescope-specific knowledge (enumeration, multi-device
        ambiguity, serial matching, hint strings) so the pipeline stage
        stays driver-agnostic. See :meth:`PowerDriver.ensure_target_powered`
        for the contract.
        """

        def _bail(msg: str, *, hint: str | None = None, level: int = logging.INFO) -> bool:
            if required:
                raise PowerError(msg, hint=hint)
            log.log(level, "%s — skipping Joulescope passthrough.", msg)
            return False

        # --- Check the driver package is importable.
        try:
            self.check_available()
        except PowerError as exc:
            if required:
                raise
            log.debug("Joulescope driver unavailable (%s) — skipping passthrough.", exc)
            return False

        # --- Enumerate devices without opening any.
        try:
            devices = enumerate_devices()
        except PowerError as exc:
            if required:
                raise
            log.debug("Joulescope enumeration failed (%s) — skipping passthrough.", exc)
            return False

        if not devices:
            return _bail(
                "No Joulescope detected",
                hint="Plug in a JS110 or JS220 and ensure it is powered on.",
                level=logging.DEBUG,
            )

        # --- Pick a device.
        if self._serial is not None:
            wanted = str(self._serial).lstrip("0") or "0"
            matched = [d for d in devices if wanted in d[0]]
            if not matched:
                paths = ", ".join(d[0] for d in devices)
                return _bail(
                    f"Joulescope serial '{self._serial}' not found",
                    hint=f"Connected devices: {paths}. "
                    "Update power.serial / --power-serial to match.",
                )
        elif len(devices) > 1:
            paths = ", ".join(d[0] for d in devices)
            return _bail(
                f"{len(devices)} Joulescopes connected — please disambiguate",
                hint=f"Set power.serial / --power-serial to one of: {paths}",
            )
        # else: exactly one device, no serial needed.

        # --- Enable passthrough; release USB handle immediately (relay is
        # latched in hardware so the board stays powered).
        try:
            self.enable_passthrough()
        except PowerError as exc:
            if required:
                raise
            log.warning("Joulescope passthrough failed: %s — continuing.", exc)
            return False

        try:
            self.disable_passthrough()
        except Exception:  # pragma: no cover - defensive
            log.debug("disable_passthrough after enable failed (ignored)")

        log.info("Joulescope passthrough enabled (relay latched).")
        return True


class JoulescopeSyncController:
    """3-wire lock-step controller backed by Joulescope GPI/GPO.

    Drives OUTPUT0 (go), reads INPUT0 (gate) and INPUT1 (state) on the same
    shared process-wide driver used for capture, so it composes with an active
    gated capture without re-opening the relay.
    """

    def __init__(self, *, serial: str | None, wiring: SyncWiring) -> None:
        self._serial = serial
        self._wiring = wiring
        self._driver: Any = None
        self._path: str | None = None

    @property
    def lockstep(self) -> bool:
        return True

    def _ensure(self) -> tuple[Any, str]:
        if self._driver is None:
            self._driver, self._path, _family = _open_device(self._serial)
        return self._driver, str(self._path)

    def _read_input(self, index: int) -> bool:
        driver, path = self._ensure()
        value = driver.publish_and_wait(
            f"{path}/s/gpi/+/!req", 0, f"{path}/s/gpi/+/!value", timeout=0.5
        )
        return bool(int(value) & (1 << index))

    def _write_go(self, high: bool) -> None:
        driver, path = self._ensure()
        driver.publish(f"{path}/s/gpo/{self._wiring.go_output_index}/value", 1 if high else 0)

    def arm(self) -> None:
        self._write_go(False)

    def wait_ready(self, *, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._read_input(self._wiring.state_input_index):
                return True
            time.sleep(0.005)
        return False

    def signal_go(self) -> None:
        self._write_go(True)

    def read_state(self) -> DeviceState:
        if self._read_input(self._wiring.gate_input_index):
            return DeviceState.RUNNING
        if self._read_input(self._wiring.state_input_index):
            return DeviceState.READY
        return DeviceState.UNKNOWN

    def release(self) -> None:
        try:
            self._write_go(False)
        except Exception:  # pragma: no cover - defensive
            pass


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


def _sv(field: Any, default: float = 0.0) -> float:
    """Extract a ``{'value': x, 'units': ...}`` scalar from a stats packet."""
    if isinstance(field, dict):
        try:
            return float(field.get("value", default))
        except (TypeError, ValueError):
            return default
    return default


def _stats_arrays(packets: list[dict[str, Any]]) -> dict[str, Any]:
    """Vectorise the per-packet fields we use from ``s/stats/value`` packets."""
    import numpy as np

    mid, host_time, dur, cur_avg, cur_max, cur_min, cur_int, pwr_avg, pwr_int = (
        [] for _ in range(9)
    )
    for p in packets:
        t = p.get("time", {}) if isinstance(p, dict) else {}
        utc = (t.get("utc", {}) or {}).get("value")
        if not utc or len(utc) < 2:
            continue
        u0, u1 = float(utc[0]), float(utc[1])
        host_tick = p.get("_host_time64") if isinstance(p, dict) else None
        sig = p.get("signals", {})
        cur = sig.get("current", {})
        pwr = sig.get("power", {})
        mid.append(0.5 * (u0 + u1))
        host_time.append(float(host_tick) if host_tick is not None else np.nan)
        dur.append((u1 - u0))
        cur_avg.append(_sv(cur.get("avg")))
        cur_max.append(_sv(cur.get("max")))
        cur_min.append(_sv(cur.get("min")))
        cur_int.append(_sv(cur.get("integral")))
        pwr_avg.append(_sv(pwr.get("avg")))
        pwr_int.append(_sv(pwr.get("integral")))
    # Report current/power as magnitude.  The Joulescope's sign reflects which
    # terminal sources vs sinks; on a SoC-only rail wired with reversed IN/OUT
    # the draw reads negative even though the magnitude is correct.  We measure
    # consumption, so normalize to |I|/|P|; timestamps are left untouched.
    #
    # Polarity-robust peak: the JS110 reports per-window avg/min/max as *signed*
    # values.  With reversed IN/OUT the SoC draw is negative, so the true current
    # PEAK is the most-negative sample (the ``min`` field) and ``max`` holds the
    # trough (closest to zero).  Taking ``|max|`` alone therefore reports the
    # trough as the peak — the tell is a "peak" that comes out *below* the p99 of
    # the per-window averages, which is physically impossible (max >= mean
    # always).  We saw exactly that on AP510 (peak 1.34 mA < p99-avg 1.55 mA).
    # ``max(|max|, |min|)`` recovers the real peak regardless of wiring polarity;
    # it also leaves the correctly-wired (positive) case unchanged.  The window
    # average is unaffected — it comes from the abs'd charge integral, which is
    # direction-independent — so this only fixes the peak/percentile stats.
    abs_max = np.abs(np.asarray(cur_max, dtype=np.float64))
    abs_min = np.abs(np.asarray(cur_min, dtype=np.float64))
    return {
        "mid": np.asarray(mid, dtype=np.float64),
        "host_time": np.asarray(host_time, dtype=np.float64),
        "dur_ticks": np.asarray(dur, dtype=np.float64),
        "cur_avg": np.abs(np.asarray(cur_avg, dtype=np.float64)),
        "cur_max": abs_max,
        "cur_min": abs_min,
        "cur_peak": np.maximum(abs_max, abs_min),
        "cur_int": np.abs(np.asarray(cur_int, dtype=np.float64)),
        "pwr_avg": np.abs(np.asarray(pwr_avg, dtype=np.float64)),
        "pwr_int": np.abs(np.asarray(pwr_int, dtype=np.float64)),
    }


def _gated_mask_axis(a: dict[str, Any]) -> tuple[Any, str]:
    """Return the timestamp axis used to align packets with GPI polls."""
    host_time = a.get("host_time")
    if host_time is not None and getattr(host_time, "size", 0) and not bool(host_time.size == 0):
        import numpy as np

        if not np.isnan(host_time).any():
            return host_time, "host_packet_arrival_time64"
    return a["mid"], "device_packet_midpoint_time64"


def _whole_summary_from_stats(packets: list[dict[str, Any]]) -> PowerSummary:
    """Summarise the entire captured window from on-device stat integrals."""
    import numpy as np
    from pyjoulescope_driver import time64

    a = _stats_arrays(packets)
    if a["mid"].size == 0:
        return PowerSummary(0.0, 0.0, 0.0, 0.0, 0.0, 0)
    duration_s = float(a["dur_ticks"].sum() / time64.SECOND)
    charge_c = float(a["cur_int"].sum())
    energy_j = float(a["pwr_int"].sum())
    peak = float(a["cur_peak"].max()) if a["cur_peak"].size else 0.0
    avg_current = charge_c / duration_s if duration_s > 0 else 0.0
    avg_power = energy_j / duration_s if duration_s > 0 else 0.0
    return PowerSummary(
        avg_current_a=avg_current,
        avg_power_w=avg_power,
        peak_current_a=peak,
        energy_j=energy_j,
        duration_s=duration_s,
        sample_count=int(a["mid"].size),
    )


def _segment_gpi_windows(poll_samples: list[tuple[int, int]]) -> list[tuple[float, float]]:
    import numpy as np

    if not poll_samples:
        return []
    poll_t = np.asarray([t for t, _ in poll_samples], dtype=np.float64)
    poll_v = np.asarray([v for _, v in poll_samples], dtype=np.int8)
    high = poll_v > 0
    edges = np.diff(high.astype(int))
    rises = poll_t[1:][edges == 1]
    falls = poll_t[1:][edges == -1]
    windows: list[tuple[float, float]] = []
    fall_index = 0
    for rise in rises:
        while fall_index < len(falls) and falls[fall_index] <= rise:
            fall_index += 1
        if fall_index < len(falls):
            windows.append((float(rise), float(falls[fall_index])))
            fall_index += 1
    return windows


def _fullrate_energy_over_windows(
    *,
    cur_chunks: list[Any],
    volt_chunks: list[Any],
    anchors: list[tuple[int, int, float]],
    poll_samples: list[tuple[int, int]],
) -> dict[str, Any] | None:
    """Integrate raw full-rate current/voltage over the GPI-high windows.

    This is the *reference* energy method used by AutoDeploy: rather than
    summing the device's 1 kHz statistics ``integral`` fields, it integrates
    the full-rate (``s/i/!data`` + ``s/v/!data``) sample stream directly.  Any
    high-frequency current content (e.g. SIMO buck switching spikes) that the
    decimated statistics stream smooths away is captured here.

    Returns per-window and aggregate energy/charge, or ``None`` if there is
    insufficient data to build a timeline.
    """
    import numpy as np
    from pyjoulescope_driver import time64

    if not cur_chunks or not anchors:
        return None

    cur = np.concatenate(cur_chunks)
    volt = np.concatenate(volt_chunks) if volt_chunks else np.array([], np.float32)
    n = min(len(cur), len(volt)) if len(volt) else len(cur)
    if n == 0:
        return None
    cur = cur[:n]
    volt = volt[:n] if len(volt) else np.full(n, np.nan, np.float32)

    idx = np.asarray([a[0] for a in anchors], dtype=np.float64)
    utc = np.asarray([a[1] for a in anchors], dtype=np.float64)
    sr = float(anchors[-1][2])
    if sr <= 0:
        return None
    slope = time64.SECOND / sr
    i0, u0 = idx[0], utc[0]
    sample_utc = u0 + (np.arange(n, dtype=np.float64) - i0) * slope

    windows = _segment_gpi_windows(poll_samples)
    if not windows:
        return None

    dt = 1.0 / sr
    win_out: list[dict[str, float]] = []
    tot_charge = 0.0
    tot_energy = 0.0
    tot_dur = 0.0
    for rise, fall in windows:
        mask = (sample_utc >= rise) & (sample_utc < fall)
        seg_i = cur[mask]
        seg_v = volt[mask]
        if seg_i.size == 0:
            continue
        charge_c = float(np.sum(seg_i) * dt)
        energy_j = float(np.sum(seg_i * seg_v) * dt)
        dur_s = (fall - rise) / time64.SECOND
        tot_charge += charge_c
        tot_energy += energy_j
        tot_dur += dur_s
        win_out.append(
            {
                "duration_s": dur_s,
                "charge_c": charge_c,
                "energy_j": energy_j,
                "mean_current_a": float(np.mean(seg_i)),
                "peak_current_a": float(np.max(np.abs(seg_i))),
            }
        )

    if not win_out or tot_dur <= 0:
        return None

    return {
        "method": "fullrate_trapezoid_integral",
        "sample_rate_hz": sr,
        "sample_count": int(n),
        "window_count": len(win_out),
        "duration_s": tot_dur,
        "charge_c": tot_charge,
        "energy_j": tot_energy,
        "mean_current_a": tot_charge / tot_dur,
        "mean_power_w": tot_energy / tot_dur,
        "energy_per_window_j": tot_energy / len(win_out),
        "windows": win_out,
    }


def _gated_stats_diagnostics(
    *,
    packets: list[dict[str, Any]],
    poll_samples: list[tuple[int, int]],
) -> dict[str, Any]:
    """Summarise how the GPIO windows intersect the stats packets."""
    import numpy as np

    a = _stats_arrays(packets)
    windows = _segment_gpi_windows(poll_samples)
    mask_axis, axis_name = _gated_mask_axis(a)

    diagnostics: dict[str, Any] = {
        "mask_time_axis": axis_name,
        "window_count": len(windows),
        "gpi_poll_count": len(poll_samples),
        "stat_packet_count": int(mask_axis.size),
        "selected_packets": 0,
        "rejected_packets": int(mask_axis.size),
        "selected_median_current_a": 0.0,
        "rejected_median_current_a": 0.0,
        "selected_min_current_a": 0.0,
        "selected_max_current_a": 0.0,
        "rejected_min_current_a": 0.0,
        "rejected_max_current_a": 0.0,
        "mask_axis_first_tick": None,
        "mask_axis_last_tick": None,
        "packet_midpoint_first_tick": None,
        "packet_midpoint_last_tick": None,
        "gpi_first_tick": None,
        "gpi_last_tick": None,
        "windows": [
            {"rise_tick": int(rise), "fall_tick": int(fall)} for rise, fall in windows
        ],
    }
    if poll_samples:
        diagnostics["gpi_first_tick"] = int(poll_samples[0][0])
        diagnostics["gpi_last_tick"] = int(poll_samples[-1][0])
    if mask_axis.size == 0:
        return diagnostics

    diagnostics["mask_axis_first_tick"] = int(mask_axis.min())
    diagnostics["mask_axis_last_tick"] = int(mask_axis.max())
    diagnostics["packet_midpoint_first_tick"] = int(a["mid"].min())
    diagnostics["packet_midpoint_last_tick"] = int(a["mid"].max())

    selected_mask = np.zeros(mask_axis.shape, dtype=bool)
    for rise, fall in windows:
        selected_mask |= (mask_axis >= rise) & (mask_axis <= fall)

    selected = a["cur_avg"][selected_mask]
    rejected = a["cur_avg"][~selected_mask]
    diagnostics["selected_packets"] = int(selected.size)
    diagnostics["rejected_packets"] = int(rejected.size)
    if selected.size:
        diagnostics["selected_median_current_a"] = float(np.median(selected))
        diagnostics["selected_min_current_a"] = float(selected.min())
        diagnostics["selected_max_current_a"] = float(selected.max())
    if rejected.size:
        diagnostics["rejected_median_current_a"] = float(np.median(rejected))
        diagnostics["rejected_min_current_a"] = float(rejected.min())
        diagnostics["rejected_max_current_a"] = float(rejected.max())
    return diagnostics


def _process_gated_stats(
    *,
    packets: list[dict[str, Any]],
    poll_samples: list[tuple[int, int]],
    io_voltage: float,
) -> tuple[list[GatedPowerWindow], PowerSummary]:
    """Integrate the gated window(s) from on-device stat-packet integrals.

    Each packet carries the instrument's full-rate charge/energy integral over a
    ~1 ms sub-window, so summing the packets whose midpoint falls inside a
    GPIO-high window gives exact window charge/energy.  The per-packet
    avg/max samples within the window yield the spike-robust distribution
    (median / p95 / p99 / glitch-robust peak) so a lone transient sample cannot
    define the headline current.
    """
    import numpy as np
    from pyjoulescope_driver import time64

    del io_voltage  # voltage is folded into the on-device power integral

    a = _stats_arrays(packets)
    windows = _segment_gpi_windows(poll_samples)
    if a["mid"].size == 0 or not windows:
        return [], PowerSummary(0.0, 0.0, 0.0, 0.0, 0.0, 0)

    mask_axis, _axis_name = _gated_mask_axis(a)
    t0 = float(mask_axis.min())
    gated_windows: list[GatedPowerWindow] = []
    total_charge = 0.0
    total_energy = 0.0
    total_duration = 0.0
    total_samples = 0
    peak_current = 0.0

    for rise, fall in windows:
        mask = (mask_axis >= rise) & (mask_axis <= fall)
        if not bool(mask.any()):
            continue
        duration_s = float(a["dur_ticks"][mask].sum() / time64.SECOND)
        if duration_s <= 0:
            continue
        charge_c = float(a["cur_int"][mask].sum())
        energy_j = float(a["pwr_int"][mask].sum())
        seg_cur_avg = a["cur_avg"][mask]
        seg_cur_max = a["cur_peak"][mask]
        seg_pwr_avg = a["pwr_avg"][mask]
        avg_current_a = charge_c / duration_s
        avg_power_w = energy_j / duration_s
        peak_current_a = float(seg_cur_max.max())
        total_charge += charge_c
        total_energy += energy_j
        total_duration += duration_s
        total_samples += int(seg_cur_avg.size)
        peak_current = max(peak_current, peak_current_a)
        gated_windows.append(
            GatedPowerWindow(
                start_s=float((rise - t0) / time64.SECOND),
                end_s=float((fall - t0) / time64.SECOND),
                duration_s=duration_s,
                charge_c=charge_c,
                energy_j=energy_j,
                avg_current_a=avg_current_a,
                avg_power_w=avg_power_w,
                peak_current_a=peak_current_a,
                sample_count=int(seg_cur_avg.size),
                median_current_a=float(np.median(seg_cur_avg)),
                p95_current_a=float(np.percentile(seg_cur_avg, 95)),
                p99_current_a=float(np.percentile(seg_cur_avg, 99)),
                peak_current_p99_a=float(np.percentile(seg_cur_max, 99)),
                median_power_w=float(np.median(seg_pwr_avg)),
                p95_power_w=float(np.percentile(seg_pwr_avg, 95)),
                p99_power_w=float(np.percentile(seg_pwr_avg, 99)),
            )
        )

    if total_duration <= 0 or not gated_windows:
        return [], PowerSummary(0.0, 0.0, 0.0, 0.0, 0.0, 0)

    summary = PowerSummary(
        avg_current_a=total_charge / total_duration,
        avg_power_w=total_energy / total_duration,
        peak_current_a=peak_current,
        energy_j=total_energy,
        duration_s=total_duration,
        sample_count=total_samples,
    )
    return gated_windows, summary


def _summary_to_dict(summary: PowerSummary) -> dict[str, float | int]:
    return {
        "avg_current_a": summary.avg_current_a,
        "avg_power_w": summary.avg_power_w,
        "peak_current_a": summary.peak_current_a,
        "energy_j": summary.energy_j,
        "duration_s": summary.duration_s,
        "sample_count": summary.sample_count,
    }


# Optional helper exposed for unit tests; not part of the public surface.
__all__ = ["JoulescopeDriver"]
