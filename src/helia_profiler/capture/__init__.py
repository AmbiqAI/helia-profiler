"""Data capture from target hardware.

Supports the following transports for reading profiling data from the target:

- **RTT** (recommended): Lossless, flow-controlled via SEGGER RTT over SWD.
- **USB CDC**: CRC-protected USB serial, requires USB connection.
- **SWO**: ITM debug output, minimal setup but no flow control.
- **UART**: Output over the J-Link OB virtual COM port; for boards without
  a USB device stack (e.g. Apollo3). 115200 8N1, no flow control.

- ``capture_pmu``: Read PMU / DWT counters and per-layer breakdown.
- ``capture_power``: Record current/voltage traces via power driver.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from ..config import DEFAULT_POWER_DURATION_S
from ..errors import CaptureError, PowerError
from ..power.diagnostics import (
    CLEAN_WINDOW_WARMUP_REPS,
    SyncHandshakeMetadata,
    count_noun,
    gate_fall_wait_s,
    gate_relative_tolerance_for,
    lockstep_ready_wait_s,
    longest_accepted_window_s,
    probe_runs_inferences,
)
from ..results.models import DEVICE_CLOCK_TOLERANCE
from ..transport import (
    LINE_TIMEOUT_S,
    CaptureArgs,
    resolve_transport,
)
from ..transport.firmware_errors import raise_on_firmware_error
from ..transport.usb_identity import usb_marker_serial
from ..vocab import Transport
from ..wire import HPX_END_SENTINEL, HPX_START_SENTINEL

if TYPE_CHECKING:
    from ..pipeline import PipelineContext
    from ..power.base import PowerDriver, PowerResult
    from ..power.sync import SyncController
    from ..results import PmuResult
    from ..target.lifecycle import TargetLifecyclePlan

log = logging.getLogger("hpx")

_LOCKSTEP_RESET_GRACE_S = 0.5


def capture_pmu(ctx: PipelineContext) -> PmuResult:
    """Read PMU data from the target via serial port.

    Returns a :class:`PmuResult` with firmware metadata, per-preset breakdowns,
    and merged per-layer results.
    """
    from .parser import parse_firmware_output

    transport = ctx.config.target.transport

    jlink_serial = ctx.effective_jlink_serial
    hb = ctx.config.target.heartbeat
    heartbeat_timeout_s = hb.host_timeout_s if hb.enabled else LINE_TIMEOUT_S
    overall_timeout_s = hb.overall_timeout_s

    if ctx.soc is None or not ctx.soc.jlink_device:
        raise CaptureError(
            "No J-Link device string — platform resolution did not run.",
            hint="Ensure stage 1 (resolve_platform) runs before capture.",
        )
    jlink_device = ctx.soc.jlink_device

    # The Cortex-M4F families (Apollo3/3P and Apollo4/4P) gate DWT->CYCCNT
    # behind the debug power domain, which only stays powered while a debugger
    # is attached.  UART/USB normally release the probe after reset, so on those
    # SoCs the readers must hold a pylink session open for the whole capture or
    # every per-layer cycle reads back 0.
    keep_debugger_attached = ctx.soc.requires_attached_probe_for_cycles

    # Use build_dir from context (set by stage 4) — no re-derivation
    build_dir = ctx.build_dir
    timing_raw: dict[str, float] = {}

    backend = resolve_transport(transport)
    capture_args = CaptureArgs(
        jlink_serial=jlink_serial,
        jlink_device=jlink_device,
        keep_debugger_attached=keep_debugger_attached,
        overall_timeout_s=overall_timeout_s,
        heartbeat_timeout_s=heartbeat_timeout_s,
        build_dir=build_dir,
        timing_raw=timing_raw,
        reset_controller=ctx.reset_controller,
    )
    backend.prepare(ctx, capture_args)
    lines = backend.collect(ctx)
    if not lines:
        raise CaptureError(
            f"No data captured via {transport} transport",
            hint="Ensure the firmware is running. Try resetting the board.",
        )

    # A firmware-reported error is more specific than any "no layer data"
    # fallback message below, so surface it first with the best hint we can.
    raise_on_firmware_error(lines, power_enabled=bool(ctx.config.power.enabled))

    # Pre-parse validation: check for protocol sentinels.  Scan the whole
    # capture, not just the head: the SWO transport emits a variable-length
    # HPX_READY sync preamble before "--- HPX_START ---" (see the firmware
    # templates), so the sentinel does not sit at a fixed offset.  The parser
    # likewise ignores everything before HPX_START.
    if not any(HPX_START_SENTINEL in l for l in lines):
        hint = (
            "The firmware may not be running the profiler app, or the "
            "transport connection failed before data arrived."
        )
        soc = ctx.soc
        if transport is Transport.SWO and soc is not None and soc.swo_trace_clock_mhz is None:
            # Core-clocked SWO decodes only at the registry clock, so a perf
            # mode that did not engage garbles the stream before the
            # HPX_MEASURED_CLOCK_HZ check can run.
            hint += (
                " On this SoC SWO baud follows the core clock: if the "
                "target.clock.cpu perf mode did not take effect, nothing "
                "decodes. Retry with --transport rtt, which does not depend "
                "on the core clock and reports the measured clock."
            )
        raise CaptureError(
            f"Captured data ({len(lines)} lines) does not contain HPX_START sentinel",
            hint=hint,
        )
    if not any(l.strip() == HPX_END_SENTINEL for l in lines):
        raise CaptureError(
            f"Capture ended before HPX_END ({len(lines)} lines).",
            hint=_truncation_hint(str(transport)),
        )

    result = parse_firmware_output(lines, aggregation=ctx.config.profiling.aggregation)
    if not result.layers:
        raise CaptureError(
            f"No layer data parsed from firmware output ({len(lines)} lines).",
            hint=_truncation_hint(str(transport)),
        )

    _verify_device_clock(ctx, result)

    if timing_raw:
        from ..results import TimingInfo

        # Collect the attributed boot/attach phase breakdown (RTT records
        # reset / sbl_settle / attach / control_block_scan / line_collection).
        phases = {
            key[len("rtt_phase_") : -len("_s")]: round(value, 6)
            for key, value in timing_raw.items()
            if key.startswith("rtt_phase_") and key.endswith("_s")
        }
        ctx.run_metadata.timing = TimingInfo(
            capture_duration_s=timing_raw.get("capture_duration_s"),
            hpx_start_latency_s=timing_raw.get("hpx_start_latency_s"),
            protocol_duration_s=timing_raw.get("protocol_duration_s"),
            phases=phases or None,
        )

    return result


class _UsbDtrHolder:
    """Hold a USB CDC port open (DTR asserted) for a gated power capture.

    The USB firmware spins in ``nsx_usb_connected()`` until the host opens its
    CDC port and raises DTR.  During a Joulescope-gated power run nothing reads
    firmware output, so this just resolves the target's CDC port, opens it, and
    asserts DTR — releasing the firmware to run the gated clean window — then
    holds it open until :meth:`close`.
    """

    def __init__(self, *, usb_port: str | None, usb_marker: str | None) -> None:
        self._usb_port = usb_port
        self._usb_marker = usb_marker
        self._ser = None

    def open(self) -> None:
        from ..transport.usb_cdc import open_cdc_port, resolve_target_cdc_port

        port = resolve_target_cdc_port(usb_port=self._usb_port, marker=self._usb_marker)
        log.info("Opening USB CDC port for gated power capture: %s", port)
        self._ser = open_cdc_port(port, timeout=1.0)

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                log.debug("Failed to close USB DTR holder port", exc_info=True)
            finally:
                self._ser = None


def _make_sync_controller(ctx: PipelineContext, driver: PowerDriver) -> SyncController:
    """Build a host sync controller from config, or a gate-only fallback.

    Lock-step is resolved via ``target.lifecycle.resolve_power_lockstep``
    (which delegates to ``PowerConfig.lockstep_resolved``): an explicit
    ``power.lockstep`` setting always wins, otherwise it is auto-enabled when
    the board is wired for it and gated external capture is requested. Without
    lock-step, or on drivers that cannot drive a GO output, the controller is
    a no-op and the device free-runs -- which is exactly the state that lets
    the measured window race the GPI poller (issue #114).
    """
    from ..power.sync import NullSyncController, SyncWiring
    from ..target.lifecycle import resolve_power_lockstep

    resolved_lockstep = resolve_power_lockstep(ctx)
    # make_sync_controller(wiring) -> SyncController is an optional driver
    # method, not part of PowerDriver: only drivers with a host-drivable GO
    # output (Joulescope) provide it. getattr, not a Protocol isinstance:
    # runtime_checkable isinstance uses getattr_static, which misses a driver
    # exposing the method dynamically (__getattr__) -- such a driver would
    # silently degrade to gate-only capture.
    make_controller = getattr(driver, "make_sync_controller", None)
    log.debug(
        "gate-race timeline: resolve_power_lockstep=%s (configured=%s, "
        "has_make_sync_controller=%s)",
        resolved_lockstep,
        ctx.config.power.lockstep,
        callable(make_controller),
    )
    if not resolved_lockstep or not callable(make_controller):
        return NullSyncController()
    wiring = SyncWiring(
        lockstep=True,
        gate_input_index=ctx.config.power.sync_input_index,
        state_input_index=ctx.config.power.state_input_index,
        go_output_index=ctx.config.power.go_output_index,
    )
    return make_controller(wiring)


def _profiled_inference_s(ctx: PipelineContext) -> float | None:
    """One inference at the run's CPU clock, from the profiled PMU cycles."""
    pmu = ctx.pmu_result
    platform = ctx.run_metadata.platform
    if pmu is None or platform is None or platform.cpu_clock_mhz <= 0:
        return None
    cycles = sum(layer.cycles or 0 for layer in pmu.layers)
    return cycles / (platform.cpu_clock_mhz * 1_000_000) if cycles > 0 else None


def capture_power(
    ctx: PipelineContext,
    *,
    duration_override_s: float | None = None,
    prepare_target: Callable[["PowerDriver", str], "TargetLifecyclePlan"] | None = None,
) -> PowerResult:
    """Record a power trace using the configured power driver.

    Returns a :class:`PowerResult` directly — no intermediate dict wrapping.
    """
    from ..power import get_driver

    # Deployment is an explicit preceding stage. Validate that contract before
    # touching the physical instrument.
    if ctx.power_run is None:
        raise PowerError(
            "Power run has not been planned.",
            hint="Run the power planning stage before capture.",
        )
    plan = ctx.power_run.plan
    effective_firmware = plan.firmware_mode
    if effective_firmware == "dedicated" and ctx.power_run.deployment is None:
        raise PowerError(
            "Dedicated power firmware has not been deployed.",
            hint="Run the plan/build/flash power stages before capture.",
        )

    driver_name = ctx.config.power.driver
    driver = get_driver(driver_name, serial=ctx.config.power.serial)

    driver.check_available()
    lifecycle_plan = None

    def _prepare_target_once() -> None:
        nonlocal lifecycle_plan
        if prepare_target is not None and lifecycle_plan is None:
            lifecycle_plan = prepare_target(driver, driver_name)

    def _attach_lifecycle_metadata(result: PowerResult) -> PowerResult:
        if result.metadata.power_firmware is None:
            result.metadata.power_firmware = effective_firmware
        if lifecycle_plan is not None and result.metadata.target_lifecycle is None:
            result.metadata.target_lifecycle = lifecycle_plan
        return result

    duration = (
        duration_override_s
        if duration_override_s is not None
        else (
            ctx.config.power.duration_s
            if ctx.config.power.duration_s is not None
            else DEFAULT_POWER_DURATION_S
        )
    )

    clean_count = plan.inference_count
    clean_avg_us = plan.reference_inference_us
    may_use_profile_metadata = effective_firmware == "shared"
    if clean_count is None and may_use_profile_metadata and ctx.pmu_result is not None:
        clean_count = ctx.pmu_result.meta.clean_infer_count
    if clean_avg_us is None and may_use_profile_metadata and ctx.pmu_result is not None:
        clean_avg_us = ctx.pmu_result.meta.clean_infer_avg_us

    if effective_firmware == "dedicated" and clean_count is None:
        raise PowerError(
            "Dedicated power run has no authoritative inference count.",
            hint=(
                "Provide a fixed inference count or run profile capture first so "
                "the power planning stage can derive one."
            ),
        )

    if getattr(driver, "supports_gated_capture", False) and clean_count is not None:
        from ..config import DEFAULT_POWER_MIN_WINDOW_MS

        # USB CDC firmware blocks in nsx_usb_connected() until the host asserts
        # DTR, so it never reaches the gated window unless we open its CDC port
        # -- done in capture_gated's on_started hook, after the GPI poller is
        # live. The dedicated power binary has no USB stack and skips this.
        dtr_holder: _UsbDtrHolder | None = None
        if effective_firmware == "shared" and ctx.config.target.transport == Transport.USB_CDC:
            dtr_holder = _UsbDtrHolder(
                usb_port=ctx.config.target.usb_port,
                usb_marker=usb_marker_serial(ctx.effective_jlink_serial),
            )

        # 3-wire lock-step: arm the host GO line first, then run the reset +
        # READY handshake inside capture_gated's ``on_started`` (invoked once
        # the poller thread samples GPI), so a firmware that raises its gate
        # early is never observed as already-high (evidence:
        # experiments/ap5-phase-d/t2-gate-race/):
        #   arm -> [poller live] -> reset -> wait READY -> (DTR) -> GO
        # The capture budget is unaffected (the driver's wait clock starts
        # after on_started returns). One try/finally so any exception still
        # releases sync.
        sync = _make_sync_controller(ctx, driver)
        probe = ctx.config.profiling.clean_window_probe
        relative_tolerance = gate_relative_tolerance_for(probe)
        longest_window_s = (
            longest_accepted_window_s(
                clean_infer_count=clean_count,
                clean_infer_avg_us=clean_avg_us,
                stats_rate_hz=ctx.config.power.stats_rate_hz,
                relative_tolerance=relative_tolerance,
            )
            if clean_count and clean_avg_us
            else None
        )
        # The firmware runs real warm-up inferences before the window for every
        # probe. A counted probe's reference is one inference; a busy_loop unit
        # is the whole spin, so its warm-up is priced from the profiled
        # per-inference cycles instead.
        warm_inference_s = (
            clean_avg_us / 1e6
            if clean_avg_us and probe_runs_inferences(probe)
            else _profiled_inference_s(ctx)
        )
        warmup_s = (
            max(CLEAN_WINDOW_WARMUP_REPS, ctx.config.profiling.warmup) * warm_inference_s
            if warm_inference_s
            else 0.0
        )
        fall_wait_s = gate_fall_wait_s(
            duration,
            longest_window_s=longest_window_s,
            lockstep=sync.lockstep,
            pre_window_s=warmup_s,
        )
        ready_wait_s = lockstep_ready_wait_s(duration, pre_window_s=warmup_s)
        if fall_wait_s > duration:
            if sync.lockstep:
                before_window = ""
            elif warmup_s:
                before_window = ", plus boot and warm-up before it without lock-step"
            else:
                before_window = ", plus boot before it without lock-step"
            log.log(
                logging.WARNING if ctx.config.power.duration_s is not None else logging.INFO,
                "Raising the capture bound from %.2fs to %.2fs to hold a gated window "
                "accepted up to %.2fs%s. power.duration_s bounds the capture; the "
                "profiling window settings set the window length.",
                duration,
                fall_wait_s,
                longest_window_s,
                before_window,
            )
        prepare_error: list[BaseException] = []
        try:
            sync.arm()
            # Filled inside the driver's start callback; a one-slot holder so
            # the typed object outlives it.
            sync_metadata_holder: list[SyncHandshakeMetadata] = []
            capture_phase = {"name": "poller_armed"}

            def _release(wait_gpi_state=None) -> None:
                try:
                    # Reset/relaunch only after GO is held low, the state
                    # input is open, and the GPI poller is live.
                    capture_phase["name"] = "resetting_target"
                    _prepare_target_once()

                    if sync.lockstep:
                        # GPIO levels during flash/reset are undefined protocol
                        # state. Discard them, then allow the target and JS320
                        # digital input path to settle before qualifying READY.
                        capture_phase["name"] = "reset_grace"
                        time.sleep(_LOCKSTEP_RESET_GRACE_S)
                        capture_phase["name"] = "waiting_ready"
                        ready_started = time.monotonic()
                        ready = (
                            wait_gpi_state(ctx.config.power.state_input_index, True, ready_wait_s)
                            if wait_gpi_state is not None
                            else sync.wait_ready(timeout_s=ready_wait_s)
                        )
                        ready_waited_s = round(time.monotonic() - ready_started, 6)
                        if not ready:
                            state = sync.read_state()
                            raise PowerError(
                                "Target did not signal READY before gated power capture",
                                hint=(
                                    "Check the state/go GPIO wiring, reset strategy, and "
                                    "that the firmware is parked in the power sync wait "
                                    f"state. Last observed state: {state.value}; waited "
                                    f"{ready_waited_s:.3f}s of a {ready_wait_s:.2f}s READY "
                                    "bound."
                                ),
                            )
                        sync_metadata_holder.append(
                            SyncHandshakeMetadata(
                                lockstep=True,
                                ready_wait_s=ready_waited_s,
                                ready_observed=True,
                            )
                        )
                    else:
                        sync_metadata_holder.append(SyncHandshakeMetadata(lockstep=False))
                    if dtr_holder is not None:
                        dtr_holder.open()
                    # Make the poller accept a fresh GATE edge before GO is
                    # released. A fast target can raise GATE inside
                    # signal_go(); updating the phase afterward would let the
                    # poller discard that edge while retaining prev_level=1.
                    capture_phase["name"] = "go_signaled"
                    sync.signal_go()
                except BaseException as exc:  # propagate through the driver thread boundary
                    prepare_error.append(exc)
                    raise

            result = driver.capture_gated(
                duration_s=fall_wait_s,
                io_voltage=ctx.config.power.io_voltage,
                sync_input_index=ctx.config.power.sync_input_index,
                state_input_index=ctx.config.power.state_input_index,
                stats_rate_hz=ctx.config.power.stats_rate_hz,
                clean_infer_count=clean_count,
                clean_infer_avg_us=clean_avg_us,
                minimum_gate_s=DEFAULT_POWER_MIN_WINDOW_MS / 1000.0,
                gate_relative_tolerance=relative_tolerance,
                work_noun=count_noun(ctx.config.profiling.clean_window_probe, clean_count or 0),
                on_started=_release,
                # The dedicated JS320 GPI stream provides the authoritative
                # gate edge. Release GO there to avoid backfeeding the target
                # during the measured window.
                on_gate_rise=sync.release_go,
                phase_getter=lambda: capture_phase["name"],
                # Lock-step facts for the gate-failure classifier: with
                # lock-step off on a board that IS wired for it, a missed gate
                # is far more likely the free-running window racing the poller
                # than a wiring fault (issue #114). ``sync.lockstep`` is the
                # runtime truth -- a driver with no GO output degrades to the
                # null controller even when the config resolved lock-step on.
                lockstep=sync.lockstep,
                lockstep_wiring_available=ctx.config.power.lockstep_wiring_available,
                pre_window_s=warmup_s,
            )
            if prepare_error:
                raise prepare_error[0]
            if result.metadata.sync is None and sync_metadata_holder:
                result.metadata.sync = sync_metadata_holder[-1]
            if result.metadata.capture_safety_bound_s is None:
                result.metadata.capture_safety_bound_s = fall_wait_s
            if result.metadata.power_plan is None:
                result.metadata.power_plan = {
                    "inference_count": plan.inference_count,
                    "reference_inference_us": plan.reference_inference_us,
                    "target_duration_ms": plan.target_duration_ms,
                    "count_source": plan.count_source,
                }
            return _attach_lifecycle_metadata(result)
        except Exception as exc:
            # A failed reset/READY/GO step outranks whatever the capture made
            # of the run it never started. Its own cause chain is kept.
            if prepare_error and exc is not prepare_error[0]:
                error = prepare_error[0]
                error.__suppress_context__ = True
                raise error
            raise
        finally:
            sync.release()
            if dtr_holder is not None:
                dtr_holder.close()

    _prepare_target_once()
    return _attach_lifecycle_metadata(
        driver.capture(duration_s=duration, io_voltage=ctx.config.power.io_voltage)
    )


_TRUNCATION_HINTS: dict[Transport, str] = {
    Transport.RTT: (
        "RTT capture switches to lossless blocking mode for CSV/HPX_END, so "
        "truncation here usually means the host stopped reading (J-Link "
        "detached, capture timed out, or the firmware hung). Check the "
        "J-Link connection and heartbeat/overall timeouts. If the run is "
        "genuinely long, raise target.heartbeat.overall_timeout_s. A larger "
        "--rtt-buffer-size-up reduces back-pressure stalls on big models."
    ),
    Transport.SWO: (
        "SWO/ITM has no flow control — its single-word FIFO silently drops "
        "data when the firmware prints faster than the ~1 Mbps SWO pin. "
        "For lossless capture use --transport rtt. If you must use SWO, "
        "reduce output volume (fewer --iterations or --pmu-counters)."
    ),
    Transport.UART: (
        "UART capture truncated. Check the J-Link VCOM connection and "
        "heartbeat/overall timeouts. UART has no flow control; reduce output "
        "volume (fewer --iterations or --pmu-counters), or use --transport rtt."
    ),
    Transport.USB_CDC: (
        "USB CDC capture truncated. Confirm the board's application USB "
        "device enumerated after reset (a separate CDC port from the "
        "J-Link), the cable is data-capable, and the host had time to open "
        "the port. RTT (--transport rtt) avoids USB enumeration entirely."
    ),
}


def _truncation_hint(transport: str) -> str:
    """Return a transport-specific hint for truncated / empty captures.

    Each transport fails differently when the firmware output does not reach
    the host intact, so point the user at the most likely cause and fix.
    """
    try:
        return _TRUNCATION_HINTS[Transport(transport)]
    except (ValueError, KeyError):
        return "Check that the firmware is printing HPX protocol data over the selected transport."


def _verify_device_clock(ctx: PipelineContext, result: PmuResult) -> None:
    """Warn if the device's clock disagrees with the registry value.

    The host derives SWO baud and every cycle->time conversion from the
    ``target.clock.cpu`` selection resolved against the platform registry.
    Two device readings can contradict it: ``HPX_SYSTEM_CLOCK_HZ`` (the
    firmware's ``SystemCoreClock``, which only differs when Apollo3 burst
    fails to engage) and ``HPX_MEASURED_CLOCK_HZ`` (DWT cycles over a STIMER
    interval, which catches a perf mode that silently did not apply).  A
    mismatch does not abort the run (the cycle counts themselves are still
    valid), but it makes every derived time value suspect.
    """
    platform = ctx.run_metadata.platform
    if platform is None:
        return
    registry_mhz = platform.cpu_clock_mhz
    if registry_mhz <= 0:
        return
    registry_hz = registry_mhz * 1_000_000
    readings = (
        ("Device reports", result.meta.system_clock_hz),
        ("Measured", result.meta.measured_clock_hz),
    )
    if result.meta.measured_clock_hz == 0:
        log.warning(
            "The on-device clock probe returned 0 Hz (DWT or STIMER did not "
            "count), so the %d MHz core clock for %s is unverified.",
            registry_mhz,
            platform.soc or "this SoC",
        )
    for label, device_hz in readings:
        if not device_hz or abs(device_hz - registry_hz) <= DEVICE_CLOCK_TOLERANCE * registry_hz:
            continue
        log.warning(
            "%s CPU clock %.3f MHz but the platform registry "
            "assumed %d MHz (cpu=%s) for %s. SWO baud and all cycle->time "
            "values use the registry value and will be wrong. Check that "
            "the perf mode took effect, or fix the clock for %s in the "
            "platform registry or the target.clock.cpu setting.",
            label,
            device_hz / 1_000_000,
            registry_mhz,
            platform.cpu_clock_name or "?",
            platform.soc or "?",
            platform.soc or "this SoC",
        )
