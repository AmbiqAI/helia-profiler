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
from typing import TYPE_CHECKING, Any

from ...errors import PowerError
from ..base import PowerResult
from ..counter_gate import PackedGateRecorder
from ..diagnostics import (
    GATE_EDGE_POLL_INTERVAL_S,
)
from ._gated_resources import _GatedCaptureResources
from ._gated_result import _CaptureInfo, _MeasurementPlan, assemble_result, reduce_measurement
from ._gated_selection import completed_gate_trace, select_gate
from ._gated_state import _GatedCaptureState
from .device import (
    _NATIVE_SAMPLE_RATE,
    _close_device,
    _open_device,
    _read_gpi_snapshot,
)
from .diagnostics import (
    _write_gate_dump,
)
from .stats import (
    _plan_gate_floor_s,
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

    try:
        from pyjoulescope_driver import time64
    except Exception as exc:
        raise PowerError(
            f"Joulescope gated capture requires pyjoulescope_driver: {exc}",
            hint="Reinstall pyjoulescope-driver and pyjls in the active environment.",
        ) from exc

    log.debug("gate-race timeline: _open_device() start t=%.3f", time.time())
    driver, device_path, family = _open_device(self._serial)
    try:
        log.debug("gate-race timeline: _open_device() done t=%.3f", time.time())

        native_rate = _NATIVE_SAMPLE_RATE[family]
        scnt = max(1, round(native_rate / max(1, int(stats_rate_hz))))

        packets: list[dict[str, Any]] = []
        stop = threading.Event()
        bit = 1 << sync_input_index
        go_release_at: float | None = None
        gpi_condition = threading.Condition()
        latest_gpi_value: int | None = None
        gpi_sample_sequence = 0
        gpi_poll_failures = 0

        def _completed_trace():
            return completed_gate_trace(
                packets=packets,
                gate_frames=gpi_stream_frames,
                state_frames=state_recorder.frames,
                sync_input_index=sync_input_index,
                state_input_index=state_input_index,
                gate_error=gate_recorder.error,
                state_error=state_recorder.error,
            )

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
            with resources.processing() as active:
                if not active:
                    return
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
            with resources.processing() as active:
                if not active:
                    return
                gate_recorder.append(value, word_aligned=family in ("js220", "js320"))

        def _on_state_data(_topic: str, value: Any) -> None:
            with resources.processing() as active:
                if not active:
                    return
                state_recorder.append(value, word_aligned=family in ("js220", "js320"))

        # Opt-in full-rate cross-check (AutoDeploy-equivalent reference method).
        # Set HPX_POWER_FULLRATE_XCHECK=1 to also stream raw s/i + s/v at the
        # instrument's native rate and integrate energy over the GPI windows,
        # logged alongside the 1 kHz stats-sum for direct comparison.
        fr_requested = os.environ.get("HPX_POWER_FULLRATE_XCHECK") == "1"
        resources = _GatedCaptureResources(driver, device_path, family)
        fullrate = resources.fullrate

        callback_errors: list[Exception] = []

        def _abort_callback(exc: Exception) -> None:
            callback_errors.append(exc)
            stop.set()
            with gpi_condition:
                gpi_condition.notify_all()

        def _phase() -> str | None:
            with resources.processing() as active:
                if not active or stop.is_set():
                    return None
                try:
                    return phase_getter() if phase_getter is not None else "go_signaled"
                except Exception as exc:
                    _abort_callback(exc)
                    return None

        def _poller() -> None:
            nonlocal gpi_sample_sequence, latest_gpi_value, gpi_poll_failures
            while not stop.is_set():
                if _phase() is None:
                    return
                gpi_value: int | None = None
                try:
                    read_start = _host_monotonic_time64(time64)
                    gpi_value = _read_gpi_snapshot(driver, device_path)
                    sample_tick = _host_monotonic_time64(time64)
                    with resources.processing() as active:
                        if not active or stop.is_set():
                            return
                        with gpi_condition:
                            latest_gpi_value = gpi_value
                            gpi_sample_sequence += 1
                            gpi_condition.notify_all()
                        level = 1 if (int(gpi_value) & bit) else 0
                        phase = _phase()
                        if phase is None:
                            return
                        rose = control.observe(
                            read_start=read_start,
                            sample_tick=sample_tick,
                            level=level,
                            state_high=bool(gpi_value & (1 << state_input_index)),
                            phase=phase,
                        )
                        if phase != "go_signaled":
                            stop.wait(timeout=poll_interval_s)
                            continue
                        # Drop GO promptly: holding it high can backfeed the target
                        # around the shunt (see power.sync.release_go).
                        if rose and on_gate_rise is not None:
                            try:
                                on_gate_rise()
                                log.debug(
                                    "gate-race timeline: on_gate_rise (GO drop) done t=%.3f",
                                    time.time(),
                                )
                            except Exception as exc:
                                _abort_callback(exc)
                                return
                        if require_done_state:
                            valid = False
                            if control.completion_pending:
                                try:
                                    _completed_trace()
                                except (KeyError, TypeError, ValueError, OverflowError):
                                    pass
                                else:
                                    valid = True
                            control.confirm_completion(valid)
                except Exception:
                    with resources.processing() as active:
                        if not active or stop.is_set():
                            return
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
                with resources.processing() as active:
                    if not active or stop.is_set():
                        return
                    if control.guard_elapsed(guard_s):
                        stop.set()
                        break
                stop.wait(timeout=poll_interval_s)

        capture_start = time.monotonic()
        control = _GatedCaptureState(
            minimum_gate_s=minimum_gate_s,
            plan_floor_s=plan_floor_s,
            require_done_state=require_done_state,
            capture_start=capture_start,
            monotonic=time.monotonic,
            wall_time=time.time,
        )
        wait_ended_at: float | None = None
        hook_error: Exception | None = None
        try:
            if family in ("js220", "js320"):
                mode = driver.query(f"{device_path}/s/gpi/+/dwnN/mode")
                gate_recorder.sample_mode = {
                    0: "off",
                    2: "first",
                    "off": "off",
                    "first": "first",
                }.get(mode)
                if gate_recorder.sample_mode is None:
                    raise PowerError("GPI decimation mode does not preserve sampled gate levels")
                state_recorder.sample_mode = gate_recorder.sample_mode
            try:
                resources.start_streams(
                    scnt=scnt,
                    sync_input_index=sync_input_index,
                    state_input_index=state_input_index,
                    require_done_state=require_done_state,
                    on_stats=_on_stats,
                    on_gpi_data=_on_gpi_data,
                    on_state_data=_on_state_data,
                    state_recorder=state_recorder,
                    fullrate_requested=fr_requested,
                )
                gpi_stream_enabled = resources.gpi_stream_enabled
                fr_xcheck = resources.fr_xcheck
                resources.start_poller(_poller)
                # USB CDC firmware can wait on DTR. Start the poller before the
                # hook releases it; this does not wait for the first GPI sample.
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
                resources.stop(stop)

            if callback_errors:
                hook_error = callback_errors[0]
                raise hook_error

            evidence = control.snapshot()
            elapsed_high_s = (
                time.monotonic() - evidence.first_high_at
                if evidence.first_high_at is not None and evidence.first_low_after_high_at is None
                else None
            )
            selection = select_gate(
                packets=packets,
                gpi_stream_frames=gpi_stream_frames,
                state_frames=state_recorder.frames,
                poll_samples=list(evidence.poll_samples),
                family=family,
                sync_input_index=sync_input_index,
                state_input_index=state_input_index,
                statistics_timebase_hz=int(time64.SECOND),
                minimum_gate_s=minimum_gate_s,
                plan_floor_s=plan_floor_s,
                elapsed_high_s=elapsed_high_s,
                saw_any_gate_fall=evidence.saw_any_gate_fall,
                gpi_stream_enabled=gpi_stream_enabled,
                require_done_state=require_done_state,
                gate_error=gate_recorder.error,
                state_error=state_recorder.error,
            )
            dump_dir = os.environ.get("HPX_GATE_DEBUG_DUMP")
            if dump_dir:
                _write_gate_dump(
                    dump_dir,
                    packets=packets,
                    gpi_stream_frames=gpi_stream_frames,
                    capture_start=capture_start,
                    go_release_at=go_release_at,
                    first_high_at=evidence.first_high_at,
                    first_low_after_high_at=evidence.first_low_after_high_at,
                    poll_samples=list(evidence.poll_samples),
                    aligned_poll_samples=selection.aligned_poll_samples,
                    gate_edge_source=selection.gate_edge_source,
                    raw_streamed=selection.raw_streamed,
                    streamed_gate_windows=selection.streamed_gate_windows,
                )
            info = _CaptureInfo(
                family=family,
                device_path=device_path,
                io_voltage=io_voltage,
                sync_input_index=sync_input_index,
                stats_rate_hz=stats_rate_hz,
                scnt=scnt,
                duration_s=duration_s,
            )
            plan = _MeasurementPlan(
                minimum_gate_s=minimum_gate_s,
                plan_floor_s=plan_floor_s,
                clean_infer_count=clean_infer_count,
                clean_infer_avg_us=clean_infer_avg_us,
                gate_relative_tolerance=gate_relative_tolerance,
                work_noun=work_noun,
                lockstep=lockstep,
                lockstep_wiring_available=lockstep_wiring_available,
                pre_window_s=pre_window_s,
            )
            reduced = reduce_measurement(
                packets=packets,
                selection=selection,
                evidence=evidence,
                info=info,
                plan=plan,
                gpi_stream_enabled=gpi_stream_enabled,
                gate_retained_bytes=gate_recorder.retained_bytes,
                gpi_poll_failures=gpi_poll_failures,
                wait_ended_at=wait_ended_at,
                fr_requested=fr_requested,
                fr_xcheck=fr_xcheck,
            )
            captured_s = time.monotonic() - capture_start
            fr, reason = None, None
            if reduced.windows and fr_xcheck:
                fr, reason = fullrate.integrate(
                    poll_samples=selection.aligned_poll_samples,
                    windows=selection.selected_windows,
                    counter_origin=selection.counter_origin,
                )
            return assemble_result(
                packets=packets,
                gpi_stream_frames=gpi_stream_frames,
                state_frames=state_recorder.frames,
                state_error=state_recorder.error,
                selection=selection,
                reduced=reduced,
                evidence=evidence,
                info=info,
                plan=plan,
                capture_start=capture_start,
                go_release_at=go_release_at,
                captured_s=captured_s,
                statistics_timebase_hz=int(time64.SECOND),
                fr_xcheck=fr_xcheck,
                fr=fr,
                reason=reason,
                fullrate_chunks=len(fullrate.current),
                fullrate_anchors=len(fullrate.anchors),
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
