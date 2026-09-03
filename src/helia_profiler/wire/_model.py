"""Wire-protocol vocabulary: constants, enums and the spec dataclass.

The narrative lives in the package docstring (``helia_profiler.wire``); this
module holds the types the three spec catalogues are written in, so the
catalogues import from here and nothing imports back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from ..engines import EngineType

# ---------------------------------------------------------------------------
# Generic parse constants
# ---------------------------------------------------------------------------

#: Namespace prefix shared by every token in the protocol.
HPX_PREFIX = "HPX_"

#: The generic metadata line grammar. ``\\w+`` is why hyphenated engine names
#: are underscored on the wire (``helia_aot``, not ``helia-aot``) and why
#: ``HPX_CONST_BLOB_LOADED region=0 …`` is not a key/value line.
KEY_VALUE_PATTERN = r"^HPX_(\w+)=(.+)$"
KEY_VALUE_RE = re.compile(KEY_VALUE_PATTERN)

#: Protocol version emitted as ``HPX_VERSION`` and expected by the parser.
HPX_PROTOCOL_VERSION = 1

# ---------------------------------------------------------------------------
# Sentinels, handshake lines and line prefixes — single source
# ---------------------------------------------------------------------------

HPX_START_SENTINEL = "--- HPX_START ---"
HPX_END_SENTINEL = "--- HPX_END ---"

#: Per-pass and per-iteration frames. The firmware formats the name/index in;
#: the host matches with the anchored patterns below.
HPX_PRESET_SENTINEL_PREFIX = "--- HPX_PRESET "
HPX_ITER_SENTINEL_PREFIX = "--- HPX_ITER "
HPX_PRESET_SENTINEL_PATTERN = r"^--- HPX_PRESET (\S+) ---$"
HPX_ITER_SENTINEL_PATTERN = r"^--- HPX_ITER (\d+) ---$"
HPX_PRESET_SENTINEL_RE = re.compile(HPX_PRESET_SENTINEL_PATTERN)
HPX_ITER_SENTINEL_RE = re.compile(HPX_ITER_SENTINEL_PATTERN)

POWER_TERMINAL_START_SENTINEL = "--- HPX_POWER_TERMINAL_START ---"
POWER_TERMINAL_END_SENTINEL = "--- HPX_POWER_TERMINAL_END ---"

#: Version of the power terminal envelope (``HPX_POWER_TERMINAL_VERSION``).
POWER_TERMINAL_VERSION = 1

#: Liveness line the firmware prints before the start header — once on RTT, as
#: a 40-line sync preamble on SWO/UART, never on USB CDC (which polls DTR).
HPX_READY_LINE = "HPX_READY"

#: The one host->device token: written to RTT down-channel 0 to release
#: firmware that is waiting for the host to upload PSRAM weights.
HPX_GO_COMMAND = "HPX_GO"

HPX_HEARTBEAT_PREFIX = "HPX_HEARTBEAT"

#: Shared prefix of every power-terminal field and pre-record diagnostic. The
#: envelope parser logs anything with this prefix that arrives ahead of the
#: start marker, which is how monitor diagnostics stay outside the contract.
HPX_POWER_PREFIX = "HPX_POWER_"
HPX_ERROR_PREFIX = "HPX_ERROR="
HPX_WARN_PREFIX = "HPX_WARN="


#: The one statement of the ``est_ms`` contract, single-sourced because it is
#: told in three places (the ``clean_window_begin`` spec note, the package
#: docstring's gap list and the generated reference's). #164 gave the
#: fixed+STIMER profile infer arm the auto arm's pre-window DWT measurement
#: (the debug domain is gated only *inside* the window, so pre-window DWT is
#: valid even where STIMER times the window itself); #170 gave busy-loop
#: windows the honest compile-time target and structurally excluded power
#: renders from measuring at all.
EST_MS_GAP = (
    "Every profile build's `clean_window_begin` heartbeat carries a real "
    "duration statement: infer windows announce a measured warm-inference "
    "estimate (both window modes), and busy-loop windows announce "
    "`window_target_ms` itself as a compile-time constant — the busy loop is "
    "calibrated to fill exactly that, and the iteration count drives nothing "
    "inside it (#170). The hardcoded `est_ms=0` survives only in dedicated "
    "power binaries, where `hpx_printf` compiles to a no-op and the host "
    "times the capture from its planned duration — no listener exists, and "
    "the power arm is the template's first branch in both window modes, so "
    "no power render measures anything pre-window. A runtime `est_ms=0` can "
    "still appear on an infer window if the measurement degrades (DWT frozen "
    "through every warmup by a debugger-attach transient); the host then "
    "reads 0 as 'no estimate' and keeps its flat heartbeat timeout. "
    "Byte-stream transports (RTT/SWO/UART, via collect_lines) hold an "
    "announced budget as a floor on their inactivity deadline until it "
    "expires; USB CDC raises its overall capture deadline instead, and its "
    "300 s per-read line gap is not widened — a silent window longer than "
    "that is cut short on USB CDC regardless of the announce. Both derive "
    "the budget from window_budget_s, capped at WINDOW_BUDGET_CAP_S. A "
    "busy-loop announce carries iters=1 — the window completes exactly one "
    "busy pass, and on a lossy transport that drops HPX_CLEAN_INFER_COUNT "
    "the iters fallback feeds the gate-duration check, which a planned "
    "inference count the window never runs would fail as a duration "
    "mismatch. (Per-inference energy is never derived for busy windows — "
    "the summary omits it by probe.)"
)


# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------


class WireKind(StrEnum):
    """Which of the protocol's grammars a token belongs to."""

    KEY_VALUE = "key_value"
    #: A printf-indexed key family: one format string, one key per index.
    KEY_VALUE_INDEXED = "key_value_indexed"
    #: ``HPX_NAME k=v k=v`` — token-then-payload, *not* matched by the generic
    #: key/value regex (the space defeats ``\\w+=``).
    RECORD = "record"
    SENTINEL = "sentinel"
    HEARTBEAT = "heartbeat"
    ERROR = "error"
    WARN = "warn"
    HANDSHAKE = "handshake"
    TERMINAL = "terminal"
    #: The per-layer CSV body. Carries no ``HPX_`` token, so no spec uses this
    #: kind; the grammar lives in :data:`CSV_GRAMMAR`.
    CSV = "csv"


