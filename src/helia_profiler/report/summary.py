"""summary.json — high-level machine-readable summary (always generated)."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .memory import _CACHE_COUNTERS, _serialise_memory_plan
from .power import _power_summary_to_dict
from ..evaluation import evaluate_run

if TYPE_CHECKING:
    from ..pipeline import PipelineContext

log = logging.getLogger("hpx")


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
        if power_meta.get("observation_mode") is not None:
            summary["power"]["observation_mode"] = power_meta["observation_mode"]
        if power_meta.get("integrity") is not None:
            summary["power"]["integrity"] = power_meta["integrity"]
        if power_meta.get("gate_failure") is not None:
            summary["power"]["gate_failure"] = power_meta["gate_failure"]
        if ctx.power_run is not None and ctx.power_run.terminal is not None:
            summary["power"]["terminal"] = asdict(ctx.power_run.terminal)
        if ctx.power_run is not None and ctx.power_run.on_device_summary is not None:
            summary["power"]["on_device_summary"] = asdict(
                ctx.power_run.on_device_summary
            )
        # High-level summaries report only the gated (inference) portion. The
        # non-inference whole-capture window is annotated in the detailed power
        # CSV, not here, so users compare like-for-like inference energy.
        if power_meta.get("sync_input_index") is not None:
            summary["power"]["sync_input_index"] = power_meta["sync_input_index"]
        if power_meta.get("gating_method") is not None:
            summary["power"]["gating_method"] = power_meta["gating_method"]
        if power_meta.get("power_firmware") is not None:
            summary["power"]["power_firmware"] = power_meta["power_firmware"]
        if power_meta.get("target_lifecycle") is not None:
            summary["power"]["target_lifecycle"] = power_meta["target_lifecycle"]
        if power_meta.get("sync") is not None:
            summary["power"]["sync"] = power_meta["sync"]
        if power_meta.get("sync_timing_s") is not None:
            summary["power"]["sync_timing_s"] = power_meta["sync_timing_s"]
        if power_meta.get("gate_duration_integrity") is not None:
            summary["power"]["gate_duration_integrity"] = power_meta[
                "gate_duration_integrity"
            ]
        if power_meta.get("power_plan") is not None:
            summary["power"]["power_plan"] = power_meta["power_plan"]
        if power_meta.get("short_gate_pulses_ignored") is not None:
            summary["power"]["short_gate_pulses_ignored"] = power_meta[
                "short_gate_pulses_ignored"
            ]
        if power_meta.get("short_gate_pulse_diagnostics") is not None:
            summary["power"]["short_gate_pulse_diagnostics"] = power_meta[
                "short_gate_pulse_diagnostics"
            ]
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
                plan_meta = power_meta.get("power_plan")
                effective_count = meta.clean_infer_count
                effective_avg_us = meta.clean_infer_avg_us
                if isinstance(plan_meta, dict) and plan_meta.get("inference_count"):
                    effective_count = int(plan_meta["inference_count"])
                    if plan_meta.get("reference_inference_us"):
                        effective_avg_us = int(plan_meta["reference_inference_us"])
                if effective_avg_us and effective_avg_us > 0 and ps.duration_s > 0:
                    from ..power.diagnostics import GateDurationIntegrity, assess_gate_duration

                    integrity_meta = power_meta.get("gate_duration_integrity")
                    if isinstance(integrity_meta, dict):
                        integrity = GateDurationIntegrity(
                            measured_s=float(integrity_meta["measured_s"]),
                            expected_s=float(integrity_meta["expected_s"]),
                            tolerance_s=float(integrity_meta["tolerance_s"]),
                            minimum_s=float(integrity_meta.get("minimum_s", 0.0)),
                        )
                    else:
                        integrity = assess_gate_duration(
                            measured_s=ps.duration_s,
                            clean_infer_count=effective_count,
                            clean_infer_avg_us=effective_avg_us,
                            stats_rate_hz=ctx.config.power.stats_rate_hz,
                        )
                    summary["power"]["gated_window_expected_duration_s"] = round(
                        integrity.expected_s, 6
                    )
                    summary["power"]["gated_window_duration_ratio"] = round(
                        integrity.ratio, 4
                    )
                    if not integrity.valid:
                        summary["power"]["gated_window_duration_suspect"] = True
                        log.warning(
                            "Joulescope gated window duration (%.4fs) does not match "
                            "clean_infer_count=%d x clean_infer_avg_us=%dus (expected "
                            "%.4fs, ratio=%.3f). Per-inference power metrics are "
                            "suppressed because the denominator is not trustworthy.",
                            ps.duration_s,
                            effective_count,
                            effective_avg_us,
                            integrity.expected_s,
                            integrity.ratio,
                        )
                    else:
                        energy_per_infer = ps.energy_j / effective_count
                        summary["power"]["energy_per_inference_j"] = round(
                            energy_per_infer, 9
                        )
                        if energy_per_infer > 0:
                            summary["power"]["inferences_per_joule"] = round(
                                1.0 / energy_per_infer,
                                6,
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
                    # experiment. Total Joulescope energy/power remains
                    # available, but per-inference metrics cannot be trusted.
                    summary["power"]["gated_window_duration_suspect"] = True
                    log.warning(
                        "Device reported clean_infer_count=%d but "
                        "clean_infer_avg_cycles=%r / clean_infer_avg_us=%r -- "
                        "an inference cannot take zero time, so the device-side "
                        "clean-window cycle measurement was likely corrupted "
                        "(e.g. a debugger/RTT attach racing the one-shot DWT "
                        "read). Per-inference power metrics are suppressed because "
                        "the duration-consistency sanity check could not run.",
                        meta.clean_infer_count,
                        meta.clean_infer_avg_cycles,
                        meta.clean_infer_avg_us,
                    )
        elif (
            measurement_scope != "free_form_capture"
            and meta
            and meta.profiled_infer_total_us is not None
        ):
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
    if (
        ctx.model_analysis is not None
        and ctx.power_result is not None
        and ctx.power_result.metadata.get("measurement_scope") != "free_form_capture"
    ):
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

    evaluation = evaluate_run(ctx)
    summary["validity"] = evaluation.validity.value
    summary["issues"] = [issue.to_dict() for issue in evaluation.issues]

    out_path = output_dir / "summary.json"
    out_path.write_text(json.dumps(summary, indent=2, default=str))
    log.info("Wrote summary: %s", out_path)
    return out_path
