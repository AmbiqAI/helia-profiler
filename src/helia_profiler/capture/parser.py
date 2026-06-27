"""Parse heliaPROFILER firmware output protocol.

The firmware emits a structured text format over ITM/SWO (serial)::

    --- HPX_START ---
    HPX_MODEL_SIZE=53412
    HPX_ARENA_SIZE=262144
    HPX_ALLOCATED_ARENA=65536
    HPX_NUM_PRESETS=2
    HPX_PRESETS=basic_cpu,memory
    ...
    --- HPX_PRESET basic_cpu ---
    --- HPX_ITER 0 ---
    "Layer","Op","ARM_PMU_CPU_CYCLES","ARM_PMU_INST_RETIRED",...,"overflow"
    0,CONV_2D,12345,100,...,0
    ...
    --- HPX_PRESET memory ---
    --- HPX_ITER 0 ---
    "Layer","Op","ARM_PMU_MEM_ACCESS",...,"overflow"
    ...
    --- HPX_END ---

Supports both single-preset (legacy: no ``--- HPX_PRESET ---`` markers) and
multi-preset formats.  Results from multiple presets are merged by layer
index under the assumption that run-to-run execution is deterministic.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import statistics
from typing import Any

from ..results import FirmwareMeta, LayerResult, PmuResult, PresetResult
from .transport import HPX_PROTOCOL_VERSION

log = logging.getLogger("hpx")

# Columns that carry string identifiers, not numeric values.
_STRING_COLS = frozenset({"Layer", "Op", "tag", "name", "overflow"})

# A per-layer counter at or above this value is treated as a uint32 underflow
# wrap (finish < start), not a real measurement.  Per-layer counters on these
# parts are comfortably below 2^31; a sample this large means the on-device
# subtraction wrapped (the Apollo4 DWT->CYCCNT settling artifact).
_UINT32_WRAP_THRESHOLD = 1 << 31


def parse_firmware_output(
    lines: list[str], aggregation: str = "median"
) -> PmuResult:
    """Parse HPX protocol output into structured profiling data.

    Returns a :class:`PmuResult` with firmware metadata, per-preset breakdowns,
    and merged per-layer results across all presets.

    *aggregation* selects how per-layer counters are reduced across profiled
    iterations: ``"median"`` (default, robust), ``"mean"``, or ``"trimmed"``.
    Structurally-invalid samples (uint32-wrap / frozen-zero) are rejected first.
    """
    meta_kv: dict[str, Any] = {}
    presets: dict[str, _PresetData] = {}
    current_preset: _PresetData | None = None
    in_session = False

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line == "--- HPX_START ---":
            in_session = True
            continue

        if line == "--- HPX_END ---":
            if current_preset is not None:
                current_preset.flush_iteration()
            break

        if not in_session:
            continue

        # HPX_HEARTBEAT lines — progress markers, not data.  We count them
        # and record the last payload so the run summary can expose it, but
        # they must NOT feed into the CSV parser.  Matched before the
        # ``HPX_KEY=value`` regex below because heartbeat lines may contain
        # ``key=value`` pairs after the ``HPX_HEARTBEAT`` prefix.
        if line.startswith("HPX_HEARTBEAT"):
            meta_kv["heartbeat_count"] = meta_kv.get("heartbeat_count", 0) + 1
            meta_kv["last_heartbeat"] = line
            continue

        # HPX_KEY=value metadata lines
        m = re.match(r"^HPX_(\w+)=(.+)$", line)
        if m:
            key = m.group(1).lower()
            val: Any = m.group(2)
            try:
                val = int(val)
            except ValueError:
                pass
            meta_kv[key] = val
            continue

        # --- HPX_PRESET name ---
        m = re.match(r"^--- HPX_PRESET (\S+) ---$", line)
        if m:
            if current_preset is not None:
                current_preset.flush_iteration()
            preset_name = m.group(1)
            current_preset = _PresetData()
            presets[preset_name] = current_preset
            continue

        # Iteration boundary
        m = re.match(r"^--- HPX_ITER (\d+) ---$", line)
        if m:
            # Auto-create a default preset for legacy single-preset streams
            if current_preset is None:
                current_preset = _PresetData()
                presets["_default"] = current_preset
            current_preset.start_iteration()
            continue

        # CSV header or data rows within an iteration
        if current_preset is not None and current_preset.in_iteration:
            current_preset.feed_line(line)

    # Build FirmwareMeta from key-value pairs
    preset_names_str = meta_kv.get("presets", "")
    preset_names = (
        tuple(preset_names_str.split(","))
        if isinstance(preset_names_str, str) and preset_names_str
        else ()
    )
    firmware_meta = FirmwareMeta(
        model_size=meta_kv.get("model_size"),
        arena_size=meta_kv.get("arena_size"),
        allocated_arena=meta_kv.get("allocated_arena"),
        input_size=meta_kv.get("input_size"),
        output_size=meta_kv.get("output_size"),
        num_tensors=meta_kv.get("num_tensors"),
        num_inputs=meta_kv.get("num_inputs"),
        num_outputs=meta_kv.get("num_outputs"),
        num_presets=meta_kv.get("num_presets"),
        system_clock_hz=meta_kv.get("system_clock_hz"),
        profiled_infer_count=meta_kv.get("profiled_infer_count"),
        profiled_infer_total_us=meta_kv.get("profiled_infer_total_us"),
        profiled_infer_avg_us=meta_kv.get("profiled_infer_avg_us"),
        clean_infer_count=meta_kv.get("clean_infer_count"),
        clean_infer_total_cycles=meta_kv.get("clean_infer_total_cycles"),
        clean_infer_avg_cycles=meta_kv.get("clean_infer_avg_cycles"),
        clean_infer_avg_us=meta_kv.get("clean_infer_avg_us"),
        presets=preset_names,
    )

    # Build per-preset typed results
    typed_presets: dict[str, PresetResult] = {}
    for name, pd in presets.items():
        avg_layers = _average_iterations(
            pd.iterations, pd.header or [], aggregation=aggregation
        )
        typed_iters = _raw_iterations_to_typed(pd.iterations, pd.header or [])
        typed_presets[name] = PresetResult(
            name=name,
            header=pd.header or [],
            iterations=typed_iters,
            layers=avg_layers,
        )

    # --- Post-parse validation ---

    # HPX protocol version check
    version = meta_kv.get("version")
    if version is not None and version != HPX_PROTOCOL_VERSION:
        log.warning(
            "HPX protocol version mismatch: firmware=%s, expected=%d. "
            "Results may be incorrectly parsed.",
            version,
            HPX_PROTOCOL_VERSION,
        )

    # Report accumulated parse errors
    total_parse_errors = sum(pd.parse_errors for pd in presets.values())
    if total_parse_errors > 0:
        log.warning(
            "%d parse error(s) in firmware output — results may be unreliable. "
            "Check transport integrity (consider --transport rtt for lossless capture).",
            total_parse_errors,
        )

    # Check iteration consistency within each preset
    for name, pd in presets.items():
        layer_counts = [len(it) for it in pd.iterations]
        if layer_counts and len(set(layer_counts)) > 1:
            log.warning(
                "Preset '%s': inconsistent layer counts across iterations %s — "
                "data may be truncated or corrupted",
                name,
                layer_counts,
            )

    # Merge layers across all presets
    merged_layers = _merge_presets(typed_presets)

    # Build per-group (compute-unit) merged layer sets.
    # Pass names follow the convention ``<group>_<index>`` (e.g. mve_0,
    # mve_1) for the new counter system, or plain preset names for legacy.
    groups = _group_presets(typed_presets)

    # Detect overflow across all presets
    overflow_detected = any(
        layer.overflow
        for pr in typed_presets.values()
        for iteration in pr.iterations
        for layer in iteration
    )

    log.info(
        "Parsed %d preset(s), %d layers per iteration%s",
        len(typed_presets),
        len(merged_layers),
        " [OVERFLOW DETECTED]" if overflow_detected else "",
    )

    return PmuResult(
        meta=firmware_meta,
        presets=typed_presets,
        layers=merged_layers,
        overflow_detected=overflow_detected,
        groups=groups,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class _PresetData:
    """Accumulator for a single PMU preset's iterations."""

    def __init__(self) -> None:
        self.iterations: list[list[dict[str, Any]]] = []
        self.header: list[str] | None = None
        self._current_layers: list[dict[str, Any]] | None = None
        self.in_iteration = False
        self.parse_errors: int = 0  # count of malformed/corrupted rows

    def start_iteration(self) -> None:
        if self._current_layers is not None:
            self.iterations.append(self._current_layers)
        self._current_layers = []
        self.in_iteration = True
        self.header = None  # Reset — first row of each iteration is header

    def flush_iteration(self) -> None:
        if self._current_layers is not None:
            self.iterations.append(self._current_layers)
            self._current_layers = None
        self.in_iteration = False

    def feed_line(self, line: str) -> None:
        if self._current_layers is None:
            return
        if self.header is None:
            reader = csv.reader(io.StringIO(line))
            for row in reader:
                self.header = [c.strip().strip('"') for c in row]
                break
            if self.header is None:
                self.header = [c.strip().strip('"') for c in line.split(",")]
            return

        reader = csv.reader(io.StringIO(line))
        for row in reader:
            if len(row) != len(self.header):
                log.warning(
                    "Malformed CSV row (expected %d fields, got %d): %.200s",
                    len(self.header),
                    len(row),
                    line,
                )
                self.parse_errors += 1
                continue
            layer: dict[str, Any] = {}
            for col, val_str in zip(self.header, row):
                val_str = val_str.strip()
                if col in _STRING_COLS:
                    # String columns: try int for Layer/overflow, else keep string
                    try:
                        layer[col] = int(val_str)
                    except ValueError:
                        layer[col] = val_str
                else:
                    # Numeric PMU counter columns: must be numeric
                    try:
                        layer[col] = int(val_str)
                    except ValueError:
                        try:
                            layer[col] = float(val_str)
                        except ValueError:
                            log.warning(
                                "Non-numeric value in PMU column '%s': %r",
                                col,
                                val_str,
                            )
                            layer[col] = None  # excluded from averaging
                            self.parse_errors += 1
            self._current_layers.append(layer)