class WireDirection(StrEnum):
    DEVICE_TO_HOST = "device_to_host"
    HOST_TO_DEVICE = "host_to_device"


class WireBinary(StrEnum):
    """Which firmware binary actually puts the token on the wire at runtime."""

    #: The transport-attached profiling binary (``hpx_profiler``).
    TRANSPORT = "transport"
    #: The dedicated free-running power binary (``hpx_profiler_power``).
    POWER = "power"


class WireConsumer(StrEnum):
    """What on the host reads the token."""

    #: Named into ``FirmwareMeta`` (or ``PsramInfo``) by ``capture.parser``.
    FIRMWARE_META = "firmware_meta"
    #: Read by the transport/capture layer for framing, handshake or timing.
    TRANSPORT_CONTROL = "transport_control"
    #: Read by ``capture.power_terminal`` into the power envelope.
    POWER_TERMINAL = "power_terminal"
    #: Logged or checked, never stored in a result artifact.
    DIAGNOSTIC = "diagnostic"
    #: Emitted by firmware and read by nothing.
    UNCONSUMED = "unconsumed"


class WireCriticality(StrEnum):
    """What losing the token costs."""

    #: Losing it breaks capture, a handshake, or a verdict that fails closed.
    #: A token whose only consumer is a verdict that merely *warns* is not
    #: protocol-critical — those are METRIC, and the verdict they feed is named
    #: in the spec's note.
    PROTOCOL = "protocol"
    #: Losing it silently degrades a reported number.
    METRIC = "metric"
    #: Losing it costs only diagnosability.
    DIAGNOSTIC = "diagnostic"


