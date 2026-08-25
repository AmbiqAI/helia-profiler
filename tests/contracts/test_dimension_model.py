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


def test_manifest_writer_records_the_resolved_engine_version(tmp_path: Path):
    """#207 review: the goldens only pin the null shape (their fixtures set
    no run_metadata.engine); the populated value needs its own pin."""
    from helia_profiler.results import EngineInfo

    ctx = make_pmu_ctx(tmp_path, board="apollo510_evb", power_enabled=False)
    ctx.run_metadata.engine = EngineInfo(type="helia-rt", version="1.17.0")

    recorded = _comparability(ctx)

    assert recorded[ComparisonDimension.ENGINE_VERSION] == "1.17.0"


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


def _run_artifacts(tmp_path: Path, *, power: dict | None, manifest=None):
    from helia_profiler.evaluation.compare import RunArtifacts

    summary: dict = {"schema_version": 2}
    if power is not None:
        summary["power"] = power
    return RunArtifacts(
        path=tmp_path,
        summary=summary,
        metadata={"hpx_version": "0.0.0", "schema_version": 1},
        layers=[],
        manifest=manifest,
    )


def test_reader_reads_every_artifact_sourced_dimension(tmp_path: Path):
    from helia_profiler.evaluation.comparability import _dimensions

    without_power = set(_dimensions(_run_artifacts(tmp_path, power=None)))
    with_power = set(
        _dimensions(_run_artifacts(tmp_path, power={"measurement_scope": "x"}))
    )
    base = {
        spec.dimension
        for spec in DIMENSION_REGISTRY.values()
        if spec.source in (ArtifactSource.RUN_METADATA, ArtifactSource.SUMMARY)
    }
    summary_power = {
        spec.dimension
        for spec in DIMENSION_REGISTRY.values()
        if spec.source is ArtifactSource.SUMMARY_POWER
    }
    assert without_power == base
    assert with_power == base | summary_power
    # MANIFEST_ONLY dimensions never appear from artifacts alone.
    assert not with_power & {
        spec.dimension
        for spec in DIMENSION_REGISTRY.values()
        if spec.source is ArtifactSource.MANIFEST_ONLY
    }


def test_manifest_merge_cannot_override_runtime_only_dimensions(tmp_path: Path):
    # The #115 phantom-comparability rule as an executable contract: a
    # manifest value for power_lockstep (config intent) must never override
    # the runtime record in summary.power.sync.lockstep.
    from helia_profiler.evaluation.comparability import _dimensions
    from helia_profiler.results import ResultManifest

    manifest = ResultManifest.from_dict(
        {
            "schema": "hpx.result-manifest",
            "schema_version": 1,
            "run_id": "r",
            "timestamp": "2026-08-20T00:00:00+00:00",
            "hpx_version": "0.0.0",
            "status": "complete",
            "validity": "valid",
            "issues": [],
            "provenance": {},
            "comparability": {"power_lockstep": False, "power_mode": "external"},
            "artifacts": [],
        }
    )
    dims = _dimensions(
        _run_artifacts(
            tmp_path,
            power={"sync": {"lockstep": True}},
            manifest=manifest,
        )
    )
    assert dims.get(ComparisonDimension.POWER_LOCKSTEP) is True
    # Authoritative manifest values still merge normally.
    assert dims.get(ComparisonDimension.POWER_MODE) == "external"


def test_manifest_only_dimensions_declare_no_artifact_path():
    for spec in DIMENSION_REGISTRY.values():
        if spec.source is ArtifactSource.MANIFEST_ONLY:
            assert spec.path == () and spec.derive is None, spec.dimension
        elif spec.derive is None:
            assert spec.path, f"{spec.dimension} needs a path or derive"