def _row_is_frozen(row: dict[str, Any], numeric_cols: list[str]) -> bool:
    """Whether a single per-iteration sample row is a frozen PMU readout.

    A genuine debug-domain freeze (the probe not yet powered / still settling)
    zeroes the *entire* PMU readout for that iteration at once.  A row is
    therefore only "frozen" when **every** numeric counter present in it reads
    ``0`` — a single legitimately-zero counter (e.g. a sparse stall counter on
    a tiny layer) must not trip the detector, or healthy samples get discarded
    and the user sees a confusing false alert.  Returns ``False`` for a row
    with no numeric counters (nothing to judge).
    """
    seen = False
    for col in numeric_cols:
        v = row.get(col)
        if isinstance(v, (int, float)):
            seen = True
            if v != 0:
                return False
    return seen


def _aggregate(vals: list[float], method: str) -> float:
    """Reduce per-iteration samples to a single value via *method*."""
    if not vals:
        return 0.0
    if method == "mean":
        return sum(vals) / len(vals)
    if method == "median":
        return float(statistics.median(vals))
    if method == "trimmed":
        # Drop one low and one high extreme, then mean.  Needs >=3 samples to
        # trim; otherwise fall back to a plain mean.
        if len(vals) >= 3:
            ordered = sorted(vals)[1:-1]
            return sum(ordered) / len(ordered)
        return sum(vals) / len(vals)
    # Unknown method should have been rejected at config load; be safe.
    return float(statistics.median(vals))