ALL_ENGINES: frozenset[EngineType] = frozenset(EngineType)
#: ``main.cc.j2`` renders identically for both — one template, two engine ids.
TFLM_ENGINES: frozenset[EngineType] = frozenset({EngineType.TFLM, EngineType.HELIA_RT})
AOT_ENGINES: frozenset[EngineType] = frozenset({EngineType.HELIA_AOT})
ET_ENGINES: frozenset[EngineType] = frozenset({EngineType.EXECUTORCH})
#: Engines with a dedicated power binary. ExecuTorch has none — preflight
#: rejects ``engine.type=executorch`` with ``power.enabled``.
POWER_BINARY_ENGINES: frozenset[EngineType] = frozenset(
    {EngineType.TFLM, EngineType.HELIA_RT, EngineType.HELIA_AOT}
)


class HeartbeatPhase(StrEnum):
    """The ``phase=`` values of ``HPX_HEARTBEAT`` records.

    Eight phases, not nine: TFLM's ``init`` record carries an extra ``t=0``
    payload field the other engines omit, which is a payload variant of one
    phase rather than a phase of its own.

    :attr:`CLEAN_WINDOW_BEGIN` is the only protocol-critical member — the host
    parses its ``iters=`` (fallback for ``HPX_CLEAN_INFER_COUNT`` on lossy
    transports) and its ``est_ms=`` (widens the capture deadline across the
    deliberately silent measurement window).
    """

    CLEAN_WINDOW_BEGIN = "clean_window_begin"
    INIT = "init"
    ALLOCATE = "allocate"
    ALLOCATED = "allocated"
    MODEL_INIT_DONE = "model_init_done"
    INFER = "infer"
    WARMUP_DONE = "warmup_done"
    FLUSHING = "flushing"


class FirmwareErrorCode(StrEnum):
    """``HPX_ERROR=<code>`` catalogue.

    The code is the first token of the payload, delimited by a space or a
    colon (``schema_mismatch:1234_vs_3``, ``unsupported_op kind=custom …``);
    ``capture._raise_on_firmware_error`` splits it exactly that way. Every
    code carries a host hint — see :attr:`WireSpec.has_host_hint`.
    """

    SCHEMA_MISMATCH = "schema_mismatch"
    UNSUPPORTED_OP = "unsupported_op"
    MISSING_OPS = "missing_ops"
    ALLOC_TENSORS_FAILED = "alloc_tensors_failed"
    PSRAM_INIT_FAILED = "psram_init_failed"
    PSRAM_INFO_FAILED = "psram_info_failed"
    BIND_ARENA_FAILED = "bind_arena_failed"
    CONST_BLOB_PSRAM_WRITE_FAILED = "const_blob_psram_write_failed"
    MODEL_INIT_FAILED = "model_init_failed"
    EXECUTORCH = "executorch"
    OPERATOR_COUNT_EXCEEDS_CAPACITY = "operator_count_exceeds_capacity"
    PMU_INIT_OR_SELFTEST_FAILED = "pmu_init_or_selftest_failed"
    STIMER_DEAD = "stimer_dead"


class FirmwareWarnCode(StrEnum):
    """``HPX_WARN=<code>`` catalogue. Non-fatal; the run continues."""

    UNUSUAL_DTYPE = "unusual_dtype"


