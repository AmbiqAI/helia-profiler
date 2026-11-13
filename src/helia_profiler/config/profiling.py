"""Profiling configuration — `ProfilingConfig`, its vocabularies and defaults.

Everything here is re-exported from :mod:`helia_profiler.config`, which
remains the public import surface (``from helia_profiler.config import ProfilingConfig`` keeps working).
"""

from __future__ import annotations

from dataclasses import field
from enum import StrEnum
from typing import Any, Literal

from pydantic import ConfigDict, field_validator, model_validator
from pydantic.dataclasses import dataclass as pydantic_dataclass

from ..vocab import Aggregation


class WindowMode(StrEnum):
    """Clean / GPIO-gated end-to-end window sizing.

    ``FIXED`` reuses ``iterations`` for the clean pass (historical
    behaviour).  ``AUTO`` (default) lets the firmware size the gated window
    at runtime from the measured clean-inference time so short models run
    more iterations and big models run fewer — filling a consistent ~1 s
    wall-clock window for ordinary profiling.  External power capture raises
    this to a longer minimum window so host-side GPIO polling and Joulescope
    packet alignment have several seconds to settle.
    """

    FIXED = "fixed"
    AUTO = "auto"


class CleanWindowProbe(StrEnum):
    """What runs inside the clean GPIO-gated window.

    ``INFER`` runs the model.  ``BUSY_LOOP`` is an opt-in bench probe that
    keeps the gate high around a calibrated CPU spin for roughly
    ``window_target_ms`` so bring-up can distinguish wrong gate semantics
    from inference behaviour.
    """

    INFER = "infer"
    BUSY_LOOP = "busy_loop"


class InputFill(StrEnum):
    """What fills the model's inputs before every run.

    ``ZERO`` (default) keeps every existing capture unchanged.  ``LCG`` fills
    each input with a fixed-seed 32-bit linear congruential sequence, so an
    output depends on every weight rather than on biases alone and a missing
    or late weight shows in the output digests (heliaAOT only).  The bytes
    are raw, so ``LCG`` is refused unless every input is int8, uint8 or int16.
    """

    ZERO = "zero"
    LCG = "lcg"


DEFAULT_ITERATIONS = 100
DEFAULT_WARMUP = 5
DEFAULT_WINDOW_MODE = WindowMode.AUTO
DEFAULT_WINDOW_TARGET_MS = 1000
DEFAULT_WINDOW_MIN = 10
DEFAULT_WINDOW_MAX = 500000
DEFAULT_CLEAN_WINDOW_PROBE = CleanWindowProbe.INFER
DEFAULT_AGGREGATION = Aggregation.MEDIAN


