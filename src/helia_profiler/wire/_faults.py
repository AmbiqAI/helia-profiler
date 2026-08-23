"""Specs for the error and warning catalogues."""

from __future__ import annotations

from ..engines import EngineType
from ._model import (
    AOT_ENGINES,
    ET_ENGINES,
    GATE_AOT_CONST_BLOBS_IN_PSRAM,
    GATE_AOT_EXTERNAL_ARENAS,
    GATE_AOT_PSRAM_ARENAS,
    GATE_BUSY_LOOP_PROBE,
    GATE_NOT_POWER_ONLY,
    GATE_PSRAM_NEEDED,
    GATE_STIMER_WINDOW,
    TFLM_ENGINES,
    FirmwareErrorCode,
    FirmwareWarnCode,
    WireConsumer,
    WireCriticality,
    WireKind,
    WireSpec,
    _spec,
    error_token,
    warn_token,
)

ERROR_SPECS: tuple[WireSpec, ...] = (
    _spec(
        error_token(FirmwareErrorCode.SCHEMA_MISMATCH),
        WireKind.ERROR,
        "The model's TFLite schema version is not the one firmware was built "
        "for.",
        WireConsumer.TRANSPORT_CONTROL,
        WireCriticality.PROTOCOL,
        engines=TFLM_ENGINES,
        value_shape="schema_mismatch:<found>_vs_<expected>",
        has_host_hint=True,
    ),
    _spec(
        error_token(FirmwareErrorCode.UNSUPPORTED_OP),
        WireKind.ERROR,
        "An operator in the model is not registered in the resolver.",
        WireConsumer.TRANSPORT_CONTROL,
        WireCriticality.PROTOCOL,
        engines=TFLM_ENGINES,
        value_shape="kind=custom|builtin [builtin=<n>] name=<s> index=<n>",
        has_host_hint=True,
    ),
    _spec(
        error_token(FirmwareErrorCode.MISSING_OPS),
        WireKind.ERROR,
        "Summary count of unregistered operators after the preflight walk.",
        WireConsumer.TRANSPORT_CONTROL,
        WireCriticality.PROTOCOL,
        engines=TFLM_ENGINES,
        value_shape="count=<n> hint=rebuild_with_op_registration",
        has_host_hint=True,
    ),
    _spec(
        error_token(FirmwareErrorCode.ALLOC_TENSORS_FAILED),
        WireKind.ERROR,
        "TFLM AllocateTensors() failed.",
        WireConsumer.TRANSPORT_CONTROL,
        WireCriticality.PROTOCOL,
        engines=TFLM_ENGINES,
        value_shape="arena=<bytes> status=<n> hint=<s>",
        has_host_hint=True,
    ),
    _spec(
        error_token(FirmwareErrorCode.PSRAM_INIT_FAILED),
        WireKind.ERROR,
        "PSRAM bring-up failed on the target.",
        WireConsumer.TRANSPORT_CONTROL,
        WireCriticality.PROTOCOL,
        engines=TFLM_ENGINES | AOT_ENGINES,
        condition=GATE_PSRAM_NEEDED,
        engine_conditions={EngineType.HELIA_AOT: GATE_AOT_PSRAM_ARENAS},
        has_host_hint=True,
    ),
    _spec(
        error_token(FirmwareErrorCode.PSRAM_INFO_FAILED),
        WireKind.ERROR,
        "PSRAM came up but its info query failed.",
        WireConsumer.TRANSPORT_CONTROL,
        WireCriticality.PROTOCOL,
        engines=TFLM_ENGINES | AOT_ENGINES,
        condition=GATE_PSRAM_NEEDED,
        engine_conditions={EngineType.HELIA_AOT: GATE_AOT_PSRAM_ARENAS},
        has_host_hint=True,
    ),
    _spec(
        error_token(FirmwareErrorCode.BIND_ARENA_FAILED),
        WireKind.ERROR,
        "Binding one external arena region to the heliaAOT model failed.",
        WireConsumer.TRANSPORT_CONTROL,
        WireCriticality.PROTOCOL,
        engines=AOT_ENGINES,
        condition=GATE_AOT_EXTERNAL_ARENAS,
        value_shape="bind_arena_failed:<status>:region=<id>",
        has_host_hint=True,
    ),
    _spec(
        error_token(FirmwareErrorCode.CONST_BLOB_PSRAM_WRITE_FAILED),
        WireKind.ERROR,
        "Writing a constant sidecar blob into PSRAM failed.",
        WireConsumer.TRANSPORT_CONTROL,
        WireCriticality.PROTOCOL,
        engines=AOT_ENGINES,
        condition=GATE_AOT_CONST_BLOBS_IN_PSRAM,
        value_shape="const_blob_psram_write_failed:region=<id>",
        has_host_hint=True,
    ),
    _spec(
        error_token(FirmwareErrorCode.MODEL_INIT_FAILED),
        WireKind.ERROR,
        "heliaAOT model_init() returned a non-zero status.",
        WireConsumer.TRANSPORT_CONTROL,
        WireCriticality.PROTOCOL,
        engines=AOT_ENGINES,
        value_shape="model_init_failed:<status>",
        has_host_hint=True,
    ),
    _spec(
        error_token(FirmwareErrorCode.EXECUTORCH),
        WireKind.ERROR,
        "An ExecuTorch runtime call failed; the stage names which one.",
        WireConsumer.TRANSPORT_CONTROL,
        WireCriticality.PROTOCOL,
        engines=ET_ENGINES,
        value_shape="stage=<s> error=<n> planned=<n>",
        has_host_hint=True,
        note="Single code for every ExecuTorch failure site.",
    ),
    _spec(
        error_token(FirmwareErrorCode.OPERATOR_COUNT_EXCEEDS_CAPACITY),
        WireKind.ERROR,
        "The model has more operators than the per-layer record array holds.",
        WireConsumer.TRANSPORT_CONTROL,
        WireCriticality.PROTOCOL,
        engines=ET_ENGINES,
        condition=GATE_NOT_POWER_ONLY,
        value_shape="capacity=<n>",
        runtime_gate="the capacity was actually exceeded during the pass",
        has_host_hint=True,
        note="The firmware parks immediately after printing this, so NO CSV "
        "body follows at all — the pre-#175 claim that rows were merely "
        "truncated described a print that is unreachable (hpx_park() "
        "precedes print_layers()).",
    ),
    _spec(
        error_token(FirmwareErrorCode.PMU_INIT_OR_SELFTEST_FAILED),
        WireKind.ERROR,
        "PMU init or its cycle-counter self-test failed for this pass.",
        WireConsumer.TRANSPORT_CONTROL,
        WireCriticality.PROTOCOL,
        engines=ET_ENGINES,
        condition=GATE_NOT_POWER_ONLY,
        value_shape="pass=<name>",
        has_host_hint=True,
    ),
    _spec(
        error_token(FirmwareErrorCode.STIMER_DEAD),
        WireKind.ERROR,
        "The 32.768 kHz XTAL failed the settle-and-verify probe at "
        "hpx_stimer_init(): the STIMER window clock is dead or implausible. "
        "Emitted BEFORE the window opens so the failure is attributed to the "
        "crystal instead of completing a window into the frozen-clock check "
        "(#110).",
        WireConsumer.TRANSPORT_CONTROL,
        WireCriticality.PROTOCOL,
        # ExecuTorch's engine_clean_window override times the window
        # without hpx_stimer_init; only its busy-loop arm (super()) inherits
        # the base emission. That arm is preflight-rejected at runtime but
        # RENDERED by the census matrix (#171 lesson), so it is declared.
        engines=TFLM_ENGINES | AOT_ENGINES | ET_ENGINES,
        condition=GATE_STIMER_WINDOW,
        engine_conditions={EngineType.EXECUTORCH: GATE_BUSY_LOOP_PROBE},
        value_shape="settle_us=<n>",
        has_host_hint=True,
    ),
)


WARN_SPECS: tuple[WireSpec, ...] = (
    _spec(
        warn_token(FirmwareWarnCode.UNUSUAL_DTYPE),
        WireKind.WARN,
        "A tensor has a dtype the preflight walk did not expect.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        engines=TFLM_ENGINES,
        value_shape="tensor=<n> dtype=<n> name=<s>",
        note="Non-fatal; the run continues. Nothing on the host reads it — "
        "only HPX_ERROR lines are scanned.",
    ),
)