class WireKey(StrEnum):
    """Every ``HPX_<KEY>=<value>`` key, valued as the parser's meta-key name.

    Values are the *lower-cased* key, which is what
    ``capture.parser.parse_firmware_output`` stores in ``meta_kv``, so a member
    indexes those dicts interchangeably with the string it replaced.
    :attr:`wire` recovers the ``HPX_…`` spelling for census and docs.
    """

    # --- start header -----------------------------------------------------
    VERSION = "version"
    ENGINE = "engine"
    EXTREME_MODE = "extreme_mode"
    ITERATIONS = "iterations"
    WARMUP = "warmup"
    NUM_PRESETS = "num_presets"
    PRESETS = "presets"
    POWER_SYNC = "power_sync"
    SYNC_GPIO = "sync_gpio"
    SYSTEM_CLOCK_HZ = "system_clock_hz"
    BURST_AVAIL = "burst_avail"
    BURST_ENGAGED = "burst_engaged"

    # --- heartbeat configuration -----------------------------------------
    HEARTBEAT_ENABLED = "heartbeat_enabled"
    HEARTBEAT_EVERY_N_OPS = "heartbeat_every_n_ops"
    HEARTBEAT_EVERY_MS = "heartbeat_every_ms"

    # --- model / memory ---------------------------------------------------
    MODEL_SIZE = "model_size"
    ARENA_SIZE = "arena_size"
    ALLOCATED_ARENA = "allocated_arena"
    INPUT_SIZE = "input_size"
    OUTPUT_SIZE = "output_size"
    NUM_TENSORS = "num_tensors"
    NUM_INPUTS = "num_inputs"
    NUM_OUTPUTS = "num_outputs"
    INPUT_INDEXED_SIZE = "input_%d_size"
    OUTPUT_INDEXED_SIZE = "output_%d_size"
    ARENAS_BOUND = "arenas_bound"

    # --- PSRAM ------------------------------------------------------------
    PSRAM_SIZE_BYTES = "psram_size_bytes"
    PSRAM_CLOCK_HZ = "psram_clock_hz"
    PSRAM_CAPABILITIES = "psram_capabilities"
    PSRAM_STATE = "psram_state"
    PSRAM_LAST_INIT_STATUS = "psram_last_init_status"
    PSRAM_XIP_ENABLED = "psram_xip_enabled"
    PSRAM_TIMING_STATUS = "psram_timing_status"
    PSRAM_RXDQS_DELAY = "psram_rxdqs_delay"
    PSRAM_ARENA = "psram_arena"
    PSRAM_READY = "psram_ready"
    PSRAM_ARENA_REGION = "psram_arena_region"

    # --- clean window -----------------------------------------------------
    CLEAN_WINDOW_PROBE = "clean_window_probe"
    CLEAN_ITER = "clean_iter"
    CLEAN_INFER_COUNT = "clean_infer_count"
    CLEAN_INFER_TOTAL_CYCLES = "clean_infer_total_cycles"
    CLEAN_INFER_AVG_CYCLES = "clean_infer_avg_cycles"
    CLEAN_INFER_AVG_US = "clean_infer_avg_us"
    CLEAN_STALLED_ITERS = "clean_stalled_iters"
    CLEAN_PARTIAL_ITERS = "clean_partial_iters"
    CLEAN_REF_CYCLES = "clean_ref_cycles"
    CLEAN_DWT_RATE_CYC = "clean_dwt_rate_cyc"
    CLEAN_DWT_RATE_US = "clean_dwt_rate_us"
    CLEAN_ATTACH_WAIT_US = "clean_attach_wait_us"

    # --- profiled summary -------------------------------------------------
    PROFILED_INFER_COUNT = "profiled_infer_count"
    PROFILED_INFER_TOTAL_US = "profiled_infer_total_us"
    PROFILED_INFER_AVG_US = "profiled_infer_avg_us"

    # --- ExecuTorch PMU bring-up -----------------------------------------
    PMU_INIT_STATUS = "pmu_init_status"
    PMU_SELFTEST_CPU_CYCLES = "pmu_selftest_cpu_cycles"

    @property
    def wire(self) -> str:
        """The ``HPX_…`` spelling of this key as firmware prints it.

        For every ``\\w+`` key this is ``f"HPX_{self.name}"``; the two indexed
        keys carry a ``%d`` conversion that ``str.upper()`` would mangle into
        ``%D``, so the value is upper-cased around it.
        """
        return HPX_PREFIX + "%d".join(part.upper() for part in self.value.split("%d"))


