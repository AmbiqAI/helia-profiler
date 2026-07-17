"""Joulescope GPIO-gated capture (:meth:`JoulescopeDriver.capture_gated`).

Split out of ``driver.py`` purely to keep module line counts manageable;
this function is attached to :class:`~.driver.JoulescopeDriver` as the
``capture_gated`` method (see the bottom of ``driver.py``). It is not a
public entry point on its own.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ...errors import PowerError
from ..base import PowerResult
from ..diagnostics import GateTransitionTiming, classify_gate_failure
from .device import (
    _HOST_STATS,
    _NATIVE_SAMPLE_RATE,
    _POWER_CYCLE,
    _close_device,
    _open_device,
)
from .diagnostics import _gated_stats_diagnostics
from .stats import (
    _fullrate_energy_over_windows,
    _process_gated_stats,
    _summary_to_dict,
    _whole_summary_from_stats,
)

if TYPE_CHECKING:
    from .driver import JoulescopeDriver

log = logging.getLogger("hpx")


def capture_gated(
    self: "JoulescopeDriver",
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
    on_gate_rise: Callable[[], None] | None = None,
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

    log.debug("gate-race timeline: _open_device() start t=%.3f", time.time())
    driver, device_path, family = _open_device(self._serial)
    log.debug("gate-race timeline: _open_device() done t=%.3f", time.time())

    cycle_topic, _off_value, on_value = _POWER_CYCLE[family]
    native_rate = _NATIVE_SAMPLE_RATE[family]
    scnt_topic, sctrl_topic, sval_topic = _HOST_STATS[family]
    scnt = max(1, round(native_rate / max(1, int(stats_rate_hz))))

    packets: list[dict[str, Any]] = []
    stop = threading.Event()
    poll_samples: list[tuple[int, int]] = []
    bit = 1 << sync_input_index
    windows_done = 0
    first_high_at: float | None = None
    first_low_after_high_at: float | None = None
    go_release_at: float | None = None

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
        nonlocal first_high_at, first_low_after_high_at, windows_done
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
                    if first_high_at is None:
                        first_high_at = time.monotonic()
                        log.debug(
                            "gate-race timeline: GPI rise detected t=%.3f (first poll sample=%s)",
                            time.time(),
                            len(poll_samples) == 1,
                        )
                        # Drop the GO line the moment the gate is observed
                        # high: the firmware has latched GO, and a GPO held
                        # high through the window backfeeds the target around
                        # the current shunt (several mA measured on an AP510
                        # EVB — enough to drive the measured window current
                        # negative).
                        if on_gate_rise is not None:
                            try:
                                on_gate_rise()
                                log.debug(
                                    "gate-race timeline: on_gate_rise (GO drop) done t=%.3f",
                                    time.time(),
                                )
                            except Exception:
                                log.warning("on_gate_rise hook failed", exc_info=True)
                elif prev_level and not level and high_seen:
                    if first_low_after_high_at is None:
                        first_low_after_high_at = time.monotonic()
                        log.debug("gate-race timeline: GPI fall detected t=%.3f", time.time())
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
            log.debug("gate-race timeline: GPI poller thread started t=%.3f", time.time())
            # The poller is now sampling GPI.  For transports whose firmware
            # blocks until the host attaches (USB CDC waits on DTR), release
            # it now — *after* the poller is watching — so the gated GPIO
            # window cannot fire before we are ready to see it.
            if on_started is not None:
                try:
                    on_started()
                    go_release_at = time.monotonic()
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
            failure = classify_gate_failure(
                saw_gate_rise=first_high_at is not None,
                duration_s=duration_s,
            )
            raise PowerError(failure.message, hint=failure.hint)

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
        gate_timing = GateTransitionTiming(
            capture_to_gate_rise_s=(
                round(first_high_at - capture_start, 6)
                if first_high_at is not None
                else None
            ),
            capture_to_gate_fall_s=(
                round(first_low_after_high_at - capture_start, 6)
                if first_low_after_high_at is not None
                else None
            ),
            go_release_to_gate_rise_s=(
                round(first_high_at - go_release_at, 6)
                if go_release_at is not None and first_high_at is not None
                else None
            ),
        )
        sync_timing = gate_timing.to_metadata()
        if sync_timing:
            metadata["sync_timing_s"] = sync_timing
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
