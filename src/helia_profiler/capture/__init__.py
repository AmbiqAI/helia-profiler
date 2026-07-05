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

from ..config import Transport
from ..errors import CaptureError, PowerError
from ..transport import (
    HPX_END,
    HPX_START,
    CaptureArgs,
    resolve_transport,
)
from ..usb_identity import usb_marker_serial

if TYPE_CHECKING:
    from ..pipeline import PipelineContext
    from ..power.base import PowerResult
    from ..results import PmuResult
    from ..target.lifecycle import TargetLifecyclePlan

log = logging.getLogger("hpx")


def capture_pmu(ctx: PipelineContext) -> PmuResult:
    """Read PMU data from the target via serial port.

    Returns a :class:`PmuResult` with firmware metadata, per-preset breakdowns,
    and merged per-layer results.
    """
    from .parser import parse_firmware_output

    transport = ctx.config.target.transport

    jlink_serial = ctx.resolved_jlink_serial or ctx.config.target.jlink_serial
    hb = ctx.config.target.heartbeat
    heartbeat_timeout_s = hb.host_timeout_s if hb.enabled else 300
    overall_timeout_s = hb.overall_timeout_s

    # Resolve J-Link device string from the SoC registry — hard error if missing
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
    backend.start(ctx)
    try:
        lines = backend.collect(ctx)
    finally:
        backend.close()
    if not lines:
        raise CaptureError(
            f"No data captured via {transport} transport",
            hint="Ensure the firmware is running. Try resetting the board.",
        )

    # --- Firmware error triage --------------------------------------------
    # Scan for HPX_ERROR= lines before parsing.  A firmware-reported error
    # is more specific than any "no layer data" fallback message, so surface
    # it with the best hint we can generate.
    _raise_on_firmware_error(lines)

    # Pre-parse validation: check for protocol sentinels.  Scan the whole
    # capture, not just the head: the SWO transport emits a variable-length
    # HPX_READY sync preamble before "--- HPX_START ---" (see the firmware
    # templates), so the sentinel does not sit at a fixed offset.  The parser
    # likewise ignores everything before HPX_START.
    if not any(HPX_START in l for l in lines):
        raise CaptureError(
            f"Captured data ({len(lines)} lines) does not contain HPX_START sentinel",
            hint=(
                "The firmware may not be running the profiler app, or the "
                "transport connection failed before data arrived."
            ),
        )
    saw_end = any(l.strip() == HPX_END for l in lines[-10:])
    if not saw_end:
        log.warning(
            "HPX_END sentinel not found in captured data (%d lines) — capture "
            "was truncated before the firmware finished. %s",
            len(lines),
            _truncation_hint(str(transport)),
        )

    result = parse_firmware_output(lines, aggregation=ctx.config.profiling.aggregation)
    if not result.layers:
        # We saw HPX_START (checked above) but parsed zero layers.  Either the
        # CSV stream was lost in transit (lossy transport / undersized buffer)
        # or the run was cut short before any iteration completed.
        detail = (
            "the stream was truncated before any CSV data arrived"
            if not saw_end
            else "the firmware emitted HPX_END but no parseable CSV rows"
        )
        raise CaptureError(
            f"No layer data parsed from firmware output ({len(lines)} lines, {detail}).",
            hint=_truncation_hint(str(transport)),
        )

    # Cross-check the device's actual clock against the registry value the host
    # assumed.  This catches registry drift or an NSX perf-mode that silently
    # failed to apply — both of which corrupt SWO baud and cycle->time math.
    _verify_device_clock(ctx, result)

    if timing_raw:
        from ..results import TimingInfo

        # Collect the attributed boot/attach phase breakdown (RTT records
        # reset / sbl_settle / attach / control_block_scan / line_collection).
        phases = {
            key[len("rtt_phase_"): -len("_s")]: round(value, 6)
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
        self._ser = None  # type: ignore[var-annotated]

    def open(self) -> None:
        import serial

        from ..transport.usb_cdc import _BAUD, _resolve_cdc_port

        port = self._usb_port
        if port is None:
            port = _resolve_cdc_port(marker=self._usb_marker)
        log.info("Opening USB CDC port for gated power capture: %s", port)
        self._ser = serial.Serial(
            port=port,
            baudrate=_BAUD,
            timeout=1.0,
            dsrdtr=True,  # assert DTR so nsx_usb_connected() returns true
        )
        self._ser.dtr = True

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                log.debug("Failed to close USB DTR holder port", exc_info=True)
            finally:
                self._ser = None


def _make_sync_controller(ctx: PipelineContext, driver: object):
    """Build a host sync controller from config, or a gate-only fallback.

    Lock-step is opt-in (``power.lockstep``); without it, or on drivers that
    cannot drive a GO output, the controller is a no-op and the device free-runs.
    """
    from ..power.sync import NullSyncController, SyncWiring

    if not ctx.config.power.lockstep or not hasattr(driver, "make_sync_controller"):
        return NullSyncController()
    wiring = SyncWiring(
        lockstep=True,
        gate_input_index=ctx.config.power.sync_input_index,
        state_input_index=ctx.config.power.state_input_index,
        go_output_index=ctx.config.power.go_output_index,
    )
    return driver.make_sync_controller(wiring)


def capture_power(
    ctx: PipelineContext,
    *,
    duration_override_s: float | None = None,
    prepare_target: Callable[[object, str], "TargetLifecyclePlan"] | None = None,
) -> PowerResult:
    """Record a power trace using the configured power driver.

    Returns a :class:`PowerResult` directly — no intermediate dict wrapping.
    """
    from ..power import GATED_CAPTURE_DRIVER_NAMES, get_driver

    driver_name = ctx.config.power.driver
    driver = get_driver(driver_name, serial=ctx.config.power.serial)

    # Verify driver is usable
    driver.check_available()
    lifecycle_plan = None

    def _prepare_target_once() -> None:
        nonlocal lifecycle_plan
        if prepare_target is not None and lifecycle_plan is None:
            lifecycle_plan = prepare_target(driver, driver_name)

    def _attach_lifecycle_metadata(result: PowerResult) -> PowerResult:
        if lifecycle_plan is not None:
            result.metadata.setdefault("target_lifecycle", lifecycle_plan.to_metadata())
        return result

    duration = (
        duration_override_s if duration_override_s is not None else ctx.config.power.duration_s
    )

    clean_count = None
    if ctx.pmu_result is not None:
        clean_count = ctx.pmu_result.meta.clean_infer_count

    if driver_name in GATED_CAPTURE_DRIVER_NAMES and clean_count is not None:
        # USB CDC firmware blocks in nsx_usb_connected() until the host asserts
        # DTR.  Unlike SWO/UART/RTT (which free-run after reset), it will never
        # reach the gated clean window — and the Joulescope would see no
        # GPIO-high window — unless we open its CDC port.  Hand capture_gated an
        # on_started hook that opens the port *after* the GPI poller is live, so
        # the firmware is released only once we are watching for the window.
        dtr_holder: _UsbDtrHolder | None = None
        if ctx.config.target.transport == Transport.USB_CDC:
            jlink_serial = ctx.resolved_jlink_serial or ctx.config.target.jlink_serial
            dtr_holder = _UsbDtrHolder(
                usb_port=ctx.config.target.usb_port,
                usb_marker=usb_marker_serial(jlink_serial),
            )
        # 3-wire lock-step: arm the host GO line before the device may run and
        # release it once the poller is live, chained after any USB DTR open.
        # The whole arm -> prepare -> wait_ready -> capture_gated sequence is
        # one try/finally so that any exception raised anywhere in it (e.g.
        # ``_prepare_target_once`` failing after GO has been driven low)
        # still unconditionally releases the sync controller.
        sync = _make_sync_controller(ctx, driver)
        try:
            sync.arm()
            # Reset/relaunch only after GO is held low and the state input is
            # open. Otherwise a fast boot can pass through the READY barrier
            # before the host is watching, leaving power capture to fail
            # later as a missing gate.
            _prepare_target_once()
            sync_metadata: dict[str, object]

            if sync.lockstep:
                from ..power.diagnostics import SyncHandshakeMetadata

                ready_started = time.monotonic()
                ready = sync.wait_ready(timeout_s=duration)
                ready_wait_s = round(time.monotonic() - ready_started, 6)
                if not ready:
                    state = sync.read_state()
                    raise PowerError(
                        "Target did not signal READY before gated power capture",
                        hint=(
                            "Check the state/go GPIO wiring, reset strategy, and that the "
                            "firmware is parked in the power sync wait state. "
                            f"Last observed state: {state.value}; waited {ready_wait_s:.3f}s."
                        ),
                    )
                sync_metadata = SyncHandshakeMetadata(
                    lockstep=True,
                    ready_wait_s=ready_wait_s,
                    ready_observed=True,
                ).to_metadata()
            else:
                from ..power.diagnostics import SyncHandshakeMetadata

                sync_metadata = SyncHandshakeMetadata(lockstep=False).to_metadata()

            def _release() -> None:
                if dtr_holder is not None:
                    dtr_holder.open()
                sync.signal_go()

            result = driver.capture_gated(
                duration_s=duration,
                io_voltage=ctx.config.power.io_voltage,
                sync_input_index=ctx.config.power.sync_input_index,
                stats_rate_hz=ctx.config.power.stats_rate_hz,
                clean_infer_count=clean_count,
                on_started=_release,
            )
            result.metadata.setdefault("sync", sync_metadata)
            return _attach_lifecycle_metadata(result)
        finally:
            sync.release()
            if dtr_holder is not None:
                dtr_holder.close()

    _prepare_target_once()
    return _attach_lifecycle_metadata(
        driver.capture(duration_s=duration, io_voltage=ctx.config.power.io_voltage)
    )


# ---------------------------------------------------------------------------
# Firmware error classifier
# ---------------------------------------------------------------------------

# Maps the short ``HPX_ERROR=<kind>`` token to a human-readable hint.  The
# firmware emits these after its own preflight checks so the host can point
# the user at the real cause instead of blaming the arena for every failure.
_ERROR_HINTS: dict[str, str] = {
    "schema_mismatch": (
        "The model's schema version does not match what the firmware was "
        "built for.  Re-export the model with a matching TFLite version."
    ),
    "unsupported_op": (
        "The model uses an operator the firmware resolver did not register.  "
        "Add the missing op to the resolver (firmware/templates/main.cc.j2 "
        "get_resolver()) or re-export the model without that op."
    ),
    "missing_ops": (
        "One or more operators in the model are not registered in the "
        "MicroMutableOpResolver.  See the preceding HPX_ERROR=unsupported_op "
        "lines for the specific ops."
    ),
    "alloc_tensors_failed": (
        "TFLM AllocateTensors() failed.  Likely causes, in order of "
        "probability: (1) the arena is too small — increase --arena-size; "
        "(2) a kernel's Prepare() rejected an op (shape/dtype/parameter "
        "mismatch not caught by preflight).  The firmware reports the "
        "configured arena size in the error line."
    ),
    "model_init_failed": (
        "heliaAOT model init returned a non-zero status.  Check that the "
        "generated module was built against the correct board and that any "
        "required memories (PSRAM, SHARED_SRAM) are initialised."
    ),
    "psram_init_failed": (
        "PSRAM initialisation failed on the target.  Verify the board "
        "actually has PSRAM populated and that --model-location=psram is "
        "appropriate for this hardware."
    ),
}


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
    except ValueError:
        return "Check that the firmware is printing HPX protocol data over the selected transport."


def _verify_device_clock(ctx: PipelineContext, result: PmuResult) -> None:
    """Warn if the device's actual clock disagrees with the registry value.

    The host derives SWO baud and every cycle->time conversion from the
    ``target.clock.cpu`` selection resolved against the platform registry.
    The firmware reports its real ``SystemCoreClock`` so we can detect when
    that assumption is wrong — e.g. a stale registry entry or an NSX perf-mode
    that did not take effect on this SoC.  A mismatch does not abort the run
    (the cycle counts themselves are still valid), but it makes every derived
    time value suspect, so surface it loudly.
    """
    platform = ctx.run_metadata.platform
    if platform is None:
        return
    device_hz = result.meta.system_clock_hz
    registry_mhz = platform.cpu_clock_mhz
    if not device_hz or registry_mhz <= 0:
        return

    registry_hz = registry_mhz * 1_000_000
    # HFRC trim tolerance is a few percent; 5% comfortably clears real trim
    # variation while still catching integer-ratio mistakes (48 vs 96 MHz).
    if abs(device_hz - registry_hz) > 0.05 * registry_hz:
        log.warning(
            "Device reports CPU clock %.3f MHz but the platform registry "
            "assumed %d MHz (cpu=%s) for %s. SWO baud and all cycle->time "
            "values use the registry value and will be wrong. Fix the clock "
            "for %s in the platform registry or the target.clock.cpu setting.",
            device_hz / 1_000_000,
            registry_mhz,
            platform.cpu_clock_name or "?",
            platform.soc or "?",
            platform.soc or "this SoC",
        )


def _raise_on_firmware_error(lines: list[str]) -> None:
    """Raise :class:`CaptureError` if the firmware reported an HPX_ERROR.

    Finds the first ``HPX_ERROR=<kind> ...`` line, extracts the kind and
    the full payload, looks up a hint, and raises.  Unknown kinds still
    raise — with a generic hint — so nothing slips through silently.
    """
    for line in lines:
        s = line.strip()
        if not s.startswith("HPX_ERROR="):
            continue

        payload = s[len("HPX_ERROR=") :]
        # Kind is the first token up to a space or ':'.  e.g.
        #   "unsupported_op kind=builtin ..."
        #   "schema_mismatch:1234_vs_3"
        kind = payload
        for sep in (" ", ":"):
            if sep in kind:
                kind = kind.split(sep, 1)[0]
                break

        hint = _ERROR_HINTS.get(
            kind,
            "Firmware reported an error.  The payload is shown above.",
        )
        raise CaptureError(
            f"Firmware error: {s}",
            hint=hint,
        )