class PowerTerminalKey(StrEnum):
    """Fields of the power terminal envelope, valued as the literal wire key.

    Unlike :class:`WireKey` these keep their ``HPX_POWER_…`` spelling: the
    envelope parser works on raw ``key=value`` splits inside the record's
    markers and never lower-cases anything.
    """

    # --- required envelope (9) -------------------------------------------
    TERMINAL_VERSION = "HPX_POWER_TERMINAL_VERSION"
    STATUS = "HPX_POWER_STATUS"
    REQUESTED_COUNT = "HPX_POWER_REQUESTED_COUNT"
    COMPLETED_COUNT = "HPX_POWER_COMPLETED_COUNT"
    ELAPSED_US = "HPX_POWER_ELAPSED_US"
    FINAL_PHASE = "HPX_POWER_FINAL_PHASE"
    ERROR_CODE = "HPX_POWER_ERROR_CODE"
    GATE_ASSERTED = "HPX_POWER_GATE_ASSERTED"
    GATE_LOWERED = "HPX_POWER_GATE_LOWERED"

    # --- optional measurement payload (all-or-none) ----------------------
    MEASUREMENT_SOURCE = "HPX_POWER_MEASUREMENT_SOURCE"
    MEASUREMENT_SCOPE = "HPX_POWER_MEASUREMENT_SCOPE"
    ENERGY_NJ = "HPX_POWER_ENERGY_NJ"
    MEASUREMENT_DURATION_US = "HPX_POWER_MEASUREMENT_DURATION_US"
    MEASUREMENT_COUNT = "HPX_POWER_MEASUREMENT_COUNT"
    MEASUREMENT_OVERFLOW = "HPX_POWER_MEASUREMENT_OVERFLOW"
    CHARGE_NC = "HPX_POWER_CHARGE_NC"
    BUS_VOLTAGE_UV = "HPX_POWER_BUS_VOLTAGE_UV"
    # HPX_POWER_SAMPLE_COUNT was retired by #165: the host accepted it but no
    # template ever emitted it, and HPX_POWER_MEASUREMENT_COUNT already
    # carries the accumulator count. An envelope carrying it is now rejected
    # as an unknown field, like any other unregistered key.
    CALIBRATION_ID = "HPX_POWER_CALIBRATION_ID"

    # --- pre-record diagnostics (outside the envelope) -------------------
    INA228_DIAG = "HPX_POWER_INA228_DIAG"
    INA228_BYSTANDER_FAILED = "HPX_POWER_INA228_BYSTANDER_FAILED"


def heartbeat_token(phase: HeartbeatPhase) -> str:
    """Registry key for one heartbeat phase (also its literal line prefix)."""
    return f"{HPX_HEARTBEAT_PREFIX} phase={phase.value}"


def error_token(code: FirmwareErrorCode) -> str:
    """Registry key for one error code (also its literal line prefix)."""
    return f"{HPX_ERROR_PREFIX}{code.value}"


def warn_token(code: FirmwareWarnCode) -> str:
    """Registry key for one warning code (also its literal line prefix)."""
    return f"{HPX_WARN_PREFIX}{code.value}"


