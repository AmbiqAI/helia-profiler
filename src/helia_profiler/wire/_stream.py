"""Specs for the transport stream: sentinels, handshake, keys, heartbeats."""

from __future__ import annotations

from ..engines import EngineType
from ._model import (
    AOT_ENGINES,
    EST_MS_GAP,
    ET_ENGINES,
    GATE_AOT_CONST_BLOBS,
    GATE_AOT_EXTERNAL_ARENAS,
    GATE_AOT_PSRAM_ARENAS,
    GATE_AOT_PSRAM_METADATA,
    GATE_APOLLO3_BURST,
    GATE_ARENA_IN_PSRAM,
    GATE_ATTACH_WAIT,
    GATE_BUSY_LOOP_PROBE,
    GATE_CLEAN_WINDOW_TRACE,
    GATE_NOT_POWER_ONLY,
    GATE_NOT_STIMER_WINDOW,
    GATE_POWER_ONLY,
    GATE_PSRAM_METADATA,
    GATE_PSRAM_WEIGHTS_UPLOAD,
    GATE_RTT_FLUSH,
    GATE_TRANSPORT_HAS_READY_PREAMBLE,
    GATE_WEIGHTS_IN_PSRAM,
    HPX_END_SENTINEL,
    HPX_GO_COMMAND,
    HPX_READY_LINE,
    HPX_START_SENTINEL,
    POWER_BINARY_ENGINES,
    POWER_TERMINAL_END_SENTINEL,
    POWER_TERMINAL_START_SENTINEL,
    TFLM_ENGINES,
    HeartbeatPhase,
    WireBinary,
    WireConsumer,
    WireCriticality,
    WireDirection,
    WireKey,
    WireKind,
    WireSpec,
    _spec,
    heartbeat_token,
)

# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

SENTINEL_SPECS: tuple[WireSpec, ...] = (
    _spec(
        "HPX_START",
        WireKind.SENTINEL,
        "Opens the profile stream; the parser ignores every line before it.",
        WireConsumer.TRANSPORT_CONTROL,
        WireCriticality.PROTOCOL,
        condition=GATE_NOT_POWER_ONLY,
        literal=HPX_START_SENTINEL,
        note="Absence is a hard CaptureError; its arrival time is the "
        "TimingInfo.hpx_start_latency_s reference for every transport.",
    ),
    _spec(
        "HPX_END",
        WireKind.SENTINEL,
        "Closes the profile stream and ends line collection.",
        WireConsumer.TRANSPORT_CONTROL,
        WireCriticality.PROTOCOL,
        condition=GATE_NOT_POWER_ONLY,
        literal=HPX_END_SENTINEL,
        note="Missing END within the last 10 lines is a truncation warning, "
        "not an error — a lossy transport may drop it after valid data.",
    ),
    _spec(
        "HPX_PRESET",
        WireKind.SENTINEL,
        "Opens one PMU pass; the name selects the preset the rows belong to.",
        WireConsumer.TRANSPORT_CONTROL,
        WireCriticality.PROTOCOL,
        condition=GATE_NOT_POWER_ONLY,
        literal="--- HPX_PRESET <name> ---",
        value_shape="pass name (no spaces)",
    ),
    _spec(
        "HPX_ITER",
        WireKind.SENTINEL,
        "Opens one profiled iteration; the CSV body follows.",
        WireConsumer.TRANSPORT_CONTROL,
        WireCriticality.PROTOCOL,
        condition=GATE_NOT_POWER_ONLY,
        literal="--- HPX_ITER <n> ---",
        value_shape="decimal iteration index",
        note="A stream with iterations but no preset marker is legacy; the "
        "parser auto-creates a '_default' preset for it.",
    ),
    _spec(
        "HPX_POWER_TERMINAL_START",
        WireKind.SENTINEL,
        "Opens the power binary's terminal record.",
        WireConsumer.POWER_TERMINAL,
        WireCriticality.PROTOCOL,
        engines=POWER_BINARY_ENGINES,
        binary=WireBinary.POWER,
        condition=GATE_POWER_ONLY,
        literal=POWER_TERMINAL_START_SENTINEL,
        note="Delivery differs by transport: on RTT the whole record is "
        "written once and the firmware parks, while UART, SWO and USB CDC "
        "retransmit it in full every 250 ms forever (the binary never "
        "terminates and the host may attach late). The envelope parser's "
        "find-a-complete-start/end-pair-and-discard-partials loop exists for "
        "exactly that repetition.",
    ),
    _spec(
        "HPX_POWER_TERMINAL_END",
        WireKind.SENTINEL,
        "Closes the power binary's terminal record.",
        WireConsumer.POWER_TERMINAL,
        WireCriticality.PROTOCOL,
        engines=POWER_BINARY_ENGINES,
        binary=WireBinary.POWER,
        condition=GATE_POWER_ONLY,
        literal=POWER_TERMINAL_END_SENTINEL,
    ),
)


