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
from typing import TYPE_CHECKING

from ..errors import CaptureError
from ..placement import Placement
from ..usb_identity import usb_marker_serial
from .transport import HPX_END, HPX_START

if TYPE_CHECKING:
    from ..pipeline import PipelineContext
    from ..power.base import PowerResult
    from ..results import PmuResult

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

    # Apollo4 gates DWT->CYCCNT behind the debug power domain, which only stays
    # powered while a debugger is attached.  UART/USB normally release the probe
    # after reset, so on those SoCs the readers must hold a pylink session open
    # for the whole capture or every per-layer cycle reads back 0.
    keep_debugger_attached = ctx.soc.requires_attached_probe_for_cycles

    # Use build_dir from context (set by stage 4) — no re-derivation
    build_dir = ctx.build_dir
    timing_raw: dict[str, float] = {}

    if transport == "usb_cdc":
        from .usb_reader import capture_usb_output

        lines = capture_usb_output(
            jlink_serial=jlink_serial,
            jlink_device=jlink_device,
            usb_port=ctx.config.target.usb_port,
            usb_marker=usb_marker_serial(jlink_serial),
            keep_attached=keep_debugger_attached,
            timing_out=timing_raw,
        )
    elif transport == "rtt":
        from .rtt_reader import capture_rtt_output
        from .rtt_symbol import resolve_rtt_control_block_address

        # Recover the linked RTT control block address from the build artifacts
        # so capture can attach directly and skip the slow SWD discovery sweep.
        known_block_address = resolve_rtt_control_block_address(
            build_dir, ctx.config.target.toolchain
        )
        if known_block_address is not None:
            log.info(
                "Using known RTT control block address 0x%08X (skipping host-side scan)",
                known_block_address,
            )

        lines = capture_rtt_output(
            jlink_serial=jlink_serial,
            jlink_device=jlink_device,
            rtt_scan_ranges=ctx.soc.rtt_scan_ranges,
            known_block_address=known_block_address,
            model_path=ctx.config.model.path,
            weights_region=ctx.weights_region or Placement.MRAM,
            timeout_s=overall_timeout_s,
            heartbeat_timeout_s=heartbeat_timeout_s,
            timing_out=timing_raw,
        )
    else:
        from .serial_reader import capture_swo_output

        if transport == "uart":
            from .uart_reader import capture_uart_output

            lines = capture_uart_output(
                jlink_serial=jlink_serial,
                jlink_device=jlink_device,
                timeout_s=overall_timeout_s,
                heartbeat_timeout_s=heartbeat_timeout_s,
                keep_attached=keep_debugger_attached,
                timing_out=timing_raw,
            )
        else:
            cpu_clock_mhz = ctx.run_metadata.platform.cpu_clock_mhz
            cpu_freq_hz = cpu_clock_mhz * 1_000_000 if cpu_clock_mhz > 0 else 96_000_000

            lines = capture_swo_output(
            build_dir=build_dir,
            jlink_serial=jlink_serial,
            jlink_device=jlink_device,
            cpu_freq=cpu_freq_hz,
            timing_out=timing_raw,
        )
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

    # Pre-parse validation: check for protocol sentinels
    if not any(HPX_START in l for l in lines[:30]):
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

    result = parse_firmware_output(lines)
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

    if timing_raw:
        from ..results import TimingInfo

        ctx.run_metadata.timing = TimingInfo(
            capture_duration_s=timing_raw.get("capture_duration_s"),
            hpx_start_latency_s=timing_raw.get("hpx_start_latency_s"),
            protocol_duration_s=timing_raw.get("protocol_duration_s"),
        )

    return result


def capture_power(ctx: PipelineContext, *, duration_override_s: float | None = None) -> PowerResult:
    """Record a power trace using the configured power driver.

    Returns a :class:`PowerResult` directly — no intermediate dict wrapping.
    """
    from ..power import get_driver

    driver_name = ctx.config.power.driver
    driver = get_driver(driver_name)

    # Verify driver is usable
    driver.check_available()

    duration = (
        duration_override_s if duration_override_s is not None else ctx.config.power.duration_s
    )

    return driver.capture(
        duration_s=duration,
        io_voltage=ctx.config.power.io_voltage,
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


def _truncation_hint(transport: str) -> str:
    """Return a transport-specific hint for truncated / empty captures.

    Each transport fails differently when the firmware output does not reach
    the host intact, so point the user at the most likely cause and fix.
    """
    if transport == "rtt":
        return (
            "RTT capture switches to lossless blocking mode for CSV/HPX_END, so "
            "truncation here usually means the host stopped reading (J-Link "
            "detached, capture timed out, or the firmware hung). Check the "
            "J-Link connection and heartbeat/overall timeouts. If the run is "
            "genuinely long, raise target.heartbeat.overall_timeout_s. A larger "
            "--rtt-buffer-size-up reduces back-pressure stalls on big models."
        )
    if transport == "swo":
        return (
            "SWO/ITM has no flow control — its single-word FIFO silently drops "
            "data when the firmware prints faster than the ~1 Mbps SWO pin. "
            "For lossless capture use --transport rtt. If you must use SWO, "
            "reduce output volume (fewer --iterations or --pmu-counters)."
        )
    if transport == "usb_cdc":
        return (
            "USB CDC capture truncated. Confirm the board's application USB "
            "device enumerated after reset (a separate CDC port from the "
            "J-Link), the cable is data-capable, and the host had time to open "
            "the port. RTT (--transport rtt) avoids USB enumeration entirely."
        )
    return "Check that the firmware is printing HPX protocol data over the selected transport."


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
