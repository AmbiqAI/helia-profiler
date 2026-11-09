"""Specs for the per-run evidence heliaAOT prints: output and input digests and
the transfer provider's lines."""

from __future__ import annotations

from ._model import (
    AOT_ENGINES,
    GATE_NOT_POWER_ONLY,
    GATE_PMU_PROFILE,
    GATE_TRANSFER_PROBE,
    WireConsumer,
    WireCriticality,
    WireKey,
    WireKind,
    WireSpec,
    _spec,
)

RUN_EVIDENCE_SPECS: tuple[WireSpec, ...] = (
    _spec(
        WireKey.OUTPUT_INDEXED_FNV1A64.wire,
        WireKind.KEY_VALUE_INDEXED,
        "FNV-1a 64 of output <i> after one instrumented run.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.OUTPUT_INDEXED_FNV1A64,
        engines=AOT_ENGINES,
        condition=GATE_NOT_POWER_ONLY,
        value_shape="<16 hex digits>",
        note="One line per output after every instrumented run, so two "
        "captures of one model can be compared run by run.",
    ),
    _spec(
        WireKey.INPUT_INDEXED_FNV1A64.wire,
        WireKind.KEY_VALUE_INDEXED,
        "FNV-1a 64 of input <i> as every run received it.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.INPUT_INDEXED_FNV1A64,
        engines=AOT_ENGINES,
        condition=GATE_NOT_POWER_ONLY,
        value_shape="<16 hex digits>",
        note="Once, after the last pass; profiling.input_fill decides the bytes.",
    ),
    _spec(
        WireKey.OUTPUT_INDEXED_HEX.wire,
        WireKind.KEY_VALUE_INDEXED,
        "The bytes of output <i> after the last instrumented run.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.OUTPUT_INDEXED_HEX,
        engines=AOT_ENGINES,
        condition=GATE_NOT_POWER_ONLY,
        runtime_gate="the output is at most 1024 bytes",
        value_shape="<2 hex digits per byte>",
        note="Once, after the last pass.",
    ),
    _spec(
        WireKey.TRANSFER_REFILLS.wire,
        WireKind.KEY_VALUE,
        "L1D cache refills per transfer unit over its issue window, and per "
        "operator over its run window, in one instrumented run.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.TRANSFER_REFILLS,
        engines=AOT_ENGINES,
        condition=GATE_PMU_PROFILE,
        runtime_gate="the module ships <prefix>_nsx_transfer.h and the pass counts "
        "ARM_PMU_L1D_CACHE_REFILL",
        value_shape="<unit>:<refills>,...;<op>:<refills>,...",
        note="Shows whether the run's reads of PSRAM reached the MSPI controller "
        "rather than the D-cache.",
    ),
    _spec(
        WireKey.TRANSFER_OVERLAP.wire,
        WireKind.KEY_VALUE,
        "Per transfer unit's issue and per operator's run in one instrumented "
        "run: whether the MSPI controller's DMA was in progress, and whether "
        "nsx-psram had reads pending, at both ends.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.TRANSFER_OVERLAP,
        engines=AOT_ENGINES,
        condition=GATE_NOT_POWER_ONLY,
        runtime_gate="the module ships <prefix>_nsx_transfer.h",
        value_shape="<unit>:<dma>/<pending>,...;<op>:<dma>/<pending>,...",
        note="Each flag is 1 only when set at both ends; a failed read of the "
        "DMA-in-progress bit counts as clear.",
    ),
    _spec(
        WireKey.TRANSFER_STATS.wire,
        WireKind.KEY_VALUE,
        "The transfer provider's counts for one instrumented run.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.TRANSFER_STATS,
        engines=AOT_ENGINES,
        condition=GATE_NOT_POWER_ONLY,
        runtime_gate="the module ships <prefix>_nsx_transfer.h",
        value_shape="<issues>,<waits>,<aborts>,<peak_in_flight>,<wait_checks_max>",
        note="Issues, waits and aborts are the run's own; the peak pieces in "
        "flight and the most checks a wait used are the provider's since it "
        "was bound.",
    ),
    _spec(
        WireKey.TRANSFER_RECOVERY.wire,
        WireKind.KEY_VALUE,
        "profiling.transfer_timeout_probe: what the probe run returned and how "
        "the module recovered.",
        WireConsumer.UNCONSUMED,
        WireCriticality.DIAGNOSTIC,
        key=WireKey.TRANSFER_RECOVERY,
        engines=AOT_ENGINES,
        condition=GATE_TRANSFER_PROBE,
        runtime_gate="the module ships <prefix>_nsx_transfer.h; once, after the "
        "first instrumented run",
        value_shape="<status>,<provider>,<bind>,<init>,<run>,<pending> | no_timeout",
        note="After a 301: the probe run's status and provider code, then the "
        "rebind, model_init and recovery run statuses and nsx_psram_pending() "
        "sampled after the probe run; the recovery run's HPX_OUTPUT_<i>_FNV1A64 "
        "lines follow. no_timeout: the probe run returned 0.",
    ),
)