HANDSHAKE_SPECS: tuple[WireSpec, ...] = (
    _spec(
        "HPX_READY",
        WireKind.HANDSHAKE,
        "Firmware liveness line printed before the start header.",
        WireConsumer.TRANSPORT_CONTROL,
        WireCriticality.PROTOCOL,
        condition=GATE_TRANSPORT_HAS_READY_PREAMBLE,
        literal=HPX_READY_LINE,
        note="RTT prints it once and uses it as the attach gate; SWO/UART "
        "print 40 disposable copies to keep the link warm while the host "
        "attaches; USB CDC prints none and polls DTR instead.",
    ),
    _spec(
        "HPX_GO",
        WireKind.HANDSHAKE,
        "Host->device release after the model is written into PSRAM.",
        WireConsumer.TRANSPORT_CONTROL,
        WireCriticality.PROTOCOL,
        engines=TFLM_ENGINES,
        direction=WireDirection.HOST_TO_DEVICE,
        condition=GATE_PSRAM_WEIGHTS_UPLOAD,
        literal=HPX_GO_COMMAND,
        emitted_by_firmware=False,
        note="The firmware never compares the bytes — it waits for six "
        "characters on RTT down-channel 0 — so the token appears in the "
        "rendered source only as a comment.",
    ),
)