@pydantic_dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class ProfilingConfig:
    """PMU capture settings.

    Counter selection is specified via *pmu_counters* — a mapping of
    compute-unit group (``cpu``, ``mve``, ``memory``, ``ethos_npu``) to a
    selection:

    * ``"default"`` — curated set of the most useful counters.
    * ``"all"``     — every counter in the group (multi-pass).
    * ``["NAME", …]`` — explicit counter names.

    The ``ethos_npu`` group samples the Ethos-U NPU's own PMU (NPU cycles,
    MAC activity, SRAM/external bus beats) and requires an NPU-equipped board
    plus ``engine.backend: ethos_u`` (engine.type helia-rt or helia-aot).

    """

    pmu_counters: dict[str, str | list[str]] = field(default_factory=lambda: {"cpu": "default"})
    per_layer: bool = True
    iterations: int = DEFAULT_ITERATIONS
    warmup: int = DEFAULT_WARMUP
    # See :class:`WindowMode`.  Under ``auto`` the firmware chooses the
    # clean-window iteration count at runtime to fill ``window_target_ms`` of
    # wall-time, clamped to ``[window_min, window_max]``, and reports the
    # actual count it ran (``HPX_CLEAN_INFER_COUNT``), which the host divides
    # into the gated energy for per-inference numbers.
    window_mode: WindowMode = DEFAULT_WINDOW_MODE
    window_target_ms: int = DEFAULT_WINDOW_TARGET_MS
    window_min: int = DEFAULT_WINDOW_MIN
    window_max: int = DEFAULT_WINDOW_MAX
    # Optional bench probe for the clean GPIO-gated window — see
    # :class:`CleanWindowProbe`.
    clean_window_probe: CleanWindowProbe = DEFAULT_CLEAN_WINDOW_PROBE
    # Sanity-check diagnostic: when true, the firmware emits an
    # ``HPX_CLEAN_ITER=<n>`` line over the active transport on every iteration
    # of the clean (GPIO-gated power) window.  This proves the device is
    # genuinely looping inferences for the whole measured window rather than
    # stalling/sleeping.  It perturbs the power reading (extra transport
    # traffic inside the gate), so leave it OFF for real measurements.
    clean_window_trace: bool = False
    # Static-power diagnostic: when true, the firmware unconditionally powers
    # and retains the full shared SSRAM array at boot (mirroring AutoDeploy's
    # ns_power_config(bNeedSharedSRAM=true)), even when the model runs entirely
    # from TCM.  Used to measure the SSRAM static/retention contribution to
    # the power floor.  SRAM-resident arena/weights power the array on
    # regardless; this flag forces it on for the TCM case too.
    force_shared_sram: bool = False
    # How per-layer counters are aggregated across profiled iterations — see
    # :class:`Aggregation`.  All methods first reject structurally-invalid
    # samples (uint32-wrap / frozen-zero) and log them.
    aggregation: Aggregation = DEFAULT_AGGREGATION
    # See :class:`InputFill`.
    input_fill: InputFill = InputFill.ZERO
    # heliaAOT with an emitted transfer provider: before the first pass, one
    # extra run outside the counted iterations gives up its waits after two
    # checks, so a dma unit still in flight times out; the provider's recovery
    # (rebind, model_init, one run) is then reported. Every other run keeps
    # the default budget.
    transfer_timeout_probe: bool = False
    # heliaAOT module with a bound stream pool or PSRAM constant arenas,
    # Apollo5 family: every instrumented run starts from a poisoned pool and
    # cold PSRAM constants (each constant arena cleaned and invalidated), so a
    # late or missing copy shows in the outputs and constant reads reach the
    # MSPI controller. This extra maintenance applies to instrumented runs;
    # the clean window follows profiling.dcache. Reports HPX_STREAM_EVIDENCE=1.
    stream_evidence: bool = False
    # Extreme benchmarking mode: power down memory regions the model does not
    # use to lower the energy floor.  Powers down the shared SSRAM array and
    # collapses MRAM to a single bank (NVM0 only).  Only safe when the model
    # weights and arena both live in TCM. Code keeps running from MRAM, so
    # transports (RTT/USB/SWO) and printf remain available throughout the run.
    extreme_mode: bool = False

    #: Data-cache policy for ordinary profiler firmware; off requires Apollo5.
    dcache: Literal["on", "off"] = field(default="on", metadata={"omit_default": True})

    @field_validator("dcache", mode="before")
    @classmethod
    def _normalize_dcache(cls, value: Any) -> Any:
        if isinstance(value, bool):
            return "on" if value else "off"
        return value

    @field_validator("pmu_counters", mode="before")
    @classmethod
    def _normalize_pmu_counters(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized: dict[str, str | list[str]] = {}
        for group, selection in value.items():
            normalized[str(group)] = selection if isinstance(selection, list) else str(selection)
        return normalized

    @model_validator(mode="after")
    def _validate(self) -> ProfilingConfig:
        if self.window_min < 1:
            raise ValueError(f"window_min must be >= 1, got {self.window_min}.")
        if self.window_max < self.window_min:
            raise ValueError(
                f"window_max ({self.window_max}) must be >= window_min ({self.window_min})."
            )
        if self.window_target_ms < 1:
            raise ValueError(f"window_target_ms must be >= 1, got {self.window_target_ms}.")
        if self.extreme_mode and self.force_shared_sram:
            raise ValueError(
                "profiling.extreme_mode and profiling.force_shared_sram are "
                "mutually exclusive: extreme mode powers the shared SSRAM "
                "array down while force_shared_sram forces it on. The "
                "firmware partials each assume the other lever is absent, "
                "and template include order silently decided the winner."
            )
        return self
