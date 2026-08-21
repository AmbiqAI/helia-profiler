"""Contracts binding the four dimension views to DIMENSION_REGISTRY (#154 Phase 3).

The registry (``results/dimensions.py``) is the single declaration; the
writer (``report/manifest.py``), reader (``evaluation/comparability.py``),
code families (``results/issues.py``), and compare display rows are views.
These tests make each view's completeness a test failure instead of a review
catch.
"""

from __future__ import annotations

from pathlib import Path

from helia_profiler.power.base import PowerResult, PowerSummary
from helia_profiler.power.metadata import MeasurementScope, PowerIntegrity, PowerMetadata
from helia_profiler.report.manifest import _comparability
from helia_profiler.results.dimensions import (
    DIMENSION_REGISTRY,
    ArtifactSource,
    ComparisonDimension,
    DimensionEffect,
    dimensions_with_effect,
)

from .conftest import make_pmu_ctx


def _authoritative(dims: tuple[ComparisonDimension, ...]) -> set[str]:
    return {
        dim.value
        for dim in dims
        if DIMENSION_REGISTRY[dim].manifest_authoritative
    }


def test_registry_covers_the_enum_exactly():
    assert set(DIMENSION_REGISTRY) == set(ComparisonDimension)


def test_manifest_writer_records_every_authoritative_dimension(tmp_path: Path):
    # With power present, the writer must record exactly the registry's
    # manifest-authoritative dimensions — power_lockstep's exclusion is the
    # tested rule, not a comment.
    ctx = make_pmu_ctx(tmp_path, board="apollo510_evb", power_enabled=True)
    ctx.power_result = PowerResult(
        summary=PowerSummary(0.01, 0.018, 0.02, 0.18, 10.0, 10000),
        metadata=PowerMetadata(
            measurement_scope=MeasurementScope.GPIO_GATED_CLEAN_WINDOW,
            integrity=PowerIntegrity.VALID,
        ),
    )
    recorded = set(_comparability(ctx))
    assert recorded == _authoritative(tuple(ComparisonDimension))


def test_manifest_writer_without_power_records_the_base_dimensions(tmp_path: Path):
    # A run that measured no power says nothing about how it measured it: a
    # value on that side would block a power-vs-no-power comparison.
    ctx = make_pmu_ctx(tmp_path, board="apollo510_evb")
    recorded = set(_comparability(ctx))
    expected = _authoritative(
        dimensions_with_effect(DimensionEffect.IDENTITY_BLOCKING)
        + dimensions_with_effect(DimensionEffect.INFORMATIVE)
    )
    assert recorded == expected


def test_runtime_only_dimensions_have_a_summary_power_source():
    # A dimension the manifest may not carry must be readable from somewhere:
    # runtime-only means summary.power is its record.
    for spec in DIMENSION_REGISTRY.values():
        if not spec.manifest_authoritative:
            assert spec.source is ArtifactSource.SUMMARY_POWER, spec.dimension


def test_manifest_only_dimensions_declare_no_artifact_path():
    for spec in DIMENSION_REGISTRY.values():
        if spec.source is ArtifactSource.MANIFEST_ONLY:
            assert spec.path == () and spec.derive is None, spec.dimension
        elif spec.derive is None:
            assert spec.path, f"{spec.dimension} needs a path or derive"
