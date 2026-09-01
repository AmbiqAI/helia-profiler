"""Run-level metric rows for ``hpx compare``.

The declared metric table (``_METRIC_FIELDS``: name, summary path, unit,
gating group, direction) and the per-region memory rows expanded from each
summary's ``memory_regions`` block (#206). Extracted from ``compare.py``
when that module crossed the size ceiling; ``compare.py`` re-imports what
its callers expect from it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..results.serde import nested_get, to_float


@dataclass(frozen=True)
class MetricDiff:
    """Run-level metric comparison."""

    name: str
    baseline: Any
    candidate: Any
    delta: float | None = None
    delta_pct: float | None = None
    unit: str = ""
    #: Declared row metadata (#206) -- replaces the name-string hacks that
    #: used to decide gating (``startswith("power.")``) and direction
    #: (``name != "layers"``). Not serialized: compare_summary.json's
    #: metrics[] shape is unchanged.
    group: str | None = None
    lower_is_better: bool = True


@dataclass(frozen=True)
class _MetricField:
    """One declared run-level metric row.

    ``group`` names the metric group whose comparability gate governs the
    row (``"power"``, ``"memory"``) -- ``None`` rows are always emitted.
    Group rows absent on BOTH sides are skipped (a run that measured no
    power has nothing to say about power); one-sided absence still emits,
    so an axis change (a region present on one SoC only) stays visible.
    """

    name: str
    path: tuple[str, ...]
    unit: str
    group: str | None = None
    lower_is_better: bool = True


_METRIC_FIELDS: tuple[_MetricField, ...] = (
    _MetricField("total_cycles", ("total_cycles",), "cycles"),
    _MetricField("device_profiled_infer_avg_us", ("latency", "device_profiled_infer_avg_us"), "us"),
    _MetricField(
        "device_profiled_infer_total_us", ("latency", "device_profiled_infer_total_us"), "us"
    ),
    _MetricField("layers", ("layers",), "", lower_is_better=False),
    _MetricField("binary.text", ("binary", "text"), "bytes"),
    _MetricField("binary.data", ("binary", "data"), "bytes"),
    _MetricField("binary.bss", ("binary", "bss"), "bytes"),
    # Reported alongside bss so a comparison across the #24 boundary shows
    # where the bytes went, instead of a bss row and a total row that
    # contradict each other with nothing to reconcile them.
    _MetricField("binary.reserved", ("binary", "reserved"), "bytes"),
    _MetricField("binary.total", ("binary", "total"), "bytes"),
    _MetricField("memory.arena_size", ("memory", "arena_size"), "bytes"),
    _MetricField("memory.allocated_arena", ("memory", "allocated_arena"), "bytes"),
    _MetricField("memory.model_size", ("memory", "model_size"), "bytes"),
    _MetricField("power.avg_current_a", ("power", "avg_current_a"), "A", group="power"),
    _MetricField("power.avg_power_w", ("power", "avg_power_w"), "W", group="power"),
    _MetricField("power.peak_current_a", ("power", "peak_current_a"), "A", group="power"),
    _MetricField("power.energy_j", ("power", "energy_j"), "J", group="power"),
    _MetricField("power.duration_s", ("power", "duration_s"), "s", group="power"),
    _MetricField(
        "power.energy_per_inference_j", ("power", "energy_per_inference_j"), "J", group="power"
    ),
    _MetricField(
        "power.inferences_per_joule",
        ("power", "inferences_per_joule"),
        "inferences/J",
        group="power",
        lower_is_better=False,
    ),
)

#: Measured per-region rows (#206): the region SET varies by SoC (AP5-family
#: adds ITCM), so these are expanded from each summary's memory_regions
#: block rather than declared statically. Canonical order matches the
#: per-SoC tables in platform/memory_map.py.
_MEMORY_REGION_ORDER: tuple[str, ...] = ("ITCM", "MRAM", "DTCM", "SRAM")
_MEMORY_REGION_FIELDS: tuple[tuple[str, bool], ...] = (
    ("used", True),
    ("free", False),
)


def _compare_metrics(
    base: dict[str, Any],
    cand: dict[str, Any],
    *,
    include_groups: frozenset[str] = frozenset({"power", "memory"}),
) -> list[MetricDiff]:
    metrics: list[MetricDiff] = []
    for spec in _METRIC_FIELDS:
        b = nested_get(base, *spec.path)
        c = nested_get(cand, *spec.path)
        diff = _metric_diff(spec, b, c, include_groups)
        if diff is not None:
            metrics.append(diff)
    metrics.extend(_memory_region_metrics(base, cand, include_groups))
    return metrics


def _metric_diff(
    spec: _MetricField, b: Any, c: Any, include_groups: frozenset[str]
) -> MetricDiff | None:
    if spec.group is not None:
        if spec.group not in include_groups:
            return None
        if b is None and c is None:
            return None
    bf = to_float(b)
    cf = to_float(c)
    delta = None
    delta_pct = None
    if bf is not None and cf is not None:
        delta = cf - bf
        # Relative to the baseline's magnitude: a region's ``free`` is
        # deliberately unclamped (negative = inventory and extent disagree),
        # and a signed divisor would flip the percentage against the delta.
        if bf != 0:
            delta_pct = delta / abs(bf) * 100
    return MetricDiff(
        name=spec.name,
        baseline=b,
        candidate=c,
        delta=delta,
        delta_pct=delta_pct,
        unit=spec.unit,
        group=spec.group,
        lower_is_better=spec.lower_is_better,
    )


def _memory_regions_by_name(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    block = summary.get("memory_regions")
    if not isinstance(block, dict):
        return {}
    regions = block.get("regions")
    if not isinstance(regions, list):
        return {}
    by_name: dict[str, dict[str, Any]] = {}
    for row in regions:
        if not isinstance(row, dict) or row.get("region") is None:
            continue
        # The producer emits one row per region; if a hand-edited artifact
        # repeats a name, the first row is the one the summary lists first.
        by_name.setdefault(str(row["region"]), row)
    return by_name


def _memory_region_metrics(
    base: dict[str, Any], cand: dict[str, Any], include_groups: frozenset[str]
) -> list[MetricDiff]:
    """Per-region used/free rows, gated as the ``memory`` group (#206).

    Regions present on either side render (canonical order first, any
    unknown names after); a region on one side only shows the asymmetry,
    which is an SoC-axis change worth seeing rather than hiding.
    """
    base_regions = _memory_regions_by_name(base)
    cand_regions = _memory_regions_by_name(cand)
    names = [name for name in _MEMORY_REGION_ORDER if name in base_regions or name in cand_regions]
    names += sorted((set(base_regions) | set(cand_regions)) - set(_MEMORY_REGION_ORDER))
    metrics: list[MetricDiff] = []
    for name in names:
        for field_name, lower_is_better in _MEMORY_REGION_FIELDS:
            # No summary path: the regions block is a list keyed by name,
            # not a nested dict, so the values are supplied below rather
            # than path-read. An empty path cannot be mistaken for one.
            spec = _MetricField(
                f"memory_regions.{name}.{field_name}",
                (),
                "bytes",
                group="memory",
                lower_is_better=lower_is_better,
            )
            b = base_regions.get(name, {}).get(field_name)
            c = cand_regions.get(name, {}).get(field_name)
            diff = _metric_diff(spec, b, c, include_groups)
            if diff is not None:
                metrics.append(diff)
    return metrics