def _average_iterations(
    iterations: list[list[dict[str, Any]]],
    header: list[str],
    aggregation: str = "median",
) -> list[LayerResult]:
    """Aggregate numeric columns across iterations, returning typed LayerResult.

    Per ``aggregation`` (``mean`` | ``median`` | ``trimmed``), each per-layer
    counter is reduced across profiled iterations after rejecting
    structurally-invalid samples (uint32-wrap / frozen-zero).  Rejections are
    summarised in a single warning so a corrupted iteration is visible without
    per-layer log spam.
    """
    if not iterations:
        return []

    num_layers = len(iterations[0])
    numeric_cols = [c for c in header if c not in _STRING_COLS]

    total_wrap = 0
    total_frozen = 0

    averaged: list[LayerResult] = []
    for layer_idx in range(num_layers):
        # Collect this layer's sample rows (with their iteration index) so
        # frozen-zero detection can reason about the whole PMU readout per row.
        rows = [
            (it_idx, it[layer_idx])
            for it_idx, it in enumerate(iterations)
            if layer_idx < len(it)
        ]

        if layer_idx < len(iterations[0]):
            first = iterations[0][layer_idx]
            op_name = first.get("Op", first.get("tag", "unknown"))
            layer_id = first.get("Layer", layer_idx)
        else:
            op_name = "unknown"
            layer_id = layer_idx

        # Row-level frozen-zero rejection: drop an iteration only when its
        # *entire* PMU readout was zero.  Keep them when every iteration is
        # frozen (a genuinely-zero layer) so a counter is never silently
        # emptied.
        frozen_iters = {
            it_idx for it_idx, row in rows if _row_is_frozen(row, numeric_cols)
        }
        if len(frozen_iters) >= len(rows):
            frozen_iters = set()
        total_frozen += len(frozen_iters)

        counters: dict[str, float] = {}
        for col in numeric_cols:
            clean: list[float] = []
            raw: list[float] = []
            for it_idx, row in rows:
                v = row.get(col)
                if not isinstance(v, (int, float)):
                    continue
                raw.append(float(v))
                # uint32-wrap is an independent per-counter underflow, judged
                # on the individual value rather than the whole row.
                if v >= _UINT32_WRAP_THRESHOLD:
                    total_wrap += 1
                    continue
                if it_idx in frozen_iters:
                    continue
                clean.append(float(v))
            if not clean:
                # Everything looked invalid — fall back to the raw samples
                # rather than emitting an empty counter.
                clean = raw
            if clean:
                counters[col] = _aggregate(clean, aggregation)

        cycles = counters.get("ARM_PMU_CPU_CYCLES")

        # Propagate overflow flag (true if ANY iteration had overflow)
        overflow_count = sum(
            1
            for it in iterations
            if layer_idx < len(it) and it[layer_idx].get("overflow", 0) not in (0, "0", False)
        )

        averaged.append(
            LayerResult(
                id=layer_id,
                op=op_name,
                counters=counters,
                cycles=cycles,
                overflow=overflow_count > 0,
            )
        )

    if total_wrap or total_frozen:
        log.warning(
            "Rejected %d uint32-wrap value(s) and %d frozen-zero sample row(s) "
            "before %s aggregation (likely debug-probe settling on the first "
            "iterations; counters reflect the surviving samples).",
            total_wrap,
            total_frozen,
            aggregation,
        )

    return averaged