START_HEADER_SPECS: tuple[WireSpec, ...] = (
    _spec(
        WireKey.VERSION.wire,
        WireKind.KEY_VALUE,
        "Protocol version of the emitting firmware.",
        WireConsumer.DIAGNOSTIC,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.VERSION,
        condition=GATE_NOT_POWER_ONLY,
        value_shape="int (currently 1)",
        note="Compared against HPX_PROTOCOL_VERSION and then discarded: a "
        "mismatch logs a warning and never reaches summary.json.",
    ),
    _spec(
        WireKey.ENGINE.wire,
        WireKind.KEY_VALUE,
        "Which inference engine produced this build.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.ENGINE,
        condition=GATE_NOT_POWER_ONLY,
        value_shape="engine id with hyphens underscored (helia_aot)",
    ),
    _spec(
        WireKey.EXTREME_MODE.wire,
        WireKind.KEY_VALUE,
        "Whether the extreme low-power mode was actually engaged.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.EXTREME_MODE,
        condition=GATE_NOT_POWER_ONLY,
        value_shape="0 | 1",
        note="Resolved at render time: requires arena and weights both in TCM.",
    ),
    _spec(
        WireKey.ITERATIONS.wire,
        WireKind.KEY_VALUE,
        "Profiled iterations per pass, as compiled in.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.ITERATIONS,
        condition=GATE_NOT_POWER_ONLY,
        value_shape="int (Jinja literal)",
    ),
    _spec(
        WireKey.WARMUP.wire,
        WireKind.KEY_VALUE,
        "Warmup iterations per pass, as compiled in.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.WARMUP,
        condition=GATE_NOT_POWER_ONLY,
        value_shape="int (Jinja literal)",
    ),
    _spec(
        WireKey.NUM_PRESETS.wire,
        WireKind.KEY_VALUE,
        "Number of PMU passes this build runs.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.NUM_PRESETS,
        condition=GATE_NOT_POWER_ONLY,
        value_shape="int",
    ),
    _spec(
        WireKey.PRESETS.wire,
        WireKind.KEY_VALUE,
        "Names of the PMU passes, in execution order.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.PRESETS,
        condition=GATE_NOT_POWER_ONLY,
        value_shape="comma-separated names",
    ),
    _spec(
        WireKey.POWER_SYNC.wire,
        WireKind.KEY_VALUE,
        "Whether the build brackets its clean window with the GPIO gate.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.POWER_SYNC,
        condition=GATE_NOT_POWER_ONLY,
        value_shape="gpio | none",
    ),
    _spec(
        WireKey.SYNC_GPIO.wire,
        WireKind.KEY_VALUE,
        "Pin number carrying the power gate signal.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.SYNC_GPIO,
        condition=GATE_NOT_POWER_ONLY,
        runtime_gate="if constexpr (kPowerSyncEnabled)",
        value_shape="int pin number",
    ),
    _spec(
        WireKey.SYSTEM_CLOCK_HZ.wire,
        WireKind.KEY_VALUE,
        "Ground-truth SystemCoreClock as configured on the device.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.METRIC,
        key=WireKey.SYSTEM_CLOCK_HZ,
        value_shape="Hz",
        note="Checked against the platform registry (>5% divergence warns) "
        "and used by the clean-window clock-rate validity check: it is the "
        "expected-rate term of PROFILE_CLEAN_WINDOW_CLOCK_RATE_LOW, with "
        "HPX_CLEAN_DWT_RATE_CYC and HPX_CLEAN_DWT_RATE_US.",
    ),
    _spec(
        WireKey.BURST_AVAIL.wire,
        WireKind.KEY_VALUE,
        "Whether Apollo3 burst mode was available on this part.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.BURST_AVAIL,
        condition=GATE_APOLLO3_BURST,
        value_shape="0 | 1",
    ),
    _spec(
        WireKey.BURST_ENGAGED.wire,
        WireKind.KEY_VALUE,
        "Whether Apollo3 burst mode was actually engaged.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.BURST_ENGAGED,
        condition=GATE_APOLLO3_BURST,
        value_shape="0 | 1",
    ),
    _spec(
        WireKey.HEARTBEAT_ENABLED.wire,
        WireKind.KEY_VALUE,
        "Whether progress heartbeats are compiled in.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.HEARTBEAT_ENABLED,
        condition=GATE_NOT_POWER_ONLY,
        value_shape="0 | 1",
    ),
    _spec(
        WireKey.HEARTBEAT_EVERY_N_OPS.wire,
        WireKind.KEY_VALUE,
        "Heartbeat cadence in operators.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.HEARTBEAT_EVERY_N_OPS,
        condition=GATE_NOT_POWER_ONLY,
        value_shape="int (0 = disabled)",
    ),
    _spec(
        WireKey.HEARTBEAT_EVERY_MS.wire,
        WireKind.KEY_VALUE,
        "Heartbeat cadence in milliseconds.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.HEARTBEAT_EVERY_MS,
        condition=GATE_NOT_POWER_ONLY,
        value_shape="int ms (0 = disabled)",
    ),
)


