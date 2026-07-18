from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from helia_profiler.evaluation import ComparabilitySeverity, assess_comparability
from helia_profiler.evaluation import RunArtifacts


def _run(
    *,
    model: str = "abc",
    engine: str = "helia-rt",
    compiler_version: str = "12.2.1",
    system_clock_hz: int = 250_000_000,
    ops=("CONV_2D",),
):
    return RunArtifacts(
        path=Path("results"),
        summary={"schema_version": 1, "total_cycles": 100},
        metadata={
            "schema_version": 1,
            "hpx_version": "0.1.0",
            "model": {"sha256": model},
            "toolchain": {"compiler_version": compiler_version},
            "firmware": {"system_clock_hz": system_clock_hz},
            "platform": {"soc": "apollo510", "cpu_clock_name": "hp"},
            "config": {
                "engine": {"type": engine},
                "target": {
                    "board": "apollo510_evb",
                    "toolchain": "arm-none-eabi-gcc",
                    "transport": "rtt",
                },
                "model": {"arena_location": "tcm", "weights_location": "mram"},
            },
        },
        layers=[{"id": index, "op": op, "cycles": 10} for index, op in enumerate(ops)],
    )


def test_engine_difference_is_informative():
    assessment = assess_comparability(_run(), _run(engine="helia-aot"))

    assert assessment.run_metrics_comparable
    assert assessment.layers_comparable
    issue = next(issue for issue in assessment.issues if issue.code == "dimension.engine_differs")
    assert issue.severity is ComparabilitySeverity.INFORMATIVE


def test_model_mismatch_blocks_all_deltas():
    assessment = assess_comparability(_run(model="abc"), _run(model="def"))

    assert not assessment.run_metrics_comparable
    assert not assessment.layers_comparable
    assert assessment.issues[0].code == "identity.model_mismatch"


def test_topology_mismatch_blocks_only_layer_deltas():
    assessment = assess_comparability(_run(), _run(ops=("CONV_2D", "SOFTMAX")))

    assert assessment.run_metrics_comparable
    assert not assessment.layers_comparable
    assert any(issue.code == "topology.layer_count_mismatch" for issue in assessment.issues)


def test_cross_machine_provenance_differences_are_structured():
    assessment = assess_comparability(
        _run(compiler_version="12.2.1", system_clock_hz=250_000_000),
        _run(compiler_version="14.3.1", system_clock_hz=96_000_000),
    )

    assert assessment.run_metrics_comparable
    assert {issue.code for issue in assessment.issues} >= {
        "dimension.compiler_version_differs",
        "dimension.system_clock_hz_differs",
    }


def test_partial_manifest_dimensions_fall_back_to_metadata():
    baseline = _run(model="abc")
    candidate = _run(model="def")
    from helia_profiler.results import ResultManifest, ResultValidity, RunStatus

    candidate = replace(
        candidate,
        manifest=ResultManifest(
            schema="hpx.result-manifest",
            schema_version=1,
            run_id="candidate",
            timestamp="2026-07-18T00:00:00+00:00",
            hpx_version="0.1.0",
            status=RunStatus.COMPLETE,
            validity=ResultValidity.VALID,
            issues=(),
            provenance={},
            comparability={},
            artifacts=(),
        ),
    )

    assessment = assess_comparability(baseline, candidate)

    assert not assessment.run_metrics_comparable