# ---------------------------------------------------------------------------
# Spec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WireSpec:
    """The contract for one wire token.

    ``condition`` is the **template** gate: a declarative boolean expression
    over the firmware render variables that decides whether this token's source
    text is emitted into the rendered C at all. ``None`` means unconditional
    within :attr:`engines`. ``engine_conditions`` overrides it for engines whose
    template gates the same token differently (heliaAOT binds its PSRAM through
    its arena regions, ExecuTorch reaches the shared clean window only through
    the busy-loop probe, and so on). The census in
    ``tests/contracts/test_wire_protocol.py`` maps every distinct expression to
    a predicate and asserts presence *and absence* across the render matrix, so
    a wrong condition here is a test failure, not a stale comment.

    ``runtime_gate`` is the other half: a C-level ``if`` or ``if constexpr``
    that decides whether the rendered line actually prints. It records the gate
    wherever one exists, whether it *selects* between shapes (the heartbeat
    cadence, the PMU pass that owns the cycle event) or merely guards a success
    path (``clean_count > 0``, ``success && g_hpx_ina228_ok``); ``None`` means
    the rendered line prints unconditionally. It is documentation only — no
    render-level test can see it.

    ``criticality`` records what losing the token costs. Two power-terminal
    fields feed validity codes whose severity depends on the power mode
    (``POWER_WINDOW_CLOCK_FROZEN`` and ``POWER_ON_DEVICE_OVERFLOW`` are errors
    when the on-device measurement is the measurement of record and warnings
    when it is a bystander); the token is protocol-critical either way, and the
    mode-dependence lives with the issue code in ``results.issues``.
    """

    token: str
    kind: WireKind
    description: str
    consumer: WireConsumer
    criticality: WireCriticality
    engines: frozenset[EngineType] = ALL_ENGINES
    binary: WireBinary = WireBinary.TRANSPORT
    direction: WireDirection = WireDirection.DEVICE_TO_HOST
    condition: str | None = None
    engine_conditions: Mapping[EngineType, str | None] = field(
        default_factory=lambda: MappingProxyType({})
    )
    runtime_gate: str | None = None
    value_shape: str = ""
    #: Exact line for sentinels and valueless handshake lines.
    literal: str | None = None
    key: WireKey | None = None
    #: Terminal kind only: ``True`` = required envelope field, ``False`` =
    #: optional measurement field, ``None`` = pre-record diagnostic.
    required: bool | None = None
    #: Error kind only: whether ``capture._ERROR_HINTS`` explains this code.
    has_host_hint: bool = False
    #: ``False`` for tokens the host accepts but no template emits.
    emitted_by_firmware: bool = True
    note: str = ""

    def condition_for(self, engine: EngineType) -> str | None:
        """The template gate for *engine* — the per-engine override if any."""
        if engine in self.engine_conditions:
            return self.engine_conditions[engine]
        return self.condition


def _spec(*args, **kwargs) -> WireSpec:
    """Build a :class:`WireSpec`, freezing any per-engine condition map."""
    overrides = kwargs.pop("engine_conditions", None)
    if overrides is not None:
        kwargs["engine_conditions"] = MappingProxyType(dict(overrides))
    return WireSpec(*args, **kwargs)


# Declarative template-gate expressions. Every string used as a ``condition``
# appears here, and the census asserts the two sets match exactly — a typo
# cannot silently become an unchecked condition.
GATE_NOT_POWER_ONLY = "not power_only"
GATE_TRANSPORT_HAS_READY_PREAMBLE = "not power_only and transport != usb_cdc"
GATE_RTT_FLUSH = "not power_only and transport == rtt"
GATE_APOLLO3_BURST = "apollo3_burst"
GATE_PSRAM_METADATA = "psram_needed and not power_only"
GATE_PSRAM_NEEDED = "psram_needed"
GATE_ARENA_IN_PSRAM = "arena_region == psram"
GATE_WEIGHTS_IN_PSRAM = "weights_region == psram"
GATE_PSRAM_WEIGHTS_UPLOAD = "weights_region == psram and transport == rtt and not power_only"
GATE_AOT_EXTERNAL_ARENAS = "not allocate_arenas and arena_regions"
GATE_AOT_PSRAM_ARENAS = "not allocate_arenas and arena_regions with placement == psram"
GATE_AOT_PSRAM_METADATA = (
    "not allocate_arenas and arena_regions with placement == psram and not power_only"
)
GATE_AOT_CONST_BLOBS = "not allocate_arenas and arena_regions with blob_filename"
GATE_AOT_CONST_BLOBS_IN_PSRAM = (
    "not allocate_arenas and arena_regions with blob_filename and placement == psram"
)
GATE_BUSY_LOOP_PROBE = "busy_loop_probe"
GATE_STIMER_WINDOW = "use_stimer_window and not power_only"
GATE_CLEAN_WINDOW_TRACE = "clean_window_trace and transport not in (swo, uart)"
GATE_NOT_STIMER_WINDOW = "not use_stimer_window"
GATE_ATTACH_WAIT = (
    "clean_window_needs_probe_attach and transport == rtt and not power_only "
    "and not use_stimer_window"
)
GATE_POWER_ONLY = "power_only"
GATE_POWER_INA228 = "power_only and power_monitor == ina228"