MODEL_MEMORY_SPECS: tuple[WireSpec, ...] = (
    _spec(
        WireKey.MODEL_SIZE.wire,
        WireKind.KEY_VALUE,
        "Size of the embedded model blob.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.MODEL_SIZE,
        engines=TFLM_ENGINES | ET_ENGINES,
        condition=GATE_NOT_POWER_ONLY,
        value_shape="bytes",
        note="heliaAOT compiles its weights in and reports no model size.",
    ),
    _spec(
        WireKey.ARENA_SIZE.wire,
        WireKind.KEY_VALUE,
        "Configured arena size.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.ARENA_SIZE,
        engines=TFLM_ENGINES | ET_ENGINES,
        condition=GATE_NOT_POWER_ONLY,
        value_shape="bytes",
        note="For ExecuTorch this is the summed arena size — planned + "
        "method + temporary; I/O buffers are separate keys — so the figure "
        "is comparable with TFLM's single-arena number (#165); the per-arena "
        "breakdown stays in the host's build record. heliaAOT reports "
        "HPX_ARENAS_BOUND instead.",
    ),
    _spec(
        WireKey.ALLOCATED_ARENA.wire,
        WireKind.KEY_VALUE,
        "Arena bytes TFLM actually used after AllocateTensors().",
        WireConsumer.FIRMWARE_META,
        WireCriticality.METRIC,
        key=WireKey.ALLOCATED_ARENA,
        engines=TFLM_ENGINES,
        value_shape="bytes",
    ),
    _spec(
        WireKey.INPUT_SIZE.wire,
        WireKind.KEY_VALUE,
        "Byte size of the model's (first) input tensor.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.INPUT_SIZE,
        engines=TFLM_ENGINES | ET_ENGINES,
        engine_conditions={EngineType.EXECUTORCH: GATE_NOT_POWER_ONLY},
        value_shape="bytes",
    ),
    _spec(
        WireKey.OUTPUT_SIZE.wire,
        WireKind.KEY_VALUE,
        "Byte size of the model's (first) output tensor.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.OUTPUT_SIZE,
        engines=TFLM_ENGINES | ET_ENGINES,
        engine_conditions={EngineType.EXECUTORCH: GATE_NOT_POWER_ONLY},
        value_shape="bytes",
    ),
    _spec(
        WireKey.NUM_TENSORS.wire,
        WireKind.KEY_VALUE,
        "Tensor count of subgraph 0.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.NUM_TENSORS,
        engines=TFLM_ENGINES,
        value_shape="int",
    ),
    _spec(
        WireKey.NUM_INPUTS.wire,
        WireKind.KEY_VALUE,
        "Number of model inputs.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.NUM_INPUTS,
        engines=TFLM_ENGINES | AOT_ENGINES,
        value_shape="int",
    ),
    _spec(
        WireKey.NUM_OUTPUTS.wire,
        WireKind.KEY_VALUE,
        "Number of model outputs.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.NUM_OUTPUTS,
        engines=TFLM_ENGINES | AOT_ENGINES,
        value_shape="int",
    ),
    _spec(
        WireKey.INPUT_INDEXED_SIZE.wire,
        WireKind.KEY_VALUE_INDEXED,
        "Byte size of input <i>, one line per input.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.INPUT_INDEXED_SIZE,
        engines=AOT_ENGINES,
        value_shape="bytes",
        note="A genuine token-shape divergence: heliaAOT reports per-index "
        "sizes where TFLM and ExecuTorch report one static HPX_INPUT_SIZE. "
        "The parser stores input_0_size, input_1_size, ... and drops them.",
    ),
    _spec(
        WireKey.OUTPUT_INDEXED_SIZE.wire,
        WireKind.KEY_VALUE_INDEXED,
        "Byte size of output <i>, one line per output.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.OUTPUT_INDEXED_SIZE,
        engines=AOT_ENGINES,
        value_shape="bytes",
    ),
    _spec(
        WireKey.ARENAS_BOUND.wire,
        WireKind.KEY_VALUE,
        "Number of external arena regions bound before model init.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.ARENAS_BOUND,
        engines=AOT_ENGINES,
        condition=GATE_AOT_EXTERNAL_ARENAS,
        value_shape="int",
    ),
    _spec(
        "HPX_CONST_BLOB_LOADED",
        WireKind.RECORD,
        "One constant sidecar blob was copied into its bound arena.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        engines=AOT_ENGINES,
        condition=GATE_AOT_CONST_BLOBS,
        value_shape="region=<id> size=<bytes>",
        note="Space-separated, so the generic key/value regex never matches "
        "it: this line reaches no host consumer at all.",
    ),
)


_PSRAM_METADATA_KEYS: tuple[tuple[WireKey, str, WireCriticality, str], ...] = (
    (WireKey.PSRAM_SIZE_BYTES, "Total PSRAM size reported by the driver.",
     WireCriticality.DIAGNOSTIC, "bytes"),
    (WireKey.PSRAM_CLOCK_HZ, "Configured PSRAM clock.",
     WireCriticality.METRIC, "Hz"),
    (WireKey.PSRAM_CAPABILITIES, "Driver capability bitfield.",
     WireCriticality.DIAGNOSTIC, "bitmask"),
    (WireKey.PSRAM_STATE, "Driver state enum after bring-up.",
     WireCriticality.DIAGNOSTIC, "int enum"),
    (WireKey.PSRAM_LAST_INIT_STATUS, "Status code of the last init attempt.",
     WireCriticality.DIAGNOSTIC, "int status"),
    (WireKey.PSRAM_XIP_ENABLED, "Whether execute-in-place is on.",
     WireCriticality.DIAGNOSTIC, "0 | 1"),
    (WireKey.PSRAM_TIMING_STATUS, "Timing-scan result.",
     WireCriticality.DIAGNOSTIC, "int status"),
    (WireKey.PSRAM_RXDQS_DELAY, "Chosen RXDQS delay tap.",
     WireCriticality.DIAGNOSTIC, "int"),
)