def _raw_iterations_to_typed(
    iterations: list[list[dict[str, Any]]],
    header: list[str],
) -> list[list[LayerResult]]:
    """Convert raw iteration dicts into typed LayerResult lists."""
    numeric_cols = [c for c in header if c not in _STRING_COLS]
    typed: list[list[LayerResult]] = []
    for iteration in iterations:
        layer_list: list[LayerResult] = []
        for row in iteration:
            counters = {
                col: float(row[col])
                for col in numeric_cols
                if col in row and isinstance(row[col], (int, float))
            }
            layer_list.append(
                LayerResult(
                    id=row.get("Layer", 0),
                    op=row.get("Op", row.get("tag", "unknown")),
                    counters=counters,
                    cycles=counters.get("ARM_PMU_CPU_CYCLES"),
                    overflow=row.get("overflow", 0) not in (0, "0", False),
                )
            )
        typed.append(layer_list)
    return typed


def _merge_presets(
    preset_results: dict[str, PresetResult],
) -> list[LayerResult]:
    """Merge averaged layer data from multiple presets into unified rows.

    Each preset contributes its own set of PMU counter columns.  Layers are
    matched by index (assumes deterministic execution across presets).
    """
    if not preset_results:
        return []

    first = next(iter(preset_results.values()))
    base_layers = first.layers
    if not base_layers:
        return []

    # Start with copies of base layer counters
    merged_counters: list[dict[str, float]] = [dict(layer.counters) for layer in base_layers]
    merged_overflow: list[bool] = [layer.overflow for layer in base_layers]

    # Merge in columns from subsequent presets
    for pr in preset_results.values():
        for i, layer in enumerate(pr.layers):
            if i >= len(merged_counters):
                break
            for key, val in layer.counters.items():
                if key not in merged_counters[i]:
                    merged_counters[i][key] = val
            if layer.overflow:
                merged_overflow[i] = True

    # Build final LayerResult list
    result: list[LayerResult] = []
    for i, base in enumerate(base_layers):
        counters = merged_counters[i]
        cycles = counters.get("ARM_PMU_CPU_CYCLES")
        result.append(
            LayerResult(
                id=base.id,
                op=base.op,
                counters=counters,
                cycles=cycles,
                overflow=merged_overflow[i],
            )
        )

    return result


def _infer_group(preset_name: str) -> str:
    """Derive the compute-unit group from a preset/pass name.

    New-style pass names use ``<group>_<index>`` (e.g. ``mve_0``).
    Legacy preset names are mapped directly.
    """
    # New convention: group_index
    m = re.match(r"^([a-z]+)_\d+$", preset_name)
    if m:
        return m.group(1)
    # Legacy preset names map 1:1
    return preset_name


def _group_presets(
    preset_results: dict[str, PresetResult],
) -> dict[str, list[LayerResult]]:
    """Merge presets that belong to the same compute-unit group.

    Returns a dict mapping group name → list of merged LayerResult.
    Multiple passes for the same group (e.g. ``mve_0``, ``mve_1``) are
    merged together so the result contains all counters from all passes.
    """
    # Bucket presets by group
    group_presets: dict[str, dict[str, PresetResult]] = {}
    for name, pr in preset_results.items():
        if name.startswith("_"):
            continue
        group = _infer_group(name)
        group_presets.setdefault(group, {})[name] = pr

    groups: dict[str, list[LayerResult]] = {}
    for group, prs in group_presets.items():
        groups[group] = _merge_presets(prs)

    return groups
