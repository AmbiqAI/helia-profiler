"""Typed model of ``summary.json`` — the schema as code (#202 Part A).

One class owns the artifact's shape for BOTH sides of the boundary:

* the **producer** (``report/summary.py``) builds a :class:`RunSummary` and
  serializes it via :meth:`RunSummary.to_dict`, whose emission order and
  omit-when-``None`` conditionality reproduce the historical hand-built dict
  byte-for-byte (``tests/contracts/test_report_golden.py`` is the proof);
* **consumers** (``validation/runner.py`` today; more as they migrate) load
  artifacts through :func:`load_run_summary` / :meth:`RunSummary.from_dict`,
  a *tolerant* reader: missing fields become ``None``, unknown keys are
  preserved on ``extras`` for inspection, and cross-version interpretation
  (legacy key spellings, the #142/#181 drift arbitration) lives in
  properties here instead of being re-derived at every read site.

That split is the point: the three shipped shadow-consumer defects (the
stage/validity window-clock split, #192's phantom compare dimension, #195's
runner misreading v4 artifacts with v3 semantics) all came from consumers
re-deriving semantics the producer never promised. A field's meaning now has
one home.

Layering: this module sits in ``results/`` (bottom of the import graph) and
imports nothing from ``evaluation/`` or ``report/`` — ``evaluation`` imports
``results``, so a ``RunEvaluation`` reference here would cycle (#204 review).

Reader vs writer asymmetry, stated honestly: ``to_dict`` serializes only the
canonical schema — it does NOT re-emit ``extras``. The producer never
populates ``extras``; the reader keeps them so a consumer can inspect a
newer-or-older artifact without this module lying about round-tripping it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

RUN_SUMMARY_SCHEMA = "hpx.run-summary"
#: Version history lives with the shape it versions (re-exported by
#: report/contracts.py for its long-standing import sites):
#: v2: #24 binary.bss; v3: #133 memory_regions owns region truth;
#: v4: #142/#181 gate verdict re-sourced -- energy_per_inference_j can
#: coexist with gate_duration_integrity.valid=false, suspect re-keyed to
#: the observer arbitration, gated_window_reference_drift added.
RUN_SUMMARY_SCHEMA_VERSION = 4

__all__ = [
    "RUN_SUMMARY_SCHEMA",
    "RUN_SUMMARY_SCHEMA_VERSION",
    "BinarySection",
    "LatencySection",
    "MemorySection",
    "PowerSection",
    "RunSummary",
    "load_run_summary",
]


def _put(target: dict[str, Any], key: str, value: Any) -> None:
    """Emit ``key`` unless the value is ``None`` (absent-field convention)."""
    if value is not None:
        target[key] = value


@dataclass(frozen=True)
class MemorySection:
    """``summary["memory"]`` — firmware-reported model memory figures."""

    arena_size: int | None = None
    allocated_arena: int | None = None
    model_size: int | None = None
    num_tensors: int | None = None
    input_size: int | None = None
    output_size: int | None = None
    #: Reader-only: keys a newer producer wrote that this schema predates.
    extras: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        _put(out, "arena_size", self.arena_size)
        _put(out, "allocated_arena", self.allocated_arena)
        _put(out, "model_size", self.model_size)
        _put(out, "num_tensors", self.num_tensors)
        _put(out, "input_size", self.input_size)
        _put(out, "output_size", self.output_size)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MemorySection:
        known = {
            "arena_size",
            "allocated_arena",
            "model_size",
            "num_tensors",
            "input_size",
            "output_size",
        }
        return cls(
            arena_size=_opt_int(data.get("arena_size")),
            allocated_arena=_opt_int(data.get("allocated_arena")),
            model_size=_opt_int(data.get("model_size")),
            num_tensors=_opt_int(data.get("num_tensors")),
            input_size=_opt_int(data.get("input_size")),
            output_size=_opt_int(data.get("output_size")),
            extras={k: v for k, v in data.items() if k not in known},
        )


@dataclass(frozen=True)
class BinarySection:
    """``summary["binary"]`` — ELF section byte totals."""

    text: int
    data: int
    bss: int
    total: int
    #: Emitted only when truthy, matching the historical writer.
    reserved: int | None = None
    extras: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "text": self.text,
            "data": self.data,
            "bss": self.bss,
            "total": self.total,
        }
        if self.reserved:
            out["reserved"] = self.reserved
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BinarySection:
        known = {"text", "data", "bss", "total", "reserved"}
        return cls(
            text=_opt_int(data.get("text")) or 0,
            data=_opt_int(data.get("data")) or 0,
            bss=_opt_int(data.get("bss")) or 0,
            total=_opt_int(data.get("total")) or 0,
            reserved=_opt_int(data.get("reserved")),
            extras={k: v for k, v in data.items() if k not in known},
        )


@dataclass(frozen=True)
class LatencySection:
    """``summary["latency"]`` — host timing plus device-reported windows.

    Field order is emission order. The historical writer had two branches
    (with and without host ``run_metadata.timing``); both emitted the device
    keys in this relative order, so one section covers both.
    """

    #: JSON numbers carried VERBATIM: the producer writes ints for the
    #: device fields, but a tolerant reader must not round what an older or
    #: foreign artifact wrote -- coercion belongs at the consumer.
    capture_duration_s: float | None = None
    hpx_start_latency_s: float | None = None
    protocol_duration_s: float | None = None
    boot_phases_s: Mapping[str, float] | None = None
    device_profiled_infer_count: float | int | None = None
    device_profiled_infer_total_us: float | int | None = None
    device_profiled_infer_avg_us: float | int | None = None
    device_clean_infer_count: float | int | None = None
    device_clean_infer_total_cycles: float | int | None = None
    device_clean_infer_avg_cycles: float | int | None = None
    device_clean_infer_avg_us: float | int | None = None
    device_clean_stalled_iters: float | int | None = None
    device_clean_partial_iters: float | int | None = None
    device_clean_ref_cycles: float | int | None = None
    device_clean_dwt_rate_cyc: float | int | None = None
    device_clean_dwt_rate_us: float | int | None = None
    device_clean_attach_wait_us: float | int | None = None
    extras: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        _put(out, "capture_duration_s", self.capture_duration_s)
        _put(out, "hpx_start_latency_s", self.hpx_start_latency_s)
        _put(out, "protocol_duration_s", self.protocol_duration_s)
        _put(out, "boot_phases_s", self.boot_phases_s)
        _put(out, "device_profiled_infer_count", self.device_profiled_infer_count)
        _put(out, "device_profiled_infer_total_us", self.device_profiled_infer_total_us)
        _put(out, "device_profiled_infer_avg_us", self.device_profiled_infer_avg_us)
        _put(out, "device_clean_infer_count", self.device_clean_infer_count)
        _put(out, "device_clean_infer_total_cycles", self.device_clean_infer_total_cycles)
        _put(out, "device_clean_infer_avg_cycles", self.device_clean_infer_avg_cycles)
        _put(out, "device_clean_infer_avg_us", self.device_clean_infer_avg_us)
        _put(out, "device_clean_stalled_iters", self.device_clean_stalled_iters)
        _put(out, "device_clean_partial_iters", self.device_clean_partial_iters)
        _put(out, "device_clean_ref_cycles", self.device_clean_ref_cycles)
        _put(out, "device_clean_dwt_rate_cyc", self.device_clean_dwt_rate_cyc)
        _put(out, "device_clean_dwt_rate_us", self.device_clean_dwt_rate_us)
        _put(out, "device_clean_attach_wait_us", self.device_clean_attach_wait_us)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LatencySection:
        known = {
            "capture_duration_s",
            "hpx_start_latency_s",
            "protocol_duration_s",
            "boot_phases_s",
            "device_profiled_infer_count",
            "device_profiled_infer_total_us",
            "device_profiled_infer_avg_us",
            "device_clean_infer_count",
            "device_clean_infer_total_cycles",
            "device_clean_infer_avg_cycles",
            "device_clean_infer_avg_us",
            "device_clean_stalled_iters",
            "device_clean_partial_iters",
            "device_clean_ref_cycles",
            "device_clean_dwt_rate_cyc",
            "device_clean_dwt_rate_us",
            "device_clean_attach_wait_us",
        }
        return cls(
            capture_duration_s=_opt_float(data.get("capture_duration_s")),
            hpx_start_latency_s=_opt_float(data.get("hpx_start_latency_s")),
            protocol_duration_s=_opt_float(data.get("protocol_duration_s")),
            boot_phases_s=data.get("boot_phases_s"),
            device_profiled_infer_count=data.get("device_profiled_infer_count"),
            device_profiled_infer_total_us=data.get("device_profiled_infer_total_us"),
            device_profiled_infer_avg_us=data.get("device_profiled_infer_avg_us"),
            device_clean_infer_count=data.get("device_clean_infer_count"),
            device_clean_infer_total_cycles=data.get("device_clean_infer_total_cycles"),
            device_clean_infer_avg_cycles=data.get("device_clean_infer_avg_cycles"),
            device_clean_infer_avg_us=data.get("device_clean_infer_avg_us"),
            device_clean_stalled_iters=data.get("device_clean_stalled_iters"),
            device_clean_partial_iters=data.get("device_clean_partial_iters"),
            device_clean_ref_cycles=data.get("device_clean_ref_cycles"),
            device_clean_dwt_rate_cyc=data.get("device_clean_dwt_rate_cyc"),
            device_clean_dwt_rate_us=data.get("device_clean_dwt_rate_us"),
            device_clean_attach_wait_us=data.get("device_clean_attach_wait_us"),
            extras={k: v for k, v in data.items() if k not in known},
        )

    @property
    def best_latency_avg_us(self) -> float | None:
        """The per-inference latency a consumer should headline.

        Clean-window first (the deliberate measurement), profiled average as
        the fallback — the precedence the validation runner has always
        applied, now stated once.
        """
        if self.device_clean_infer_avg_us is not None:
            return float(self.device_clean_infer_avg_us)
        if self.device_profiled_infer_avg_us is not None:
            return float(self.device_profiled_infer_avg_us)
        return None


@dataclass(frozen=True)
class PowerSection:
    """``summary["power"]`` — instrument metrics plus the gate verdict render.

    Field order is emission order. Nested blobs the schema does not (yet)
    type stay as mappings under their historical keys; they graduate to
    typed sections when a consumer needs their interior.
    """

    avg_current_a: float | None = None
    avg_power_w: float | None = None
    peak_current_a: float | None = None
    energy_j: float | None = None
    capture_duration_s: float | None = None
    measurement_scope: str | None = None
    firmware_code_fingerprint: str | None = None
    observation_mode: str | None = None
    integrity: str | None = None
    gate_failure: Mapping[str, Any] | None = None
    #: Optional[bool]: ``False`` is a real, emitted value (a missing gate
    #: edge); ``None`` means the capture never recorded the field.
    gate_rise_observed: bool | None = None
    gate_fall_observed: bool | None = None
    gpi_poll_count: int | None = None
    stat_packets: int | None = None
    capture_window_s: float | None = None
    gating_diagnostics: Mapping[str, Any] | None = None
    window_clock_ceiling: Mapping[str, Any] | None = None
    terminal: Mapping[str, Any] | None = None
    on_device_summary: Mapping[str, Any] | None = None
    sync_input_index: int | None = None
    gating_method: str | None = None
    power_firmware: str | None = None
    target_lifecycle: Mapping[str, Any] | None = None
    sync: Mapping[str, Any] | None = None
    sync_timing_s: Mapping[str, Any] | None = None
    gate_duration_integrity: Mapping[str, Any] | None = None
    power_plan: Mapping[str, Any] | None = None
    short_gate_pulses_ignored: int | None = None
    short_gate_pulse_diagnostics: Mapping[str, Any] | None = None
    gated_window_count: int | None = None
    per_inference_metrics_omitted: str | None = None
    median_current_a: float | None = None
    p95_current_a: float | None = None
    p99_current_a: float | None = None
    peak_current_p99_a: float | None = None
    median_power_w: float | None = None
    p95_power_w: float | None = None
    p99_power_w: float | None = None
    gated_window_expected_duration_s: float | None = None
    gated_window_duration_ratio: float | None = None
    #: ``True`` or absent — the writer never emits ``False``.
    gated_window_duration_suspect: bool | None = None
    gated_window_reference_drift: str | None = None
    energy_per_inference_j: float | None = None
    inferences_per_joule: float | None = None
    active_window_estimated_duration_s: float | None = None
    active_window_estimated_energy_j: float | None = None
    active_window_estimate_method: str | None = None
    active_window_estimated_energy_per_inference_j: float | None = None
    active_window_estimated_inferences_per_joule: float | None = None
    extras: Mapping[str, Any] = field(default_factory=dict)

    _KNOWN = (
        "avg_current_a",
        "avg_power_w",
        "peak_current_a",
        "energy_j",
        "capture_duration_s",
        "measurement_scope",
        "firmware_code_fingerprint",
        "observation_mode",
        "integrity",
        "gate_failure",
        "gate_rise_observed",
        "gate_fall_observed",
        "gpi_poll_count",
        "stat_packets",
        "capture_window_s",
        "gating_diagnostics",
        "window_clock_ceiling",
        "terminal",
        "on_device_summary",
        "sync_input_index",
        "gating_method",
        "power_firmware",
        "target_lifecycle",
        "sync",
        "sync_timing_s",
        "gate_duration_integrity",
        "power_plan",
        "short_gate_pulses_ignored",
        "short_gate_pulse_diagnostics",
        "gated_window_count",
        "per_inference_metrics_omitted",
        "median_current_a",
        "p95_current_a",
        "p99_current_a",
        "peak_current_p99_a",
        "median_power_w",
        "p95_power_w",
        "p99_power_w",
        "gated_window_expected_duration_s",
        "gated_window_duration_ratio",
        "gated_window_duration_suspect",
        "gated_window_reference_drift",
        "energy_per_inference_j",
        "inferences_per_joule",
        "active_window_estimated_duration_s",
        "active_window_estimated_energy_j",
        "active_window_estimate_method",
        "active_window_estimated_energy_per_inference_j",
        "active_window_estimated_inferences_per_joule",
    )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key in self._KNOWN:
            _put(out, key, getattr(self, key))
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PowerSection:
        known = set(cls._KNOWN)
        kwargs: dict[str, Any] = {key: data.get(key) for key in cls._KNOWN}
        return cls(
            **kwargs,
            extras={k: v for k, v in data.items() if k not in known},
        )

    # -- cross-version interpretation (the runner's legacy fallbacks, once) --

    @property
    def energy_uj(self) -> float | None:
        """Total gated energy in µJ, across three schema generations."""
        for legacy in ("total_energy_uj", "energy_uJ"):
            if legacy in self.extras:
                return float(self.extras[legacy])
        if self.energy_j is not None:
            return float(self.energy_j) * 1e6
        return None

    @property
    def avg_current_ma(self) -> float | None:
        if "avg_current_ma" in self.extras:
            return float(self.extras["avg_current_ma"])
        if self.avg_current_a is not None:
            return float(self.avg_current_a) * 1e3
        return None

    @property
    def avg_power_mw(self) -> float | None:
        if "avg_power_mw" in self.extras:
            return float(self.extras["avg_power_mw"])
        if self.avg_power_w is not None:
            return float(self.avg_power_w) * 1e3
        return None

    @property
    def peak_current_ma(self) -> float | None:
        if "peak_current_ma" in self.extras:
            return float(self.extras["peak_current_ma"])
        if self.peak_current_a is not None:
            return float(self.peak_current_a) * 1e3
        return None

    @property
    def energy_per_inference_uj(self) -> float | None:
        if self.energy_per_inference_j is None:
            return None
        return float(self.energy_per_inference_j) * 1e6

    @property
    def gate_duration_integrity_valid(self) -> bool | None:
        blob = self.gate_duration_integrity
        if isinstance(blob, Mapping) and "valid" in blob:
            return bool(blob["valid"])
        return None

    @property
    def gate_failure_kind(self) -> str | None:
        blob = self.gate_failure
        if isinstance(blob, Mapping) and blob.get("kind") is not None:
            return str(blob["kind"])
        return None

    @property
    def gate_duration_unarbitrated_failure(self) -> bool:
        """The est*count band failed with nothing to reclassify it.

        ``valid: false`` alone stopped meaning "bad capture" at schema v4
        (#142/#181): when the firmware's own window clock confirmed the gate,
        the artifact carries ``gated_window_reference_drift`` and the miss is
        a stale reference. This property is that arbitration, stated once —
        the misreading of it is exactly how the validation runner failed
        healthy cold-boot runs (#195, found by two lenses independently).
        """
        return (
            self.gate_duration_integrity_valid is False
            and self.gated_window_reference_drift is None
        )


@dataclass(frozen=True)
class RunSummary:
    """The ``summary.json`` artifact, typed. Field order is emission order."""

    engine: str
    layers: int
    #: Historically a float (a sum of per-layer cycle floats) -- carried
    #: verbatim; use :attr:`total_cycles_int` for a rounded reading.
    total_cycles: float
    overflow_detected: bool
    validity: str | None = None
    issues: tuple[Mapping[str, Any], ...] = ()
    schema: str = RUN_SUMMARY_SCHEMA
    schema_version: int = RUN_SUMMARY_SCHEMA_VERSION
    compatibility: Mapping[str, Any] | None = None
    dependencies: Mapping[str, Any] | None = None
    top_layers: tuple[Mapping[str, Any], ...] = ()
    memory: MemorySection | None = None
    psram: Mapping[str, Any] | None = None
    memory_plan: Mapping[str, Any] | None = None
    memory_regions: Mapping[str, Any] | None = None
    memory_reconciliation: Mapping[str, Any] | None = None
    binary: BinarySection | None = None
    cache: Mapping[str, Any] | None = None
    model_analysis: Mapping[str, Any] | None = None
    power: PowerSection | None = None
    latency: LatencySection | None = None
    extras: Mapping[str, Any] = field(default_factory=dict)

    @property
    def total_cycles_int(self) -> int | None:
        if not self.total_cycles:
            return None
        return int(self.total_cycles)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "engine": self.engine,
            "layers": self.layers,
            "total_cycles": self.total_cycles,
            "overflow_detected": self.overflow_detected,
        }
        _put(out, "compatibility", self.compatibility)
        _put(out, "dependencies", self.dependencies)
        out["top_layers"] = list(self.top_layers)
        if self.memory is not None:
            memory = self.memory.to_dict()
            if memory:
                out["memory"] = memory
        _put(out, "psram", self.psram)
        _put(out, "memory_plan", self.memory_plan)
        _put(out, "memory_regions", self.memory_regions)
        _put(out, "memory_reconciliation", self.memory_reconciliation)
        if self.binary is not None:
            out["binary"] = self.binary.to_dict()
        _put(out, "cache", self.cache)
        _put(out, "model_analysis", self.model_analysis)
        if self.power is not None:
            out["power"] = self.power.to_dict()
        if self.latency is not None:
            latency = self.latency.to_dict()
            if latency:
                out["latency"] = latency
        _put(out, "validity", self.validity)
        out["issues"] = list(self.issues)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RunSummary:
        known = {
            "schema",
            "schema_version",
            "engine",
            "layers",
            "total_cycles",
            "overflow_detected",
            "compatibility",
            "dependencies",
            "top_layers",
            "memory",
            "psram",
            "memory_plan",
            "memory_regions",
            "memory_reconciliation",
            "binary",
            "cache",
            "model_analysis",
            "power",
            "latency",
            "validity",
            "issues",
        }
        memory = data.get("memory")
        binary = data.get("binary")
        power = data.get("power")
        latency = data.get("latency")
        return cls(
            engine=str(data.get("engine", "")),
            layers=_opt_int(data.get("layers")) or 0,
            total_cycles=_opt_float(data.get("total_cycles")) or 0,
            overflow_detected=bool(data.get("overflow_detected", False)),
            validity=(str(data["validity"]) if data.get("validity") is not None else None),
            issues=tuple(data.get("issues") or ()),
            schema=str(data.get("schema", RUN_SUMMARY_SCHEMA)),
            schema_version=_opt_int(data.get("schema_version")) or 1,
            compatibility=data.get("compatibility"),
            dependencies=data.get("dependencies"),
            top_layers=tuple(data.get("top_layers") or ()),
            memory=(MemorySection.from_dict(memory) if isinstance(memory, Mapping) else None),
            psram=data.get("psram"),
            memory_plan=data.get("memory_plan"),
            memory_regions=data.get("memory_regions"),
            memory_reconciliation=data.get("memory_reconciliation"),
            binary=(BinarySection.from_dict(binary) if isinstance(binary, Mapping) else None),
            cache=data.get("cache"),
            model_analysis=data.get("model_analysis"),
            power=(PowerSection.from_dict(power) if isinstance(power, Mapping) else None),
            latency=(
                LatencySection.from_dict(latency) if isinstance(latency, Mapping) else None
            ),
            extras={k: v for k, v in data.items() if k not in known},
        )


def load_run_summary(path: str | Path) -> RunSummary:
    """Load a ``summary.json`` from disk, tolerantly.

    Raises ``OSError``/``json.JSONDecodeError``/``ValueError`` for an
    unreadable or non-object document — the caller decides what a broken
    artifact means (the validation runner fails the case; a report viewer
    might not).
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"summary.json is not a JSON object: {path}")
    return RunSummary.from_dict(data)


def _opt_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