PSRAM_SPECS: tuple[WireSpec, ...] = tuple(
    _spec(
        key.wire,
        WireKind.KEY_VALUE,
        description,
        WireConsumer.FIRMWARE_META,
        criticality,
        key=key,
        # NOT ExecuTorch: it has no PSRAM support (preflight rejects the
        # combination) and since the #187 gate finding its child overrides
        # engine_psram_metadata empty — test-rendered ET psram arms emit
        # none of these keys.
        engines=TFLM_ENGINES | AOT_ENGINES,
        condition=GATE_PSRAM_METADATA,
        engine_conditions={EngineType.HELIA_AOT: GATE_AOT_PSRAM_METADATA},
        value_shape=value_shape,
        note=(
            "Presence of HPX_PSRAM_CLOCK_HZ is what makes the parser build a "
            "PsramInfo at all; the other seven default to 0 inside it."
            if key is WireKey.PSRAM_CLOCK_HZ
            else "Lands in summary['psram'] via PsramInfo."
        ),
    )
    for key, description, criticality, value_shape in _PSRAM_METADATA_KEYS
) + (
    _spec(
        WireKey.PSRAM_ARENA.wire,
        WireKind.KEY_VALUE,
        "Base address and size of the arena placed in PSRAM.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.PSRAM_ARENA,
        engines=TFLM_ENGINES,
        condition=GATE_ARENA_IN_PSRAM,
        value_shape="0x<addr>,<bytes>",
    ),
    _spec(
        WireKey.PSRAM_READY.wire,
        WireKind.KEY_VALUE,
        "PSRAM is up and awaiting the host's model upload at this address.",
        WireConsumer.TRANSPORT_CONTROL,
        WireCriticality.PROTOCOL,
        key=WireKey.PSRAM_READY,
        engines=TFLM_ENGINES,
        condition=GATE_WEIGHTS_IN_PSRAM,
        value_shape="0x<addr>,<bytes>",
        note="The RTT transport blocks on this line, writes the model over "
        "SWD, then releases the firmware with HPX_GO.",
    ),
    _spec(
        WireKey.PSRAM_ARENA_REGION.wire,
        WireKind.KEY_VALUE,
        "One heliaAOT arena region placed in PSRAM.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.PSRAM_ARENA_REGION,
        engines=AOT_ENGINES,
        condition=GATE_AOT_PSRAM_ARENAS,
        value_shape="<region_id>,0x<addr>,<bytes>",
    ),
)


#: The six clean-window check keys live inside ``engine_clean_window`` in
#: ``_main_base.cc.j2``, and ExecuTorch overrides that block wholesale (it
#: accumulates the runtime's own execute-only cycle count instead), so their
#: absence there is template-structural rather than a consequence of
#: apollo510's STIMER window. The busy-loop probe delegates back to the base,
#: but it is STIMER-timed, so it does not restore them either.
_DWT_CLEAN_WINDOW_ENGINES = TFLM_ENGINES | AOT_ENGINES
_ET_OMITS_THE_CHECK_BLOCK = (
    "Out of ExecuTorch's scope because its engine_clean_window block override "
    "replaces the shared window and emits none of the check keys — a template "
    "structure, not an apollo510 coincidence."
)


