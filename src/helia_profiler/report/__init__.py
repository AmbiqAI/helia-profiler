"""Report generation — CSV, JSON, terminal summary, and Model Explorer overlays.

The ``write_report`` function is called by the report stage and dispatches to
the appropriate formatters based on ``OutputConfig``.

Output structure
----------------

Always generated (root of output_dir):
    summary.json          High-level machine-readable summary (cycles, memory,
                          cache highlights, binary sections, top layers).
    profile_results.csv   Merged per-layer profiling results.
    run_metadata.json     Full run metadata (config, toolchain, platform, …).

Model Explorer (``model_explorer/`` subfolder, unless ``--no-model-explorer``):
    me_overlay_<COUNTER>.json   Per-counter overlay files.

Detailed (``detailed/`` subfolder, only with ``--detailed``):
    profile_<preset>.csv        Per-preset CSV breakdowns.
    profile_<group>.csv         Per-group (compute-unit) merged CSVs.
    memory.json                 Memory breakdown (binary sections, arena,
                                per-layer cache counters).
    power_summary.csv           Power capture summary (when available).
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import ReportError
from ..model_analysis import ModelAnalysis
from ..results import BinarySections, LayerResult, PmuResult, RunMetadata

if TYPE_CHECKING:
    from ..pipeline import PipelineContext
    from ..power.base import PowerResult

log = logging.getLogger("hpx")


def _power_summary_to_dict(summary: Any) -> dict[str, Any]:
    return {
        "avg_current_a": summary.avg_current_a,
        "avg_power_w": summary.avg_power_w,
        "peak_current_a": summary.peak_current_a,
        "energy_j": summary.energy_j,
        "capture_duration_s": summary.duration_s,
    }

# Memory-related PMU counter names used for cache/memory summaries.
_CACHE_COUNTERS = (
    "ARM_PMU_L1D_CACHE",
    "ARM_PMU_L1D_CACHE_RD",
    "ARM_PMU_L1D_CACHE_REFILL",
    "ARM_PMU_L1D_CACHE_MISS_RD",
    "ARM_PMU_L1D_CACHE_WB",
    "ARM_PMU_L1D_CACHE_ALLOCATE",
    "ARM_PMU_L1I_CACHE",
    "ARM_PMU_L1I_CACHE_REFILL",
    "ARM_PMU_DTCM_ACCESS",
    "ARM_PMU_ITCM_ACCESS",
    "ARM_PMU_MEM_ACCESS",
    "ARM_PMU_BUS_ACCESS",
    "ARM_PMU_BUS_CYCLES",
)


def write_report(ctx: PipelineContext) -> list[Path]:
    """Generate all configured report outputs.

    Returns a list of paths to the files written.
    """
    assert ctx.pmu_result is not None

    output_dir = ctx.config.output.dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    fmt = ctx.config.output.format
    pmu = ctx.pmu_result
    detailed = ctx.config.output.detailed
    analysis = ctx.model_analysis

    # --- Always: primary profile results ---
    if fmt == "csv":
        p = _write_csv(pmu, output_dir, analysis)
        paths.append(p)
    elif fmt == "json":
        p = _write_json(pmu, ctx.power_result, ctx.run_metadata, output_dir)
        paths.append(p)
    else:
        raise ReportError(f"Unknown output format: '{fmt}'")

    # --- Always: summary.json ---
    p = _write_summary(ctx, output_dir)
    paths.append(p)

    # --- Always: run metadata ---
    p = _write_run_metadata(ctx, output_dir)
    paths.append(p)

    # --- heliaAOT operator manifest (engine-specific) ---
    p = _write_aot_manifest(ctx, output_dir)
    if p is not None:
        paths.append(p)
        p = _write_aot_memory_layers(ctx, output_dir)
        if p is not None:
            paths.append(p)

    # --- Model Explorer overlays → model_explorer/ subfolder ---
    if ctx.config.output.model_explorer:
        try:
            me_dir = output_dir / "model_explorer"
            me_dir.mkdir(parents=True, exist_ok=True)
            _write_model_explorer_overlays(ctx, me_dir, paths)
        except Exception as exc:
            raise ReportError(
                f"Model Explorer overlay generation failed: {exc}",
            ) from exc

    # --- Detailed outputs → detailed/ subfolder ---
    if detailed:
        detail_dir = output_dir / "detailed"
        detail_dir.mkdir(parents=True, exist_ok=True)

        # Per-preset CSV breakdowns
        if len(pmu.presets) > 1:
            for preset_name, pr in pmu.presets.items():
                if preset_name.startswith("_"):
                    continue
                p = _write_preset_csv(preset_name, pr.layers, detail_dir)
                paths.append(p)

        # Per-group (compute-unit) unified CSVs
        if pmu.groups:
            for group_name, group_layers in pmu.groups.items():
                p = _write_preset_csv(group_name, group_layers, detail_dir)
                paths.append(p)

        # Memory breakdown JSON
        p = _write_memory_breakdown(ctx, detail_dir)
        paths.append(p)

        # Power summary CSV
        if ctx.power_result is not None:
            p = _write_power_csv(ctx.power_result, detail_dir)
            paths.append(p)

    return paths


# ---------------------------------------------------------------------------
# summary.json — high-level machine-readable summary (always generated)
# ---------------------------------------------------------------------------


def _write_summary(ctx: PipelineContext, output_dir: Path) -> Path:
    """Write a high-level summary JSON with cycles, memory, cache, and binary info."""
    assert ctx.pmu_result is not None
    pmu = ctx.pmu_result
    meta = pmu.meta
    layers = pmu.layers

    total_cycles = sum(layer.cycles or 0 for layer in layers)
    sorted_layers = sorted(layers, key=lambda l: l.cycles or 0, reverse=True)

    summary: dict[str, Any] = {
        "engine": ctx.config.engine.type.value,
        "layers": len(layers),
        "total_cycles": total_cycles,
        "overflow_detected": pmu.overflow_detected,
    }

    # Top layers by cycles
    summary["top_layers"] = [
        {
            "op": l.op,
            "cycles": l.cycles or 0,
            "pct": round((l.cycles or 0) / total_cycles * 100, 1) if total_cycles else 0,
        }
        for l in sorted_layers[:5]
    ]

    # Memory from firmware meta
    mem: dict[str, Any] = {}
    if meta.arena_size is not None:
        mem["arena_size"] = meta.arena_size
    if meta.allocated_arena is not None:
        mem["allocated_arena"] = meta.allocated_arena
    if meta.model_size is not None:
        mem["model_size"] = meta.model_size
    if meta.num_tensors is not None:
        mem["num_tensors"] = meta.num_tensors
    if meta.input_size is not None:
        mem["input_size"] = meta.input_size
    if meta.output_size is not None:
        mem["output_size"] = meta.output_size
    if mem:
        summary["memory"] = mem

    # Memory plan — engine-agnostic per-region usage
    if ctx.memory_plan is not None:
        summary["memory_plan"] = _serialise_memory_plan(ctx.memory_plan)

    # Binary sections
    if ctx.binary_sections is not None:
        bs = ctx.binary_sections
        summary["binary"] = {
            "text": bs.text,
            "data": bs.data,
            "bss": bs.bss,
            "total": bs.total,
        }

    # Cache / memory counter totals (summed across all layers)
    cache: dict[str, float] = {}
    for layer in layers:
        for cname in _CACHE_COUNTERS:
            if cname in layer.counters:
                cache[cname] = cache.get(cname, 0) + layer.counters[cname]
    if cache:
        # Compute derived metrics
        l1d_accesses = cache.get("ARM_PMU_L1D_CACHE_RD", cache.get("ARM_PMU_L1D_CACHE", 0))
        l1d_misses = cache.get(
            "ARM_PMU_L1D_CACHE_MISS_RD", cache.get("ARM_PMU_L1D_CACHE_REFILL", 0)
        )
        if l1d_accesses > 0:
            cache["l1d_hit_rate_pct"] = round((1 - l1d_misses / l1d_accesses) * 100, 2)
        summary["cache"] = cache

    # Model analysis — MACs, OPS, TOPS
    if ctx.model_analysis is not None:
        ma = ctx.model_analysis
        analysis_dict: dict[str, Any] = {
            "total_macs": ma.total_macs,
            "total_ops": ma.total_ops,
            "num_parameters": ma.num_parameters,
        }
        if total_cycles > 0 and ma.total_ops > 0:
            analysis_dict["cycles_per_mac"] = (
                round(total_cycles / ma.total_macs, 2) if ma.total_macs else None
            )
            analysis_dict["cycles_per_op"] = round(total_cycles / ma.total_ops, 2)
        summary["model_analysis"] = analysis_dict

    # Power summary
    if ctx.power_result is not None:
        ps = ctx.power_result.summary
        power_meta = ctx.power_result.metadata
        measurement_scope = power_meta.get("measurement_scope", "whole_capture_window")
        summary["power"] = _power_summary_to_dict(ps)
        summary["power"]["measurement_scope"] = measurement_scope
        # High-level summaries report only the gated (inference) portion. The
        # non-inference whole-capture window is annotated in the detailed power
        # CSV, not here, so users compare like-for-like inference energy.
        if power_meta.get("sync_input_index") is not None:
            summary["power"]["sync_input_index"] = power_meta["sync_input_index"]
        if power_meta.get("gating_method") is not None:
            summary["power"]["gating_method"] = power_meta["gating_method"]
        if power_meta.get("target_lifecycle") is not None:
            summary["power"]["target_lifecycle"] = power_meta["target_lifecycle"]
        if power_meta.get("sync") is not None:
            summary["power"]["sync"] = power_meta["sync"]
        if power_meta.get("sync_timing_s") is not None:
            summary["power"]["sync_timing_s"] = power_meta["sync_timing_s"]
        if ctx.power_result.gated_windows:
            summary["power"]["gated_window_count"] = len(ctx.power_result.gated_windows)
        meta = ctx.pmu_result.meta if ctx.pmu_result is not None else None
        if measurement_scope == "gpio_gated_clean_window":
            if ctx.power_result.gated_windows:
                gw = ctx.power_result.gated_windows[0]
                # Spike-robust distribution: a lone transient sample cannot
                # define the headline current/power. The peak is reported both
                # as the raw max and as the p99 of per-packet maxima.
                summary["power"]["median_current_a"] = round(gw.median_current_a, 9)
                summary["power"]["p95_current_a"] = round(gw.p95_current_a, 9)
                summary["power"]["p99_current_a"] = round(gw.p99_current_a, 9)
                summary["power"]["peak_current_p99_a"] = round(gw.peak_current_p99_a, 9)
                summary["power"]["median_power_w"] = round(gw.median_power_w, 9)
                summary["power"]["p95_power_w"] = round(gw.p95_power_w, 9)
                summary["power"]["p99_power_w"] = round(gw.p99_power_w, 9)
            if meta and meta.clean_infer_count and meta.clean_infer_count > 0:
                energy_per_infer = ps.energy_j / meta.clean_infer_count
                summary["power"]["energy_per_inference_j"] = round(energy_per_infer, 9)
                if energy_per_infer > 0:
                    summary["power"]["inferences_per_joule"] = round(
                        1.0 / energy_per_infer,
                        6,
                    )
                # Sanity-check the gated window's ACTUAL measured duration
                # against what clean_infer_count inferences should take,
                # based on the firmware's own reported per-inference time.
                # energy_per_inference_j always divides by the firmware's
                # precisely-known clean_infer_count (not by an inferred count
                # from the measured duration) because that count is exact;
                # the Joulescope-side gated-window duration is the more
                # failure-prone side of this equation. If the instrument's
                # gated capture misses part of the true window (e.g. a
                # GPI-edge/timing-alignment fluke), the observed duration
                # will be shorter (or longer) than expected, and dividing
                # correctly-measured-but-incomplete energy by the full count
                # silently produces a WRONG per-inference number with no
                # other symptom. Found 2026-07-02: a UART-transport capture's
                # gated window was ~10% short, understating energy/inference
                # by ~6% with no error raised. This check cannot fix the
                # measurement, only flag it.
                if meta.clean_infer_avg_us and meta.clean_infer_avg_us > 0 and ps.duration_s > 0:
                    expected_duration_s = (
                        meta.clean_infer_count * meta.clean_infer_avg_us
                    ) / 1_000_000.0
                    duration_ratio = (
                        ps.duration_s / expected_duration_s if expected_duration_s > 0 else 0.0
                    )
                    summary["power"]["gated_window_expected_duration_s"] = round(
                        expected_duration_s, 6
                    )
                    summary["power"]["gated_window_duration_ratio"] = round(duration_ratio, 4)
                    # Allow up to half an inference's worth of slack either
                    # way before flagging -- normal GPIO-edge/packet-boundary
                    # jitter is well under this.
                    tolerance = 0.5 / meta.clean_infer_count
                    if abs(duration_ratio - 1.0) > tolerance:
                        summary["power"]["gated_window_duration_suspect"] = True
                        log.warning(
                            "Joulescope gated window duration (%.4fs) does not match "
                            "clean_infer_count=%d x clean_infer_avg_us=%dus (expected "
                            "%.4fs, ratio=%.3f) -- energy_per_inference_j may be "
                            "systematically wrong. A truncated or extended capture "
                            "window divided by the full inference count silently "
                            "biases this number; check the gated-window capture for "
                            "missed GPIO edges.",
                            ps.duration_s,
                            meta.clean_infer_count,
                            meta.clean_infer_avg_us,
                            expected_duration_s,
                            duration_ratio,
                        )
                elif meta.clean_infer_avg_cycles is not None or meta.clean_infer_avg_us is not None:
                    # The firmware counted clean_infer_count > 0 inferences but
                    # reported a zero (or missing) avg cycle/us figure -- an
                    # inference cannot take zero time, so this means the
                    # device-side DWT-based clean-window measurement was
                    # corrupted (known cause: a debugger/RTT attach racing the
                    # one-shot DWT->CYCCNT read, freezing/resetting it mid-
                    # window -- see main.cc.j2's warmup-phase workaround
                    # comment for the same underlying race). Previously this
                    # silently skipped the duration sanity check entirely
                    # (leaving gated_window_duration_ratio absent with no
                    # warning) instead of flagging the bad reading. Found
                    # 2026-07-03 while validating an ITCM placement
                    # experiment. The Joulescope-measured energy/power numbers
                    # themselves are NOT affected (they don't depend on
                    # device-reported cycles), only this specific duration
                    # cross-check is unavailable.
                    summary["power"]["gated_window_duration_suspect"] = True
                    log.warning(
                        "Device reported clean_infer_count=%d but "
                        "clean_infer_avg_cycles=%r / clean_infer_avg_us=%r -- "
                        "an inference cannot take zero time, so the device-side "
                        "clean-window cycle measurement was likely corrupted "
                        "(e.g. a debugger/RTT attach racing the one-shot DWT "
                        "read). The duration-consistency sanity check could not "
                        "run; energy_per_inference_j itself is unaffected (it "
                        "only depends on Joulescope-measured energy and the "
                        "exact clean_infer_count), but device-reported timing "
                        "diagnostics for this run should not be trusted.",
                        meta.clean_infer_count,
                        meta.clean_infer_avg_cycles,
                        meta.clean_infer_avg_us,
                    )
        elif meta and meta.profiled_infer_total_us is not None:
            active_duration_s = meta.profiled_infer_total_us / 1_000_000.0
            summary["power"]["active_window_estimated_duration_s"] = round(active_duration_s, 6)
            summary["power"]["active_window_estimated_energy_j"] = round(
                ps.avg_power_w * active_duration_s,
                9,
            )
            summary["power"]["active_window_estimate_method"] = (
                "scaled from whole-window average power using device-profiled inference time; "
                "not instrument-GPIO-gated"
            )
            if meta.profiled_infer_count and meta.profiled_infer_count > 0:
                energy_per_infer = (ps.avg_power_w * active_duration_s) / meta.profiled_infer_count
                summary["power"]["active_window_estimated_energy_per_inference_j"] = round(
                    energy_per_infer,
                    9,
                )
                if energy_per_infer > 0:
                    summary["power"]["active_window_estimated_inferences_per_joule"] = round(
                        1.0 / energy_per_infer,
                        6,
                    )

    if ctx.run_metadata.timing is not None:
        timing = {}
        if ctx.run_metadata.timing.capture_duration_s is not None:
            timing["capture_duration_s"] = round(ctx.run_metadata.timing.capture_duration_s, 6)
        if ctx.run_metadata.timing.hpx_start_latency_s is not None:
            timing["hpx_start_latency_s"] = round(ctx.run_metadata.timing.hpx_start_latency_s, 6)
        if ctx.run_metadata.timing.protocol_duration_s is not None:
            timing["protocol_duration_s"] = round(ctx.run_metadata.timing.protocol_duration_s, 6)
        if ctx.run_metadata.timing.phases:
            timing["boot_phases_s"] = ctx.run_metadata.timing.phases
        if meta.profiled_infer_count is not None:
            timing["device_profiled_infer_count"] = meta.profiled_infer_count
        if meta.profiled_infer_total_us is not None:
            timing["device_profiled_infer_total_us"] = meta.profiled_infer_total_us
        if meta.profiled_infer_avg_us is not None:
            timing["device_profiled_infer_avg_us"] = meta.profiled_infer_avg_us
        if meta.clean_infer_count is not None:
            timing["device_clean_infer_count"] = meta.clean_infer_count
        if meta.clean_infer_total_cycles is not None:
            timing["device_clean_infer_total_cycles"] = meta.clean_infer_total_cycles
        if meta.clean_infer_avg_cycles is not None:
            timing["device_clean_infer_avg_cycles"] = meta.clean_infer_avg_cycles
        if meta.clean_infer_avg_us is not None:
            timing["device_clean_infer_avg_us"] = meta.clean_infer_avg_us
        if timing:
            summary["latency"] = timing
    elif any(
        value is not None
        for value in (
            meta.profiled_infer_count,
            meta.profiled_infer_total_us,
            meta.profiled_infer_avg_us,
            meta.clean_infer_count,
            meta.clean_infer_avg_cycles,
            meta.clean_infer_avg_us,
        )
    ):
        summary["latency"] = {
            key: value
            for key, value in {
                "device_profiled_infer_count": meta.profiled_infer_count,
                "device_profiled_infer_total_us": meta.profiled_infer_total_us,
                "device_profiled_infer_avg_us": meta.profiled_infer_avg_us,
                "device_clean_infer_count": meta.clean_infer_count,
                "device_clean_infer_total_cycles": meta.clean_infer_total_cycles,
                "device_clean_infer_avg_cycles": meta.clean_infer_avg_cycles,
                "device_clean_infer_avg_us": meta.clean_infer_avg_us,
            }.items()
            if value is not None
        }

    # Compute TOPS/W if both model analysis and power data are available
    if ctx.model_analysis is not None and ctx.power_result is not None:
        ma = ctx.model_analysis
        ps = ctx.power_result.summary
        if ps.avg_power_w and ps.avg_power_w > 0 and ps.duration_s and ps.duration_s > 0:
            infer_count = 1
            if (
                ctx.power_result.metadata.get("measurement_scope") == "gpio_gated_clean_window"
                and ctx.pmu_result is not None
                and ctx.pmu_result.meta.clean_infer_count
            ):
                infer_count = ctx.pmu_result.meta.clean_infer_count
            tops = (ma.total_ops * infer_count) / 1e12 / ps.duration_s
            tops_per_watt = tops / ps.avg_power_w
            summary.setdefault("model_analysis", {})["tops"] = round(tops, 6)
            summary.setdefault("model_analysis", {})["tops_per_watt"] = round(tops_per_watt, 6)

    out_path = output_dir / "summary.json"
    out_path.write_text(json.dumps(summary, indent=2, default=str))
    log.info("Wrote summary: %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# memory.json — detailed memory breakdown (detailed/ only)
# ---------------------------------------------------------------------------


def _serialise_memory_plan(plan: Any) -> dict[str, Any]:
    """Serialise a ``MemoryPlan`` into a JSON-friendly dict."""
    return {
        "engine": plan.engine,
        "model_weight_bytes": plan.model_weight_bytes,
        "has_overflow": plan.has_overflow,
        "regions": [
            {
                "region": r.region,
                "capacity": r.capacity,
                "used": r.used,
                "free": r.free,
                "overflow": r.overflow,
                "consumers": [
                    {"name": c.name, "size": c.size, "kind": c.kind} for c in r.consumers
                ],
            }
            for r in plan.regions
        ],
    }


def _write_memory_breakdown(ctx: PipelineContext, detail_dir: Path) -> Path:
    """Write detailed memory breakdown: binary sections, arena, per-layer cache."""
    assert ctx.pmu_result is not None
    pmu = ctx.pmu_result
    meta = pmu.meta
    layers = pmu.layers

    data: dict[str, Any] = {}

    # Binary sections
    if ctx.binary_sections is not None:
        bs = ctx.binary_sections
        data["binary_sections"] = {
            "text": bs.text,
            "data": bs.data,
            "bss": bs.bss,
            "total": bs.total,
        }

    # Arena / tensor info from firmware meta
    arena: dict[str, Any] = {}
    if meta.arena_size is not None:
        arena["arena_size"] = meta.arena_size
    if meta.allocated_arena is not None:
        arena["allocated_arena"] = meta.allocated_arena
    if meta.num_tensors is not None:
        arena["num_tensors"] = meta.num_tensors
    if meta.num_inputs is not None:
        arena["num_inputs"] = meta.num_inputs
    if meta.num_outputs is not None:
        arena["num_outputs"] = meta.num_outputs
    if meta.model_size is not None:
        arena["model_size"] = meta.model_size
    if arena:
        data["arena"] = arena

    # Memory plan — engine-agnostic per-region usage
    if ctx.memory_plan is not None:
        data["memory_plan"] = _serialise_memory_plan(ctx.memory_plan)

    # Per-layer cache/memory counters
    per_layer: list[dict[str, Any]] = []
    for layer in layers:
        row: dict[str, Any] = {"op": layer.op}
        layer_cache = {k: v for k, v in layer.counters.items() if k in _CACHE_COUNTERS}
        if layer_cache:
            row["counters"] = layer_cache
            per_layer.append(row)
    if per_layer:
        data["per_layer_memory"] = per_layer

    # Aggregate cache totals
    totals: dict[str, float] = {}
    for layer in layers:
        for cname in _CACHE_COUNTERS:
            if cname in layer.counters:
                totals[cname] = totals.get(cname, 0) + layer.counters[cname]
    if totals:
        l1d_accesses = totals.get("ARM_PMU_L1D_CACHE_RD", totals.get("ARM_PMU_L1D_CACHE", 0))
        l1d_misses = totals.get(
            "ARM_PMU_L1D_CACHE_MISS_RD", totals.get("ARM_PMU_L1D_CACHE_REFILL", 0)
        )
        if l1d_accesses > 0:
            totals["l1d_hit_rate_pct"] = round((1 - l1d_misses / l1d_accesses) * 100, 2)
        data["cache_totals"] = totals

    out_path = detail_dir / "memory.json"
    out_path.write_text(json.dumps(data, indent=2, default=str))
    log.info("Wrote memory breakdown: %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# heliaAOT operator manifest persistence
# ---------------------------------------------------------------------------


def _write_aot_manifest(ctx: PipelineContext, output_dir: Path) -> Path | None:
    """Persist the heliaAOT operator manifest into the report directory.

    The manifest captures exactly which operators the AOT compiler emitted
    (after fusion / DCE / etc.) together with input/output tensor shapes
    and dtypes — so a post-run consumer can line the CSV layer rows up
    with the actual compiled graph.  Silently no-ops for non-AOT engines
    or when the manifest is empty.
    """
    artifacts = getattr(ctx, "engine_artifacts", None)
    if artifacts is None:
        return None
    manifest = artifacts.aot_op_manifest
    if not manifest:
        return None
    out_path = output_dir / "aot_operator_manifest.json"
    out_path.write_text(json.dumps(manifest, indent=2, default=str))
    log.info("Wrote AOT operator manifest: %s", out_path)
    return out_path


def _write_aot_memory_layers(ctx: PipelineContext, output_dir: Path) -> Path | None:
    """Write a flat per-layer/per-buffer AOT placement CSV.

    ``aot_operator_manifest.json`` is the rich source of truth. This CSV is
    intentionally redundant and spreadsheet-friendly so customers can sort by
    layer, tensor kind, runtime memory, or staged source/destination.
    """

    artifacts = getattr(ctx, "engine_artifacts", None)
    if artifacts is None or not artifacts.aot_op_manifest:
        return None

    rows: list[dict[str, Any]] = []
    for op in artifacts.aot_op_manifest:
        for group, tensor_role in (
            ("inputs", "input"),
            ("outputs", "output"),
            ("local_tensors", "local"),
        ):
            for tensor in op.get(group, []) or []:
                if not isinstance(tensor, dict):
                    continue
                rows.append(
                    {
                        "layer_idx": op.get("idx"),
                        "layer_id": op.get("id"),
                        "op_type": op.get("op_type"),
                        "op_name": op.get("name"),
                        "tensor_role": tensor_role,
                        "tensor_id": tensor.get("id"),
                        "tensor_name": tensor.get("name"),
                        "tensor_kind": tensor.get("kind"),
                        "memory": tensor.get("memory"),
                        "source_memory": tensor.get("source_memory"),
                        "staged": tensor.get("staged"),
                        "arena_role": tensor.get("arena_role"),
                        "arena_region_id": tensor.get("arena_region_id"),
                        "offset": tensor.get("offset"),
                        "size": tensor.get("allocation_size", tensor.get("nbytes", tensor.get("size"))),
                        "shape": json.dumps(tensor.get("shape")) if tensor.get("shape") is not None else "",
                    }
                )

    if not rows:
        return None

    out_path = output_dir / "aot_memory_layers.csv"
    fieldnames = [
        "layer_idx",
        "layer_id",
        "op_type",
        "op_name",
        "tensor_role",
        "tensor_id",
        "tensor_name",
        "tensor_kind",
        "memory",
        "source_memory",
        "staged",
        "arena_role",
        "arena_region_id",
        "offset",
        "size",
        "shape",
    ]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    log.info("Wrote AOT memory placement CSV: %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# Core CSV / JSON writers
# ---------------------------------------------------------------------------


def _layer_to_flat_dict(
    layer: LayerResult,
    analysis: ModelAnalysis | None = None,
    total_cycles: float | None = None,
) -> dict[str, Any]:
    """Flatten a LayerResult into a CSV-friendly dict."""
    row: dict[str, Any] = {"id": layer.id, "op": layer.op}
    row.update(layer.counters)
    if layer.cycles is not None:
        row["cycles"] = layer.cycles
    if total_cycles is not None:
        if layer.cycles is None or total_cycles <= 0:
            row["cycles_pct"] = None
        else:
            row["cycles_pct"] = round(layer.cycles / total_cycles * 100, 1)
    row["overflow"] = layer.overflow

    # Enrich with model analysis data when available
    if analysis is not None:
        layer_idx = int(layer.id) if isinstance(layer.id, (int, float)) else None
        if layer_idx is not None and 0 <= layer_idx < len(analysis.layers):
            la = analysis.layers[layer_idx]
            row["macs"] = la.macs
            row["ops"] = la.ops
            if la.macs > 0 and layer.cycles:
                row["cycles_per_mac"] = round(layer.cycles / la.macs, 2)

    return row


def _write_csv(
    pmu: PmuResult,
    output_dir: Path,
    analysis: ModelAnalysis | None = None,
) -> Path:
    """Write merged per-layer profiling results as CSV."""
    layers = pmu.layers
    if not layers:
        raise ReportError("No layer data to write.")

    out_path = output_dir / "profile_results.csv"
    total_cycles = sum(layer.cycles or 0 for layer in layers)
    rows = [_layer_to_flat_dict(layer, analysis, total_cycles) for layer in layers]
    fieldnames = list(rows[0].keys())
    # Ensure enriched columns appear even if first row lacks them
    if analysis is not None:
        for col in ("macs", "ops", "cycles_per_mac"):
            if col not in fieldnames:
                fieldnames.append(col)

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    log.info("Wrote CSV report: %s (%d layers)", out_path, len(layers))
    return out_path


def _write_preset_csv(
    preset_name: str,
    layers: list[LayerResult],
    output_dir: Path,
) -> Path:
    """Write per-layer results for a single PMU preset as CSV."""
    out_path = output_dir / f"profile_{preset_name}.csv"
    if not layers:
        return out_path

    total_cycles = sum(layer.cycles or 0 for layer in layers)
    rows = [_layer_to_flat_dict(layer, total_cycles=total_cycles) for layer in layers]
    fieldnames = list(rows[0].keys())

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    log.info("Wrote preset CSV: %s (%d layers)", out_path, len(layers))
    return out_path


def _write_json(
    pmu: PmuResult,
    power: PowerResult | None,
    run_metadata: RunMetadata,
    output_dir: Path,
) -> Path:
    """Write full profiling results as JSON."""
    out_path = output_dir / "profile_results.json"
    total_cycles = sum(layer.cycles or 0 for layer in pmu.layers)
    preset_totals = {
        name: sum(layer.cycles or 0 for layer in pr.layers) for name, pr in pmu.presets.items()
    }

    data: dict[str, Any] = {
        "metadata": _metadata_to_dict(run_metadata),
        "summary": _firmware_meta_to_dict(pmu.meta),
        "layers": [_layer_to_flat_dict(l, total_cycles=total_cycles) for l in pmu.layers],
        "presets": {
            name: {
                "layers": [
                    _layer_to_flat_dict(l, total_cycles=preset_totals[name])
                    for l in pr.layers
                ],
                "iteration_count": len(pr.iterations),
            }
            for name, pr in pmu.presets.items()
        },
        "overflow_detected": pmu.overflow_detected,
    }

    if power is not None:
        data["power"] = {
            "avg_current_a": power.summary.avg_current_a,
            "avg_power_w": power.summary.avg_power_w,
            "peak_current_a": power.summary.peak_current_a,
            "energy_j": power.summary.energy_j,
            "duration_s": power.summary.duration_s,
            "sample_count": power.summary.sample_count,
        }

    out_path.write_text(json.dumps(data, indent=2, default=str))
    log.info("Wrote JSON report: %s", out_path)
    return out_path


def _write_power_csv(power: PowerResult, output_dir: Path) -> Path:
    """Write power summary as a separate CSV.

    The high-level summary.json reports only the gated (inference) portion. This
    detailed CSV is the place where the non-inference whole-capture window is
    annotated, so it carries both the gated metrics and, when available, the
    ``whole_capture_window`` rows for reference.
    """
    out_path = output_dir / "power_summary.csv"
    summary = power.summary
    meta = power.metadata or {}
    scope = meta.get("measurement_scope", "whole_capture_window")
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["scope", "metric", "value"])
        writer.writerow([scope, "avg_current_a", summary.avg_current_a])
        writer.writerow([scope, "avg_power_w", summary.avg_power_w])
        writer.writerow([scope, "peak_current_a", summary.peak_current_a])
        writer.writerow([scope, "energy_j", summary.energy_j])
        writer.writerow([scope, "duration_s", summary.duration_s])
        writer.writerow([scope, "sample_count", summary.sample_count])

        # Per-window detail for gated captures.
        for i, w in enumerate(power.gated_windows):
            writer.writerow([f"gated_window_{i}", "start_s", w.start_s])
            writer.writerow([f"gated_window_{i}", "duration_s", w.duration_s])
            writer.writerow([f"gated_window_{i}", "energy_j", w.energy_j])
            writer.writerow([f"gated_window_{i}", "charge_c", w.charge_c])
            writer.writerow([f"gated_window_{i}", "avg_current_a", w.avg_current_a])
            writer.writerow([f"gated_window_{i}", "avg_power_w", w.avg_power_w])
            writer.writerow([f"gated_window_{i}", "peak_current_a", w.peak_current_a])
            writer.writerow([f"gated_window_{i}", "sample_count", w.sample_count])
            # Spike-robust distribution from per-packet stats.
            writer.writerow([f"gated_window_{i}", "median_current_a", w.median_current_a])
            writer.writerow([f"gated_window_{i}", "p95_current_a", w.p95_current_a])
            writer.writerow([f"gated_window_{i}", "p99_current_a", w.p99_current_a])
            writer.writerow([f"gated_window_{i}", "peak_current_p99_a", w.peak_current_p99_a])
            writer.writerow([f"gated_window_{i}", "median_power_w", w.median_power_w])
            writer.writerow([f"gated_window_{i}", "p95_power_w", w.p95_power_w])
            writer.writerow([f"gated_window_{i}", "p99_power_w", w.p99_power_w])

        # Non-inference reference: whole captured window (annotation only).
        whole = meta.get("whole_capture_summary")
        if isinstance(whole, dict):
            for key in (
                "avg_current_a",
                "avg_power_w",
                "peak_current_a",
                "energy_j",
                "duration_s",
                "sample_count",
            ):
                if key in whole:
                    writer.writerow(["whole_capture_window", key, whole[key]])
    log.info("Wrote power summary: %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------


def _firmware_meta_to_dict(meta: Any) -> dict[str, Any]:
    """Convert FirmwareMeta to a JSON-safe dict, dropping None values."""
    from ..results import FirmwareMeta

    if isinstance(meta, FirmwareMeta):
        return {k: v for k, v in asdict(meta).items() if v is not None}
    return {}


def _metadata_to_dict(meta: RunMetadata) -> dict[str, Any]:
    """Convert RunMetadata to a JSON-safe dict."""
    d: dict[str, Any] = {
        "hpx_version": meta.hpx_version,
        "run_id": meta.run_id,
        "timestamp": meta.timestamp,
    }
    if meta.config_snapshot:
        d["config"] = meta.config_snapshot
    if meta.platform is not None:
        d["platform"] = asdict(meta.platform)
    if meta.model is not None:
        d["model"] = asdict(meta.model)
    if meta.toolchain is not None:
        d["toolchain"] = asdict(meta.toolchain)
    if meta.timing is not None:
        d["timing"] = {k: v for k, v in asdict(meta.timing).items() if v is not None}
    return d


def _write_run_metadata(ctx: PipelineContext, output_dir: Path) -> Path:
    """Write run metadata (config, toolchain, model info, etc.) as JSON."""
    out_path = output_dir / "run_metadata.json"

    meta_dict = _metadata_to_dict(ctx.run_metadata)

    # Enrich with firmware-reported values from the capture
    if ctx.pmu_result is not None:
        meta_dict["firmware"] = _firmware_meta_to_dict(ctx.pmu_result.meta)
    if ctx.power_result is not None:
        lifecycle = ctx.power_result.metadata.get("target_lifecycle")
        if lifecycle is not None:
            meta_dict["target_lifecycle"] = lifecycle

    out_path.write_text(json.dumps(meta_dict, indent=2, default=str))
    log.info("Wrote run metadata: %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# Model Explorer overlays → model_explorer/ subfolder
# ---------------------------------------------------------------------------


def _write_model_explorer_overlays(
    ctx: PipelineContext,
    me_dir: Path,
    paths: list[Path],
) -> None:
    """Build and save Model Explorer overlay files from PMU data."""
    from .model_explorer import build_multi_metric_overlays

    assert ctx.pmu_result is not None
    layers = ctx.pmu_result.layers
    if not layers:
        return

    # Extract per-metric node_key→value dicts from layer data.
    #
    # Model Explorer matches nodes by ID (integer string).  For TFLite
    # models the node ID is the sequential operator index in the graph.
    #
    # AOT firmware emits "TYPE:id" in the Op column (e.g. "CONV_2D:3")
    # where `id` is the original TFLite operator index preserved through
    # AOT transforms.  We extract that suffix as the node key.
    #
    # TFLM firmware emits just the type string (e.g. "CONV_2D").  Since
    # multiple layers can share the same type, we fall back to the
    # sequential layer index, which matches TFLite graph operator order.
    metrics: dict[str, dict[str, float]] = {}
    for layer in layers:
        op_str = str(layer.op) if layer.op else ""
        if ":" in op_str:
            # AOT format — "CONV_2D:3" → use "3" as node key
            node_key = op_str.rsplit(":", 1)[1]
        else:
            # TFLM / generic — use sequential layer index
            node_key = str(layer.id)
        for key, val in layer.counters.items():
            metrics.setdefault(key, {})[node_key] = val

    if not metrics:
        return

    overlays = build_multi_metric_overlays(metrics)
    for metric_name, overlay in overlays.items():
        out_path = me_dir / f"me_overlay_{metric_name}.json"
        overlay.save(out_path)
        paths.append(out_path)
