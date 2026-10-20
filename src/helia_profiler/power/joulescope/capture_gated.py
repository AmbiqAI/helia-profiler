"""Joulescope GPIO-gated capture (:meth:`JoulescopeDriver.capture_gated`).

This function is bound as the ``capture_gated`` method in the
:class:`~.driver.JoulescopeDriver` class body. It is not a public entry
point on its own.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ...errors import PowerError
from ..base import PowerResult
from ..completion import GatedCompletion, resolve_counter_completion
from ..counter_gate import PackedGateRecorder, counter_gate_trace
from ..diagnostics import (
    BOOT_SETTLE_S,
    GATE_EDGE_POLL_INTERVAL_S,
    GateTransitionTiming,
    classify_gate_failure,
    longest_accepted_window_s,
)
from ..metadata import MeasurementScope, PowerMetadata
from .device import (
    _HOST_STATS,
    _NATIVE_SAMPLE_RATE,
    _POWER_CYCLE,
    _close_device,
    _open_device,
    _read_gpi_snapshot,
)
from .diagnostics import (
    _attach_capture_diagnostics,
    _degraded_observation_result,
    _gated_stats_diagnostics,
    _poll_edge_uncertainty_s,
    _with_gpi_poll_failures,
    _write_gate_dump,
)
from .fullrate import FullRateRecorder
from .stats import (
    _counter_rate_ratio,
    _map_poll_samples_to_packet_time,
    _plan_gate_floor_s,
    _plan_ranked_candidates,
    _process_gated_stats,
    _segment_gpi_windows,
    _select_streamed_window,
)

if TYPE_CHECKING:
    from .driver import JoulescopeDriver

log = logging.getLogger("hpx")


def _host_monotonic_time64(time64: Any) -> int:
    return time.monotonic_ns() * time64.SECOND // 1_000_000_000


def capture_gated(
    self: "JoulescopeDriver",
    *,
    duration_s: float,
    io_voltage: float,
    sync_input_index: int,
    state_input_index: int = 1,
    stats_rate_hz: int = 1000,
    clean_infer_count: int | None = None,
    clean_infer_avg_us: int | None = None,
    minimum_gate_s: float = 0.0,
    gate_relative_tolerance: float = 0.01,
    # Human-facing noun for clean_infer_count in the duration-mismatch hint;
    # the driver has no config access, so the caller passes the probe-aware
    # word (power.diagnostics.count_noun, #172).
    work_noun: str = "inferences",
    poll_interval_s: float = GATE_EDGE_POLL_INTERVAL_S,
    min_high_windows: int = 1,
    guard_s: float = 0.15,
    on_started: Callable[..., None] | None = None,
    on_gate_rise: Callable[[], None] | None = None,
    phase_getter: Callable[[], str] | None = None,
    lockstep: bool | None = None,
    lockstep_wiring_available: bool = False,
    pre_window_s: float = 0.0,
    require_done_state: bool = False,
    **kwargs: Any,
) -> PowerResult:
    """Capture GPIO-gated power using on-device-integrated host stats.

    Instead of streaming raw current/voltage at the instrument's native
    ~1-2 MSPS and integrating on the host (MB/s of data), this configures
    the device's host-side statistics stream at ``stats_rate_hz`` (via
    ``s/stats/scnt``).  Each stat packet carries the instrument's *full-rate*
    charge and energy integrals over a ~1 ms sub-window, so summing the
    packets selected by midpoint yields window energy with packet-scale
    endpoint uncertainty. The per-packet avg/min/max/std also give a
    spike-robust current/power distribution for reporting.

    Only one clean window is supported. After its falling edge and a
    ``guard_s`` settle, capture stops; ``duration_s`` bounds the wait for it.
    With ``require_done_state``, a GO acknowledgement and post-gate DONE
    on the state input are also required before stopping. Both input streams
    are retained for counter-domain protocol validation by the caller.
    An exception from ``on_started`` ends the capture at once and propagates
    unchanged. ``pre_window_s`` is the warm-up the target runs between boot
    and the window when lock-step is off.
    """
    del kwargs

    if min_high_windows != 1:
        raise PowerError(
            "Joulescope gated capture supports exactly one high window (min_high_windows=1)."
        )

    # With a planned window, the gate the capture stops on and selects must
    # also be plausibly that window. A sync-line high during reset or boot can
    # outlast the fixed minimum, and on JS220/JS320 the GPI stream is not
    # phase-gated (contract C-S2). The plan floor only ranks candidates: when
    # no high reaches it, the last high that clears the fixed minimum is kept,
    # so a real window short of the plan still yields a (warned) result.
    plan_floor_s = _plan_gate_floor_s(
        minimum_gate_s=minimum_gate_s,
        clean_infer_count=clean_infer_count,
        clean_infer_avg_us=clean_infer_avg_us,
        stats_rate_hz=stats_rate_hz,
        relative_tolerance=gate_relative_tolerance,
    )
    below_plan_highs = 0
    # Edges of the last high the plan rejected, published if it is the one kept.
    below_plan_rise_at: float | None = None
    below_plan_fall_at: float | None = None

    try:
        from pyjoulescope_driver import time64
    except Exception as exc:
        raise PowerError(
            f"Joulescope gated capture requires pyjoulescope_driver: {exc}",
            hint="Reinstall pyjoulescope-driver and pyjls in the active environment.",
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
    poll_reads: list[tuple[int, int, int]] = []
    bit = 1 << sync_input_index
    windows_done = 0
    first_high_at: float | None = None
    first_low_after_high_at: float | None = None
    saw_any_gate_rise = False
    saw_any_gate_fall = False
    go_release_at: float | None = None
    short_pulses_ignored = 0
    short_pulse_phases: dict[str, int] = {}
    short_pulse_first_s: float | None = None
    short_pulse_last_s: float | None = None
    gpi_condition = threading.Condition()
    latest_gpi_value: int | None = None
    gpi_sample_sequence = 0
    gpi_poll_failures = 0
    completion = GatedCompletion()
    early_stopped = False

    def _completed_trace():
        if gate_recorder.error or state_recorder.error:
            raise ValueError("Invalid retained completion stream")
        captured_packets = list(packets)
        gate = counter_gate_trace(
            list(gpi_stream_frames), captured_packets, channel=sync_input_index
        )
        state = counter_gate_trace(
            list(state_recorder.frames), captured_packets, channel=state_input_index
        )
        return gate, resolve_counter_completion(gate, state)

    def _wait_gpi_state(index: int, high: bool, timeout_s: float) -> bool:
        bit_mask = 1 << index
        deadline = time.monotonic() + timeout_s
        consecutive = 0
        with gpi_condition:
            observed_sequence = gpi_sample_sequence
            while not stop.is_set() and time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if not gpi_condition.wait_for(
                    lambda: stop.is_set() or gpi_sample_sequence > observed_sequence,
                    timeout=max(0.0, remaining),
                ):
                    return False
                if stop.is_set():
                    return False
                observed_sequence = gpi_sample_sequence
                ready_gate_low = (
                    not require_done_state
                    or index != state_input_index
                    or not high
                    or latest_gpi_value is not None
                    and not (latest_gpi_value & bit)
                )
                if (
                    latest_gpi_value is not None
                    and bool(latest_gpi_value & bit_mask) is high
                    and ready_gate_low
                ):
                    consecutive += 1
                    if consecutive >= 3:
                        return True
                else:
                    consecutive = 0
        return False

    def _on_stats(_topic: str, value: Any) -> None:
        if isinstance(value, dict):
            packet = dict(value)
            packet["_host_time64"] = _host_monotonic_time64(time64)
            packets.append(packet)

    # Device-timestamped gate edges: stream the sync GPI as a signal so
    # window edges live on the instrument clock — the same axis as the stat
    # packets — at sample-period resolution.  The snapshot poller below stays
    # as the *control* plane (early-stop, GO release, READY qualification) and
    # as the edge fallback, but its poll cadence plus callback latency can
    # merge a pre-window coupling pulse into the true rise (or hold a fall)
    # under host load.  A streamed edge cannot be merged this way.
    gate_recorder = PackedGateRecorder(
        sync_input_index, min(128 * 1024 * 1024, int(duration_s * 2_100_000) + 1048576)
    )
    gpi_stream_frames = gate_recorder.frames
    state_recorder = PackedGateRecorder(state_input_index, gate_recorder.max_bytes)

    def _on_gpi_data(_topic: str, value: Any) -> None:
        gate_recorder.append(value, word_aligned=family in ("js220", "js320"))

    def _on_state_data(_topic: str, value: Any) -> None:
        state_recorder.append(value, word_aligned=family in ("js220", "js320"))

    # Opt-in full-rate cross-check (AutoDeploy-equivalent reference method).
    # Set HPX_POWER_FULLRATE_XCHECK=1 to also stream raw s/i + s/v at the
    # instrument's native rate and integrate energy over the GPI windows,
    # logged alongside the 1 kHz stats-sum for direct comparison.
    fr_requested = os.environ.get("HPX_POWER_FULLRATE_XCHECK") == "1"
    fr_xcheck = fr_requested
    fullrate = FullRateRecorder(driver, device_path)

    callback_errors: list[Exception] = []

    def _abort_callback(exc: Exception) -> None:
        callback_errors.append(exc)
        stop.set()
        with gpi_condition:
            gpi_condition.notify_all()

    def _phase() -> str | None:
        try:
            return phase_getter() if phase_getter is not None else "go_signaled"
        except Exception as exc:
            _abort_callback(exc)
            return None

    def _poller() -> None:
        nonlocal first_high_at, first_low_after_high_at, short_pulse_first_s
        nonlocal saw_any_gate_rise, saw_any_gate_fall
        nonlocal gpi_sample_sequence, latest_gpi_value, short_pulse_last_s
        nonlocal short_pulses_ignored, windows_done, gpi_poll_failures, below_plan_highs
        nonlocal below_plan_rise_at, below_plan_fall_at
        nonlocal early_stopped
        prev_level = 0
        high_seen = False
        high_phase = "unknown"
        complete_at: float | None = None
        while not stop.is_set():
            if _phase() is None:
                return
            gpi_value: int | None = None
            try:
                read_start = _host_monotonic_time64(time64)
                gpi_value = _read_gpi_snapshot(driver, device_path)
                sample_tick = _host_monotonic_time64(time64)
                with gpi_condition:
                    latest_gpi_value = gpi_value
                    gpi_sample_sequence += 1
                    gpi_condition.notify_all()
                level = 1 if (int(gpi_value) & bit) else 0
                phase = _phase()
                if phase is None:
                    return
                done = completion.observe(
                    state_high=bool(gpi_value & (1 << state_input_index)),
                    gate_high=bool(level),
                    go_signaled=phase == "go_signaled",
                )
                if phase != "go_signaled":
                    # Flash/reset pin levels are not protocol events. Keep the
                    # latest bitfield for READY qualification, but establish a
                    # fresh low/high gate history only after GO is sent.
                    poll_samples.clear()
                    poll_reads[:] = [(read_start, sample_tick, level)]
                    high_seen = False
                    first_high_at = None
                    first_low_after_high_at = None
                    prev_level = level
                    time.sleep(poll_interval_s)
                    continue
                # GO may be asserted between poll iterations.  In that case
                # the first retained post-GO sample is already high, while
                # ``prev_level`` correctly carries the known pre-GO low
                # state.  Preserve that state in the segmenter input so the
                # real falling edge can close a complete window.
                if not poll_samples and not prev_level and level:
                    prior_tick = poll_reads[-1][1] if poll_reads else read_start
                    poll_samples.append((prior_tick, 0))
                    if not poll_reads:
                        poll_reads.append((read_start, read_start, 0))
                poll_samples.append((sample_tick, level))
                poll_reads.append((read_start, sample_tick, level))
                if level and not prev_level:
                    high_seen = True
                    saw_any_gate_rise = True
                    high_phase = phase
                    if first_high_at is None:
                        first_high_at = time.monotonic()
                        log.debug(
                            "gate-race timeline: GPI rise detected t=%.3f (first poll sample=%s)",
                            time.time(),
                            len(poll_samples) == 1,
                        )
                        # Drop GO the moment the gate is observed high: a GPO
                        # held high through the window backfeeds the target
                        # around the shunt (see power.sync.release_go).
                        if on_gate_rise is not None:
                            try:
                                on_gate_rise()
                                log.debug(
                                    "gate-race timeline: on_gate_rise (GO drop) done t=%.3f",
                                    time.time(),
                                )
                            except Exception as exc:
                                _abort_callback(exc)
                                return
                elif prev_level and not level and high_seen:
                    saw_any_gate_fall = True
                    if first_low_after_high_at is None:
                        first_low_after_high_at = time.monotonic()
                        log.debug("gate-race timeline: GPI fall detected t=%.3f", time.time())
                    high_duration_s = (
                        time.monotonic() - first_high_at if first_high_at is not None else 0.0
                    )
                    if high_duration_s >= plan_floor_s:
                        windows_done += 1
                        if (
                            not require_done_state
                            and windows_done >= min_high_windows
                            and complete_at is None
                        ):
                            complete_at = time.monotonic()
                    elif high_duration_s >= minimum_gate_s:
                        # Too short for the plan: keep waiting for the real
                        # window, but keep this high as the fallback candidate.
                        below_plan_highs += 1
                        below_plan_rise_at = first_high_at
                        below_plan_fall_at = first_low_after_high_at
                        log.debug(
                            "GPIO-high of %.6fs is short of the plan; waiting", high_duration_s
                        )
                        first_high_at = None
                        first_low_after_high_at = None
                        high_seen = False
                    else:
                        short_pulses_ignored += 1
                        short_pulse_phases[high_phase] = short_pulse_phases.get(high_phase, 0) + 1
                        pulse_start_s = (
                            first_high_at - capture_start if first_high_at is not None else 0.0
                        )
                        short_pulse_first_s = (
                            pulse_start_s
                            if short_pulse_first_s is None
                            else min(short_pulse_first_s, pulse_start_s)
                        )
                        short_pulse_last_s = max(short_pulse_last_s or 0.0, pulse_start_s)
                        log.debug(
                            "Ignoring %.6fs GPIO-high pulse shorter than the %.3fs "
                            "minimum power gate",
                            high_duration_s,
                            minimum_gate_s,
                        )
                        first_high_at = None
                        first_low_after_high_at = None
                        high_seen = False
                prev_level = level
                if require_done_state:
                    # Snapshot levels only trigger a check. Delayed callbacks,
                    # a false fall, or a second transaction cannot close capture.
                    if not done:
                        complete_at = None
                    else:
                        try:
                            _completed_trace()
                        except (KeyError, TypeError, ValueError, OverflowError):
                            complete_at = None
                        else:
                            if complete_at is None:
                                complete_at = time.monotonic()
            except Exception:
                if gpi_value is not None:
                    log.debug("GPI poller iteration failed", exc_info=True)
                else:
                    gpi_poll_failures += 1
                    if gpi_poll_failures == 1:
                        log.debug(
                            "GPI snapshot poll failed; later failures are only counted",
                            exc_info=True,
                        )
            # Early-stop once we have the gated window(s) plus a settle guard
            # so the trailing stat packets covering the window arrive.
            if complete_at is not None and (time.monotonic() - complete_at) >= guard_s:
                early_stopped = True
                stop.set()
                break
            time.sleep(poll_interval_s)

    capture_start = time.monotonic()
    wait_ended_at: float | None = None
    hook_error: Exception | None = None
    state_subscribed = False
    state_data_topic = f"{device_path}/s/gpi/{state_input_index}/!data"
    state_ctrl_topic = f"{device_path}/s/gpi/{state_input_index}/ctrl"
    try:
        if family in ("js220", "js320"):
            mode = driver.query(f"{device_path}/s/gpi/+/dwnN/mode")
            gate_recorder.sample_mode = {0: "off", 2: "first", "off": "off", "first": "first"}.get(
                mode
            )
            if gate_recorder.sample_mode is None:
                raise PowerError("GPI decimation mode does not preserve sampled gate levels")
            state_recorder.sample_mode = gate_recorder.sample_mode
        try:
            driver.publish(f"{device_path}/{cycle_topic}", on_value)
        except Exception:
            pass

        driver.publish(f"{device_path}/{scnt_topic}", scnt)
        driver.publish(f"{device_path}/{sctrl_topic}", 1)
        driver.subscribe(f"{device_path}/{sval_topic}", "pub", _on_stats)
        gpi_stream_enabled = False
        gpi_subscribed = False
        gpi_data_topic = f"{device_path}/s/gpi/{sync_input_index}/!data"
        gpi_ctrl_topic = f"{device_path}/s/gpi/{sync_input_index}/ctrl"
        if family in ("js220", "js320"):
            try:
                driver.subscribe(gpi_data_topic, "pub", _on_gpi_data)
                gpi_subscribed = True
                driver.publish(gpi_ctrl_topic, 1)
                gpi_stream_enabled = True
                if require_done_state:
                    driver.subscribe(state_data_topic, "pub", _on_state_data)
                    state_subscribed = True
                    driver.publish(state_ctrl_topic, 1)
            except Exception:
                if require_done_state:
                    state_recorder.error = "Required state stream could not be enabled"
                log.warning(
                    "GPI edge streaming unavailable on %s; gate edges fall "
                    "back to snapshot polling",
                    device_path,
                    exc_info=True,
                )
        if fr_xcheck:
            fr_xcheck = fullrate.start()
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
                    on_started(_wait_gpi_state)
                    go_release_at = time.monotonic()
                except Exception as exc:
                    hook_error = exc
                    raise
            stop.wait(timeout=duration_s)
            wait_ended_at = time.monotonic()
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
            if gpi_subscribed:
                try:
                    driver.publish(gpi_ctrl_topic, 0)
                except Exception:
                    pass
                try:
                    driver.unsubscribe(gpi_data_topic, _on_gpi_data)
                except Exception:
                    pass
            if state_subscribed:
                try:
                    driver.publish(state_ctrl_topic, 0)
                except Exception:
                    pass
                try:
                    driver.unsubscribe(state_data_topic, _on_state_data)
                except Exception:
                    pass
            if fr_requested:
                fullrate.stop()

        if callback_errors:
            hook_error = callback_errors[0]
            raise hook_error

        aligned_poll_samples = poll_samples
        use_device_time_axis = family in ("js220", "js320")

        # Prefer instrument-clock gate edges from the GPI stream whenever it
        # produced at least one window that clears the minimum gate.  The
        # snapshot-poll segmentation stays authoritative otherwise — including
        # the degraded no-usable-window path, whose classification must keep
        # seeing exactly what the poller saw.
        # A high open at the stop is the window the bound cut short: with a plan,
        # no shorter high stands in for it and it degrades as no_gate_fall.
        open_high = (
            first_high_at is not None
            and first_low_after_high_at is None
            and time.monotonic() - first_high_at >= minimum_gate_s
        )
        allow_fallback = not (open_high and plan_floor_s > minimum_gate_s)
        fall_seen = saw_any_gate_fall and allow_fallback
        streamed_gate_windows: list[tuple[float, float]] | None = None
        raw_streamed: list[tuple[float, float]] = []
        gate_edge_source = "gpi_snapshot_poll"
        counter_origin = None
        counter_trace = None
        counter_evidence = []
        if gpi_stream_enabled:
            try:
                if gate_recorder.error:
                    raise ValueError(gate_recorder.error)
                # These sensors supply statistics and all signal sample IDs
                # from the same native counter. Do not infer another origin via UTC.
                if family not in ("js220", "js320"):
                    raise ValueError("Shared GPI/statistics counter contract unavailable")
                counter_trace = counter_gate_trace(
                    gpi_stream_frames, packets, channel=sync_input_index
                )
                counter_origin = (counter_trace.origin, counter_trace.rate)
                raw_streamed = [
                    (
                        (a - counter_trace.origin) * time64.SECOND / counter_trace.rate,
                        (b - counter_trace.origin) * time64.SECOND / counter_trace.rate,
                    )
                    for a, b in counter_trace.windows
                ]
                if require_done_state:
                    counter_trace, completed = _completed_trace()
                    index = counter_trace.windows.index((completed.rise, completed.fall))
                else:
                    selected = _select_streamed_window(
                        raw_streamed,
                        minimum_gate_s=minimum_gate_s,
                        plan_floor_s=plan_floor_s,
                        allow_fallback=allow_fallback,
                    )
                    if selected is None:
                        raise ValueError("No complete qualifying packed GPI gate")
                    index = raw_streamed.index(selected[0])
                streamed_gate_windows = [raw_streamed[index]]
                counter_evidence.append(
                    counter_trace.window_evidence(*counter_trace.windows[index])
                )
                gate_edge_source = "gpi_stream_sample_counter"
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                gate_recorder.error = str(exc)
                counter_origin = None
                streamed_gate_windows = []

        if use_device_time_axis and streamed_gate_windows is None:
            aligned_poll_samples = _map_poll_samples_to_packet_time(
                packets=packets,
                poll_samples=poll_samples,
                minimum_window_s=minimum_gate_s,
            )

        selected_windows = streamed_gate_windows
        if selected_windows is None and plan_floor_s > minimum_gate_s:
            # The poll samples can hold a high the plan rejected as well as the
            # real window: select by the stream's rule instead of summing both.
            polled = _plan_ranked_candidates(
                _segment_gpi_windows(aligned_poll_samples),
                minimum_gate_s=minimum_gate_s,
                plan_floor_s=plan_floor_s,
                allow_fallback=allow_fallback,
            )
            # An empty override forces the degraded path when an open window
            # withheld the fallback; otherwise no candidate keeps the legacy one.
            selected_windows = polled[-1:] if polled or not allow_fallback else None

        dump_dir = os.environ.get("HPX_GATE_DEBUG_DUMP")
        if dump_dir:
            _write_gate_dump(
                dump_dir,
                packets=packets,
                gpi_stream_frames=gpi_stream_frames,
                capture_start=capture_start,
                go_release_at=go_release_at,
                first_high_at=first_high_at,
                first_low_after_high_at=first_low_after_high_at,
                poll_samples=poll_samples,
                aligned_poll_samples=aligned_poll_samples,
                gate_edge_source=gate_edge_source,
                raw_streamed=raw_streamed,
                streamed_gate_windows=streamed_gate_windows,
            )

        gating_diagnostics = _gated_stats_diagnostics(
            packets=packets,
            poll_samples=aligned_poll_samples,
            prefer_device_time=use_device_time_axis,
            windows_override=selected_windows,
            gate_edge_source=gate_edge_source,
            stream_segment_count=len(raw_streamed) if gpi_stream_enabled else None,
            counter_origin=counter_origin,
        )
        if fr_requested and not fr_xcheck:
            gating_diagnostics["fullrate_xcheck_unavailable_reason"] = "stream_setup_failed"
        if gpi_poll_failures:
            gating_diagnostics["gpi_poll_failures"] = gpi_poll_failures
        if below_plan_highs:
            gating_diagnostics["below_plan_highs"] = below_plan_highs
        if gate_edge_source == "gpi_snapshot_poll":
            gating_diagnostics["poll_edge_uncertainty_s"] = _poll_edge_uncertainty_s(
                poll_reads, minimum_window_s=minimum_gate_s, plan_floor_s=plan_floor_s
            )

        gating_diagnostics["packed_gpi_retained_bytes"] = gate_recorder.retained_bytes
        gating_diagnostics["packed_gpi_error"] = gate_recorder.error
        gating_diagnostics["counter_gate_evidence"] = counter_evidence
        if counter_trace is not None:
            gating_diagnostics["counter_clock_mapping"] = counter_trace.clock_diagnostics
        # The filter that scales utc, read straight from the packets (#249).
        counter_rate = _counter_rate_ratio(packets)
        if counter_rate is not None:
            gating_diagnostics["instrument_time_map"] = counter_rate

        windows, gated_summary = _process_gated_stats(
            packets=packets,
            poll_samples=aligned_poll_samples,
            io_voltage=io_voltage,
            prefer_device_time=use_device_time_axis,
            minimum_window_s=minimum_gate_s,
            windows_override=selected_windows,
            counter_origin=counter_origin,
        )
        if not windows:
            if fr_requested:
                gating_diagnostics.setdefault(
                    "fullrate_xcheck_unavailable_reason", "no_integrable_gate_samples"
                )
            planned_window_s = (
                clean_infer_count * clean_infer_avg_us / 1_000_000
                if clean_infer_count and clean_infer_avg_us
                else None
            )
            longest_window_s = (
                longest_accepted_window_s(
                    clean_infer_count=clean_infer_count,
                    clean_infer_avg_us=clean_infer_avg_us,
                    stats_rate_hz=stats_rate_hz,
                    relative_tolerance=gate_relative_tolerance,
                )
                if clean_infer_count and clean_infer_avg_us
                else None
            )
            gate_high_s = (
                max(0.0, wait_ended_at - first_high_at)
                if wait_ended_at is not None and first_high_at is not None
                else None
            )
            # Without lock-step the wait starts at reset, so the window opens
            # only after boot and warm-up.
            rise_due_s = BOOT_SETTLE_S + pre_window_s if lockstep is False else None
            failure = classify_gate_failure(
                saw_gate_rise=saw_any_gate_rise,
                saw_gate_fall=fall_seen,
                duration_s=duration_s,
                lockstep=lockstep,
                lockstep_wiring_available=lockstep_wiring_available,
                planned_window_s=planned_window_s,
                gate_high_s=gate_high_s,
                longest_window_s=longest_window_s,
                rise_due_s=rise_due_s,
            )
            failure = _with_gpi_poll_failures(failure, gpi_poll_failures)
            if not packets:
                raise PowerError(failure.message, hint=failure.hint)
            captured_s = time.monotonic() - capture_start
            result = _degraded_observation_result(
                packets=packets,
                family=family,
                device_path=device_path,
                io_voltage=io_voltage,
                sync_input_index=sync_input_index,
                stats_rate_hz=stats_rate_hz,
                scnt=scnt,
                poll_count=len(poll_samples),
                duration_s=duration_s,
                captured_s=captured_s,
                saw_gate_rise=saw_any_gate_rise,
                saw_gate_fall=fall_seen,
                short_pulses_ignored=short_pulses_ignored,
                failure=failure,
                gating_diagnostics=gating_diagnostics,
            )
            # The hint is logged, not just stored in metadata: on the degraded
            # path there is no PowerError to carry it, so the terminal warning
            # is the only place the user is told what to change (issue #114).
            log.warning(
                "%s; retaining %.3fs free-form capture for post-run diagnostics. %s",
                failure.message,
                result.summary.duration_s,
                failure.hint,
            )
            return replace(
                result,
                gate_frames=gpi_stream_frames,
                gate_stream_error=gate_recorder.error,
                gate_samples=aligned_poll_samples,
                statistics_timebase_hz=int(time64.SECOND),
                statistics_device_time=use_device_time_axis,
                state_frames=state_recorder.frames,
                state_stream_error=state_recorder.error,
            )
        if short_pulses_ignored:
            log.warning(
                "Ignored %d GPIO-high pulse(s) shorter than the %.3fs minimum "
                "before the valid power gate (phases=%s, first=%.3fs, last=%.3fs)",
                short_pulses_ignored,
                minimum_gate_s,
                short_pulse_phases,
                short_pulse_first_s or 0.0,
                short_pulse_last_s or 0.0,
            )

        gate_integrity = None
        if (
            clean_infer_count is not None
            and clean_infer_count > 0
            and clean_infer_avg_us is not None
            and clean_infer_avg_us > 0
        ):
            from ..diagnostics import assess_gate_duration

            gate_integrity = assess_gate_duration(
                measured_s=gated_summary.duration_s,
                clean_infer_count=clean_infer_count,
                clean_infer_avg_us=clean_infer_avg_us,
                stats_rate_hz=stats_rate_hz,
                minimum_s=minimum_gate_s,
                relative_tolerance=gate_relative_tolerance,
            )
            if not gate_integrity.valid:
                # Deliberately NOT a raise (#142/#181): the est*count
                # expectation straddles boots and thermal states, so a healthy
                # cold boot can undershoot it. evaluation.validity arbitrates
                # with the firmware's own STIMER window (same boot, same
                # physical window); raising here would discard the capture
                # before its best evidence arrives and leave no artifact.
                log.warning(
                    "Gated window duration %.6fs differs from the planned "
                    "%d %s at %dus each (expected %.6fs, band %.6fs, "
                    "minimum %.3fs). Recording for validity evaluation; the "
                    "power-terminal envelope arbitrates whether this is "
                    "clock drift or a broken gate.",
                    gate_integrity.measured_s,
                    clean_infer_count,
                    work_noun,
                    clean_infer_avg_us,
                    gate_integrity.expected_s,
                    gate_integrity.tolerance_s,
                    minimum_gate_s,
                )

        captured_s = time.monotonic() - capture_start
        metadata = PowerMetadata(
            driver=f"joulescope-{family}",
            device=device_path,
            io_voltage=io_voltage,
            measurement_scope=MeasurementScope.GPIO_GATED_CLEAN_WINDOW,
            gating_method=f"{gate_edge_source}+host_stats_integral",
            sync_input_index=sync_input_index,
            stats_rate_hz=stats_rate_hz,
            stats_scnt=scnt,
            window_count=len(windows),
            gpi_poll_count=len(poll_samples),
            stat_packets=len(packets),
            early_stopped=early_stopped,
            capture_window_s=round(captured_s, 4),
            capture_safety_bound_s=duration_s,
            short_gate_pulses_ignored=short_pulses_ignored,
        )
        if short_pulses_ignored:
            metadata.short_gate_pulse_diagnostics = {
                "by_phase": dict(short_pulse_phases),
                "first_rise_s": round(short_pulse_first_s or 0.0, 6),
                "last_rise_s": round(short_pulse_last_s or 0.0, 6),
            }
        if first_high_at is None and below_plan_rise_at is not None:
            # No high reached the plan, so the fallback kept the last shorter
            # one: report its edges.
            first_high_at, first_low_after_high_at = below_plan_rise_at, below_plan_fall_at
        gate_timing = GateTransitionTiming(
            capture_to_gate_rise_s=(
                round(first_high_at - capture_start, 6) if first_high_at is not None else None
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
        if gate_timing.to_metadata():
            metadata.sync_timing_s = gate_timing
        if clean_infer_count is not None:
            metadata.clean_infer_count = clean_infer_count
        if gate_integrity is not None:
            metadata.gate_duration_integrity = replace(
                gate_integrity, relative_tolerance=gate_relative_tolerance
            )
        if fr_xcheck:
            fr, reason = fullrate.integrate(
                poll_samples=aligned_poll_samples,
                windows=selected_windows,
                counter_origin=counter_origin,
            )
            if reason is not None:
                gating_diagnostics["fullrate_xcheck_unavailable_reason"] = reason
            if fr:
                metadata.fullrate_xcheck = fr
                stats_energy_per = gated_summary.energy_j / len(windows) if windows else 0.0
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
                gating_diagnostics.setdefault(
                    "fullrate_xcheck_unavailable_reason", "no_integrable_gate_samples"
                )
                log.warning(
                    "Full-rate cross-check requested but produced no result "
                    "(chunks=%d, anchors=%d)",
                    len(fullrate.current),
                    len(fullrate.anchors),
                )
        _attach_capture_diagnostics(
            metadata, packets, gated_summary, gating_diagnostics, stats_rate_hz
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
            state_frames=state_recorder.frames,
            state_stream_error=state_recorder.error,
            statistics=packets,
            gate_samples=aligned_poll_samples,
            statistics_timebase_hz=int(time64.SECOND),
            statistics_device_time=use_device_time_axis,
            gate_frames=gpi_stream_frames,
            gate_stream_error=gate_recorder.error,
            counter_gate_evidence=counter_evidence,
            counter_domain="joulescope_sensor_sample_id" if counter_evidence else None,
        )
    except PowerError:
        raise
    except Exception as exc:
        # The start hook's failure is the caller's error to report, not a
        # capture failure.
        if exc is hook_error:
            raise
        raise PowerError(
            f"Joulescope gated capture failed: {exc}",
            hint="Check USB connection, sync wiring, and that no other software is using the device.",
        ) from exc
    finally:
        _close_device(driver, device_path)