CLEAN_WINDOW_SPECS: tuple[WireSpec, ...] = (
    _spec(
        WireKey.CLEAN_WINDOW_PROBE.wire,
        WireKind.KEY_VALUE,
        "The clean window ran a busy loop instead of inferences.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.CLEAN_WINDOW_PROBE,
        condition=GATE_BUSY_LOOP_PROBE,
        value_shape="busy_loop",
        note="The host reaches the same conclusion from its own config, so "
        "this line is currently informational only.",
    ),
    _spec(
        WireKey.CLEAN_ITER.wire,
        WireKind.KEY_VALUE,
        "Per-iteration trace marker inside the clean window.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.CLEAN_ITER,
        condition=GATE_CLEAN_WINDOW_TRACE,
        value_shape="int iteration index",
        note="Opt-in diagnostic; excluded on SWO/UART because printing inside "
        "the window would contaminate the measurement.",
    ),
    _spec(
        WireKey.CLEAN_INFER_COUNT.wire,
        WireKind.KEY_VALUE,
        "Inferences completed inside the gated clean window.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.METRIC,
        key=WireKey.CLEAN_INFER_COUNT,
        value_shape="int",
        runtime_gate="clean_count > 0",
        note="Divides the gated energy, so losing it downgrades power results "
        "to whole-capture estimates. The clean_window_begin heartbeat's "
        "iters= is the host's fallback for exactly that case.",
    ),
    _spec(
        WireKey.CLEAN_INFER_TOTAL_CYCLES.wire,
        WireKind.KEY_VALUE,
        "Total cycles measured across the clean window.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.METRIC,
        key=WireKey.CLEAN_INFER_TOTAL_CYCLES,
        value_shape="cycles",
        runtime_gate="clean_count > 0",
        note="Back-derived from the STIMER measurement on the STIMER path. "
        "With HPX_CLEAN_INFER_AVG_US it feeds PROFILE_CLEAN_WINDOW_FROZEN "
        "(zero elapsed time against completed inferences); the verdict warns, "
        "so the criticality stays metric.",
    ),
    _spec(
        WireKey.CLEAN_INFER_AVG_CYCLES.wire,
        WireKind.KEY_VALUE,
        "Mean cycles per clean inference.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.METRIC,
        key=WireKey.CLEAN_INFER_AVG_CYCLES,
        value_shape="cycles",
        runtime_gate="clean_count > 0",
    ),
    _spec(
        WireKey.CLEAN_INFER_AVG_US.wire,
        WireKind.KEY_VALUE,
        "Mean wall time per clean inference.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.METRIC,
        key=WireKey.CLEAN_INFER_AVG_US,
        value_shape="microseconds",
        runtime_gate="clean_count > 0; on the cycle-counter path additionally "
        "SystemCoreClock > 0",
        note="Seeds the power-window iteration count, so a stalled or zero "
        "value undersizes the next power run. With "
        "HPX_CLEAN_INFER_TOTAL_CYCLES it feeds PROFILE_CLEAN_WINDOW_FROZEN "
        "(zero elapsed time against completed inferences); the verdict warns, "
        "so the criticality stays metric.",
    ),
    _spec(
        WireKey.CLEAN_STALLED_ITERS.wire,
        WireKind.KEY_VALUE,
        "Clean iterations whose cycle counter did not advance at all.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.METRIC,
        key=WireKey.CLEAN_STALLED_ITERS,
        engines=_DWT_CLEAN_WINDOW_ENGINES,
        condition=GATE_NOT_STIMER_WINDOW,
        value_shape="int (0 on a healthy run)",
        note="Always emitted on the cycle-counter path so the host can tell a "
        "firmware that checks from one that does not. Feeds "
        "PROFILE_CLEAN_WINDOW_STALLED with HPX_CLEAN_PARTIAL_ITERS. "
        + _ET_OMITS_THE_CHECK_BLOCK,
    ),
    _spec(
        WireKey.CLEAN_PARTIAL_ITERS.wire,
        WireKind.KEY_VALUE,
        "Clean iterations that advanced far less than the warm reference.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.METRIC,
        key=WireKey.CLEAN_PARTIAL_ITERS,
        engines=_DWT_CLEAN_WINDOW_ENGINES,
        condition=GATE_NOT_STIMER_WINDOW,
        value_shape="int (0 on a healthy run)",
        note="Feeds PROFILE_CLEAN_WINDOW_STALLED with HPX_CLEAN_STALLED_ITERS "
        "— the two failure shapes are counted separately and neither is "
        "inferred from the other. " + _ET_OMITS_THE_CHECK_BLOCK,
    ),
    _spec(
        WireKey.CLEAN_REF_CYCLES.wire,
        WireKind.KEY_VALUE,
        "Warm per-inference cycle reference the partial check compares to.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.CLEAN_REF_CYCLES,
        engines=_DWT_CLEAN_WINDOW_ENGINES,
        condition=GATE_NOT_STIMER_WINDOW,
        value_shape="cycles",
        note="Emitted so the stall threshold is auditable from the capture "
        "rather than taken on trust. Sole input to "
        "PROFILE_CLEAN_WINDOW_CHECK_INOPERATIVE: a zero reference means no "
        "iteration could fall below the floor, so losing this key silently "
        "disables the verdict that says the partial-stall check did not run. "
        + _ET_OMITS_THE_CHECK_BLOCK,
    ),
    _spec(
        WireKey.CLEAN_DWT_RATE_CYC.wire,
        WireKind.KEY_VALUE,
        "Cycles the counter advanced during a fixed calibration probe.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.METRIC,
        key=WireKey.CLEAN_DWT_RATE_CYC,
        engines=_DWT_CLEAN_WINDOW_ENGINES,
        condition=GATE_NOT_STIMER_WINDOW,
        value_shape="cycles",
        note="With HPX_CLEAN_DWT_RATE_US and HPX_SYSTEM_CLOCK_HZ this feeds "
        "PROFILE_CLEAN_WINDOW_CLOCK_RATE_LOW — the only check that can see a "
        "uniform slowdown, since the in-window counters are DWT-relative and "
        "cancel under one. " + _ET_OMITS_THE_CHECK_BLOCK,
    ),
    _spec(
        WireKey.CLEAN_DWT_RATE_US.wire,
        WireKind.KEY_VALUE,
        "Duration of that calibration probe.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.METRIC,
        key=WireKey.CLEAN_DWT_RATE_US,
        engines=_DWT_CLEAN_WINDOW_ENGINES,
        condition=GATE_NOT_STIMER_WINDOW,
        value_shape="microseconds",
        note="Printed from the HPX_CLEAN_DWT_RATE_PROBE_US macro — one of the "
        "few cases where a compile-time constant travels on the wire. The "
        "denominator of PROFILE_CLEAN_WINDOW_CLOCK_RATE_LOW, with "
        "HPX_CLEAN_DWT_RATE_CYC and HPX_SYSTEM_CLOCK_HZ. "
        + _ET_OMITS_THE_CHECK_BLOCK,
    ),
    _spec(
        WireKey.CLEAN_ATTACH_WAIT_US.wire,
        WireKind.KEY_VALUE,
        "How long the firmware waited for the debug probe before the window.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.CLEAN_ATTACH_WAIT_US,
        engines=_DWT_CLEAN_WINDOW_ENGINES,
        condition=GATE_ATTACH_WAIT,
        value_shape="microseconds",
        note=_ET_OMITS_THE_CHECK_BLOCK,
    ),
    _spec(
        WireKey.PROFILED_INFER_COUNT.wire,
        WireKind.KEY_VALUE,
        "Instrumented inferences summed across all PMU passes.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.METRIC,
        key=WireKey.PROFILED_INFER_COUNT,
        engines=TFLM_ENGINES | AOT_ENGINES,
        condition=GATE_NOT_POWER_ONLY,
        runtime_gate="profiled_infer_count > 0 && SystemCoreClock > 0",
        value_shape="int",
        note="ExecuTorch overrides this block empty on purpose: its invoke "
        "path is not a pure inference call, so the same keys would carry "
        "different semantics.",
    ),
    _spec(
        WireKey.PROFILED_INFER_TOTAL_US.wire,
        WireKind.KEY_VALUE,
        "Total instrumented inference time.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.METRIC,
        key=WireKey.PROFILED_INFER_TOTAL_US,
        engines=TFLM_ENGINES | AOT_ENGINES,
        condition=GATE_NOT_POWER_ONLY,
        runtime_gate="profiled_infer_count > 0 && SystemCoreClock > 0",
        value_shape="microseconds",
    ),
    _spec(
        WireKey.PROFILED_INFER_AVG_US.wire,
        WireKind.KEY_VALUE,
        "Mean instrumented inference time.",
        WireConsumer.FIRMWARE_META,
        WireCriticality.METRIC,
        key=WireKey.PROFILED_INFER_AVG_US,
        engines=TFLM_ENGINES | AOT_ENGINES,
        condition=GATE_NOT_POWER_ONLY,
        runtime_gate="profiled_infer_count > 0 && SystemCoreClock > 0",
        value_shape="microseconds",
        note="Fallback latency source when the clean window produced none.",
    ),
    _spec(
        WireKey.PMU_INIT_STATUS.wire,
        WireKind.KEY_VALUE,
        "Status returned by nsx_pmu_init() for this pass.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.PMU_INIT_STATUS,
        engines=ET_ENGINES,
        value_shape="int status",
    ),
    _spec(
        WireKey.PMU_SELFTEST_CPU_CYCLES.wire,
        WireKind.KEY_VALUE,
        "Cycles observed during the PMU self-test busy loop.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.PMU_SELFTEST_CPU_CYCLES,
        engines=ET_ENGINES,
        runtime_gate="the pass selects ARM_PMU_CPU_CYCLES (event 0x0011)",
        value_shape="cycles",
        note="Zero here means a powered-down or frozen PMU, which would "
        "otherwise produce plausible-looking all-zero layer data.",
    ),
)


