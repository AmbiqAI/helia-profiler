"""The comparability dimension model (#154 Phase 3).

One registry declaring every comparison dimension: its effect on comparison
output, where its value lives in the run artifacts, whether the manifest may
override the artifact-derived value, its metric group, and its display label.
The former hand-synced copies are now views of this registry, each bound to
it by a contract test:

* the comparability code families in ``results/issues.py`` **derive** their
  dimension tuples from the registry by effect class;
* ``evaluation/comparability.py:_dimensions()`` reads artifacts by each
  spec's source and path;
* ``report/manifest.py:_comparability()`` keeps its typed-context extraction
  (declaring extractors here would couple ``results/`` to the pipeline) but
  a contract test pins its key set to this registry;
* ``evaluation/compare.py``'s config-diff table pulls label and path from
  the specs for the rows that are dimensions.

Wire freeze: dimension names, emitted code strings (including the doubled
``metric.power_power_*`` prefix — decision recorded on #154: frozen
permanently), and every artifact shape are unchanged. Registry entry order
reproduces the historical emission order of the comparability loops.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping


class ComparisonDimension(StrEnum):
    """Comparison dimensions (the vocabulary behind comparability codes).

    Moved here from ``results/issues.py`` in Phase 3 (which re-exports it);
    now also covers the two dimensions that participate in comparability
    without appearing inside code strings: ``MODEL_SHA256`` (identity) and
    ``POWER_INTEGRITY`` (metric gate).
    """

    # Identity — a mismatch blocks the whole comparison.
    MODEL_SHA256 = "model_sha256"

    # Power dimensions — a mismatch blocks power metrics.
    POWER_SCOPE = "power_scope"
    POWER_MODE = "power_mode"
    POWER_FIRMWARE = "power_firmware"
    POWER_MONITOR = "power_monitor"
    POWER_LOCKSTEP = "power_lockstep"
    POWER_CLEAN_WINDOW_PROBE = "power_clean_window_probe"
    POWER_FIRMWARE_FINGERPRINT = "power_firmware_fingerprint"

    # Metric gate — a non-valid value on either side blocks power metrics.
    POWER_INTEGRITY = "power_integrity"

    # Informative dimensions — a difference is reported, never blocking.
    HPX_VERSION = "hpx_version"
    ENGINE = "engine"
    BOARD = "board"
    SOC = "soc"
    CPU_CLOCK = "cpu_clock"
    TOOLCHAIN = "toolchain"
    COMPILER_VERSION = "compiler_version"
    SYSTEM_CLOCK_HZ = "system_clock_hz"
    RUN_SUMMARY_SCHEMA_VERSION = "run_summary_schema_version"
    RUN_METADATA_SCHEMA_VERSION = "run_metadata_schema_version"
    TRANSPORT = "transport"
    ARENA_LOCATION = "arena_location"
    WEIGHTS_LOCATION = "weights_location"


class DimensionEffect(StrEnum):
    """What a dimension does to comparison output."""

    IDENTITY_BLOCKING = "identity_blocking"
    POWER_METRIC_BLOCKING = "power_metric_blocking"
    METRIC_GATE = "metric_gate"
    INFORMATIVE = "informative"


class ArtifactSource(StrEnum):
    """Where the reader finds a dimension's value in the run artifacts."""

    #: A path into ``run_metadata.json``.
    RUN_METADATA = "run_metadata"
    #: A path into ``summary.json``'s root.
    SUMMARY = "summary"
    #: A path into ``summary.json``'s ``power`` block (read only when the
    #: block exists).
    SUMMARY_POWER = "summary_power"
    #: No artifact fallback — the value reaches the reader only through the
    #: manifest's comparability record.
    MANIFEST_ONLY = "manifest_only"