HEARTBEAT_SPECS: tuple[WireSpec, ...] = (
    _spec(
        heartbeat_token(HeartbeatPhase.CLEAN_WINDOW_BEGIN),
        WireKind.HEARTBEAT,
        "Announces the silent clean window before it starts.",
        WireConsumer.TRANSPORT_CONTROL,
        WireCriticality.PROTOCOL,
        value_shape="iters=<n> est_ms=<n>",
        note="The host widens its capture deadline from est_ms and keeps "
        "iters as a fallback for HPX_CLEAN_INFER_COUNT. " + EST_MS_GAP,
    ),
    _spec(
        heartbeat_token(HeartbeatPhase.INIT),
        WireKind.HEARTBEAT,
        "Firmware reached engine initialisation.",
        WireConsumer.DIAGNOSTIC,
        WireCriticality.DIAGNOSTIC,
        condition=GATE_NOT_POWER_ONLY,
        note="TFLM/heliaRT append a 't=0' field the other engines omit.",
    ),
    _spec(
        heartbeat_token(HeartbeatPhase.ALLOCATE),
        WireKind.HEARTBEAT,
        "About to call AllocateTensors().",
        WireConsumer.DIAGNOSTIC,
        WireCriticality.DIAGNOSTIC,
        engines=TFLM_ENGINES,
        condition=GATE_NOT_POWER_ONLY,
    ),
    _spec(
        heartbeat_token(HeartbeatPhase.ALLOCATED),
        WireKind.HEARTBEAT,
        "AllocateTensors() succeeded.",
        WireConsumer.DIAGNOSTIC,
        WireCriticality.DIAGNOSTIC,
        engines=TFLM_ENGINES,
        value_shape="arena_used=<bytes>",
    ),
    _spec(
        heartbeat_token(HeartbeatPhase.MODEL_INIT_DONE),
        WireKind.HEARTBEAT,
        "heliaAOT model_init() returned successfully.",
        WireConsumer.DIAGNOSTIC,
        WireCriticality.DIAGNOSTIC,
        engines=AOT_ENGINES,
    ),
    _spec(
        heartbeat_token(HeartbeatPhase.INFER),
        WireKind.HEARTBEAT,
        "Progress inside an instrumented inference, between operators.",
        WireConsumer.DIAGNOSTIC,
        WireCriticality.DIAGNOSTIC,
        value_shape="pass=<n> iter=<n> layer=<n>",
        runtime_gate="heartbeat cadence reached (ops or elapsed time)",
        note="Three emit sites, one format: TFLM's profiler class, heliaAOT's "
        "operator callback, ExecuTorch's end_operator hook. TFLM's lives in "
        "hpx_pmu_profiler.cc, not main.cc.",
    ),
    _spec(
        heartbeat_token(HeartbeatPhase.WARMUP_DONE),
        WireKind.HEARTBEAT,
        "One pass finished its warmup iterations.",
        WireConsumer.DIAGNOSTIC,
        WireCriticality.DIAGNOSTIC,
        condition=GATE_NOT_POWER_ONLY,
        value_shape="pass=<n>",
    ),
    _spec(
        heartbeat_token(HeartbeatPhase.FLUSHING),
        WireKind.HEARTBEAT,
        "Draining the RTT buffer before the end sentinel.",
        WireConsumer.DIAGNOSTIC,
        WireCriticality.DIAGNOSTIC,
        condition=GATE_RTT_FLUSH,
        note="Refreshes the host inactivity timer while the drain runs.",
    ),
)