@dataclass(frozen=True)
class DimensionSpec:
    """Declaration of one comparison dimension.

    ``manifest_authoritative`` is ``False`` only for ``power_lockstep``: the
    runtime value in ``summary.power.sync.lockstep`` records the state the
    rail was actually in, and config intent (which is what the manifest
    derives from) answers the wrong question — a driver with no GO output
    degrades to the null controller even when config resolved lock-step on.
    The manifest writer excludes it and the reader merge must never override
    it; both rules are contract-tested from this flag (the #115
    phantom-comparability lesson, as data instead of comments).

    ``derive`` computes the value from the source dict when a plain path
    cannot express it (``power_monitor``'s manifest-less fallback).
    """

    dimension: ComparisonDimension
    effect: DimensionEffect
    source: ArtifactSource
    path: tuple[str, ...] = ()
    manifest_authoritative: bool = True
    metric_group: str | None = None
    label: str | None = None
    derive: Callable[[dict[str, Any]], Any] | None = None
    #: Optional human sentence for a mismatch issue, replacing the generic
    #: "…because <dimension> differs." — used where the raw values (two
    #: 64-hex digests) tell the user nothing actionable.
    mismatch_hint: str | None = None
    #: Dimensions that must MATCH (present and equal on both sides) before
    #: this one is consulted at all. Declared here so the scoping is registry
    #: data, not comparator special-casing. Used by the firmware fingerprint:
    #: cross-platform renders trivially differ, and board/SoC differences are
    #: documented as visible-not-blocking — a fingerprint mismatch only means
    #: something when the platform and firmware mode agree (#138 regression 3).
    #: If a scope dimension is absent on either side (a legacy artifact), the
    #: platform match cannot be established and the dimension is skipped —
    #: the same conservative non-blocking rule as an absent value itself.
    scoped_to: tuple[ComparisonDimension, ...] = ()


def _derive_power_monitor(power: dict[str, Any]) -> str:
    # Manifest-less fallback: a published on-device payload means monitor
    # firmware was live. The manifest's config-derived value (merged after)
    # is authoritative when present.
    return "ina228" if power.get("on_device_summary") else "none"


#: Entry order reproduces the historical emission order of the comparability
#: loops (base dict order, then the power-family loop order), which reaches
#: the compare artifacts.
_DIMENSION_SPECS: tuple[DimensionSpec, ...] = (
    DimensionSpec(
        ComparisonDimension.MODEL_SHA256,
        DimensionEffect.IDENTITY_BLOCKING,
        ArtifactSource.RUN_METADATA,
        ("model", "sha256"),
        label="Model SHA256",
    ),
    DimensionSpec(
        ComparisonDimension.HPX_VERSION,
        DimensionEffect.INFORMATIVE,
        ArtifactSource.RUN_METADATA,
        ("hpx_version",),
        label="hpx version",
    ),
    DimensionSpec(
        ComparisonDimension.ENGINE,
        DimensionEffect.INFORMATIVE,
        ArtifactSource.RUN_METADATA,
        ("config", "engine", "type"),
        label="Engine",
    ),
    DimensionSpec(
        ComparisonDimension.BOARD,
        DimensionEffect.INFORMATIVE,
        ArtifactSource.RUN_METADATA,
        ("config", "target", "board"),
        label="Board",
    ),
    DimensionSpec(
        ComparisonDimension.SOC,
        DimensionEffect.INFORMATIVE,
        ArtifactSource.RUN_METADATA,
        ("platform", "soc"),
        label="SoC",
    ),
    DimensionSpec(
        ComparisonDimension.CPU_CLOCK,
        DimensionEffect.INFORMATIVE,
        ArtifactSource.RUN_METADATA,
        ("platform", "cpu_clock_name"),
        label="CPU clock",
    ),
    DimensionSpec(
        ComparisonDimension.TOOLCHAIN,
        DimensionEffect.INFORMATIVE,
        ArtifactSource.RUN_METADATA,
        ("config", "target", "toolchain"),
        label="Toolchain",
    ),
    DimensionSpec(
        ComparisonDimension.COMPILER_VERSION,
        DimensionEffect.INFORMATIVE,
        ArtifactSource.RUN_METADATA,
        ("toolchain", "compiler_version"),
        label="Compiler version",
    ),
    DimensionSpec(
        ComparisonDimension.SYSTEM_CLOCK_HZ,
        DimensionEffect.INFORMATIVE,
        ArtifactSource.RUN_METADATA,
        ("firmware", "system_clock_hz"),
        label="System clock",
    ),
    DimensionSpec(
        ComparisonDimension.RUN_SUMMARY_SCHEMA_VERSION,
        DimensionEffect.INFORMATIVE,
        ArtifactSource.SUMMARY,
        ("schema_version",),
        label="Summary schema",
    ),
    DimensionSpec(
        ComparisonDimension.RUN_METADATA_SCHEMA_VERSION,
        DimensionEffect.INFORMATIVE,
        ArtifactSource.RUN_METADATA,
        ("schema_version",),
        label="Metadata schema",
    ),
    DimensionSpec(
        ComparisonDimension.TRANSPORT,
        DimensionEffect.INFORMATIVE,
        ArtifactSource.RUN_METADATA,
        ("config", "target", "transport"),
        label="Transport",
    ),
    DimensionSpec(
        ComparisonDimension.ARENA_LOCATION,
        DimensionEffect.INFORMATIVE,
        ArtifactSource.RUN_METADATA,
        ("config", "model", "arena_location"),
        label="Arena location",
    ),
    DimensionSpec(
        ComparisonDimension.WEIGHTS_LOCATION,
        DimensionEffect.INFORMATIVE,
        ArtifactSource.RUN_METADATA,
        ("config", "model", "weights_location"),
        label="Weights location",
    ),
    DimensionSpec(
        ComparisonDimension.POWER_SCOPE,
        DimensionEffect.POWER_METRIC_BLOCKING,
        ArtifactSource.SUMMARY_POWER,
        ("measurement_scope",),
        metric_group="power",
    ),
    DimensionSpec(
        ComparisonDimension.POWER_MODE,
        DimensionEffect.POWER_METRIC_BLOCKING,
        ArtifactSource.MANIFEST_ONLY,
        metric_group="power",
    ),
    DimensionSpec(
        ComparisonDimension.POWER_FIRMWARE,
        DimensionEffect.POWER_METRIC_BLOCKING,
        ArtifactSource.SUMMARY_POWER,
        ("power_firmware",),
        metric_group="power",
    ),
    DimensionSpec(
        ComparisonDimension.POWER_MONITOR,
        DimensionEffect.POWER_METRIC_BLOCKING,
        ArtifactSource.SUMMARY_POWER,
        metric_group="power",
        derive=_derive_power_monitor,
    ),
    DimensionSpec(
        ComparisonDimension.POWER_LOCKSTEP,
        DimensionEffect.POWER_METRIC_BLOCKING,
        ArtifactSource.SUMMARY_POWER,
        ("sync", "lockstep"),
        manifest_authoritative=False,
        metric_group="power",
    ),
    DimensionSpec(
        ComparisonDimension.POWER_CLEAN_WINDOW_PROBE,
        DimensionEffect.POWER_METRIC_BLOCKING,
        ArtifactSource.MANIFEST_ONLY,
        metric_group="power",
    ),
    DimensionSpec(
        ComparisonDimension.POWER_FIRMWARE_FINGERPRINT,
        DimensionEffect.POWER_METRIC_BLOCKING,
        ArtifactSource.SUMMARY_POWER,
        ("firmware_code_fingerprint",),
        metric_group="power",
        mismatch_hint=(
            "Power metrics omitted because the measured power firmware's "
            "code differs between the runs — a firmware change altered what "
            "the window executes. Re-baseline, or compare the runs side by "
            "side knowingly."
        ),
        scoped_to=(
            ComparisonDimension.SOC,
            ComparisonDimension.BOARD,
            ComparisonDimension.POWER_FIRMWARE,
        ),
    ),
    DimensionSpec(
        ComparisonDimension.POWER_INTEGRITY,
        DimensionEffect.METRIC_GATE,
        ArtifactSource.SUMMARY_POWER,
        ("integrity",),
        metric_group="power",
    ),
)

DIMENSION_REGISTRY: Mapping[ComparisonDimension, DimensionSpec] = MappingProxyType(
    {spec.dimension: spec for spec in _DIMENSION_SPECS}
)


def dimensions_with_effect(effect: DimensionEffect) -> tuple[ComparisonDimension, ...]:
    """Registry-order dimensions carrying *effect* — the derivation the code
    families are built from, so no second list can drift."""
    return tuple(spec.dimension for spec in _DIMENSION_SPECS if spec.effect is effect)


def uniform_metric_group(dimensions: tuple[ComparisonDimension, ...]) -> str | None:
    """The single metric group shared by *dimensions* (asserted uniform)."""
    groups = {DIMENSION_REGISTRY[dim].metric_group for dim in dimensions}
    if len(groups) != 1:
        raise ValueError(f"Dimensions span multiple metric groups: {sorted(map(str, groups))}")
    return next(iter(groups))
