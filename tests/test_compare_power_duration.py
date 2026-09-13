"""Power duration comparisons resolve the published measurement scope."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helia_profiler.config import load_config
from helia_profiler.evaluation import compare_runs, write_compare_artifacts
from helia_profiler.evaluation.run_metrics import _METRIC_FIELDS, _compare_metrics
from helia_profiler.pipeline import PipelineContext
from helia_profiler.power.base import GatedPowerWindow, PowerResult, PowerSummary
from helia_profiler.power.metadata import MeasurementScope, PowerIntegrity, PowerMetadata
from helia_profiler.report import _write_csv, _write_run_metadata, _write_summary
from helia_profiler.results import BinarySections, FirmwareMeta, LayerResult, PmuResult, TimingInfo
from helia_profiler.results.serde import nested_get
from tests.pipeline_context_helpers import set_power_result, set_profile_result


def _produced_run(
    directory: Path,
    duration: float,
    *,
    scope: MeasurementScope = MeasurementScope.GPIO_GATED_CLEAN_WINDOW,
    integrity: PowerIntegrity = PowerIntegrity.VALID,
) -> dict:
    directory.mkdir()
    ctx = PipelineContext(
        config=load_config(None, {"model": {"path": "model.tflite"}}),
        work_dir=directory,
    )
    ctx.binary_sections = BinarySections(text=100, data=20, bss=30, reserved=40, total=190)
    ctx.run_metadata.timing = TimingInfo(capture_duration_s=19.0)
    set_profile_result(
        ctx,
        PmuResult(
            meta=FirmwareMeta(
                arena_size=4096,
                allocated_arena=2048,
                model_size=1024,
                profiled_infer_count=10,
                profiled_infer_total_us=10000,
                profiled_infer_avg_us=1000,
                clean_infer_count=10,
                clean_infer_avg_us=int(duration * 100000),
            ),
            layers=[LayerResult(id=0, op="CONV_2D", cycles=1000)],
        ),
    )
    set_power_result(
        ctx,
        PowerResult(
            summary=PowerSummary(0.01, 0.02, 0.03, 0.02 * duration, duration, 100),
            gated_windows=[
                GatedPowerWindow(
                    start_s=0,
                    end_s=duration,
                    duration_s=duration,
                    charge_c=0.01 * duration,
                    energy_j=0.02 * duration,
                    avg_current_a=0.01,
                    avg_power_w=0.02,
                    peak_current_a=0.03,
                    sample_count=100,
                )
            ]
            if scope == MeasurementScope.GPIO_GATED_CLEAN_WINDOW
            else [],
            metadata=PowerMetadata(
                measurement_scope=scope,
                integrity=integrity,
                whole_capture_summary={"duration_s": 23.0},
            ),
        ),
    )
    summary = json.loads(_write_summary(ctx, directory).read_text())
    _write_run_metadata(ctx, directory)
    _write_csv(ctx.captured_pmu, directory)
    return summary


@pytest.mark.parametrize(
    "scope",
    [MeasurementScope.GPIO_GATED_CLEAN_WINDOW, MeasurementScope.FREE_FORM_CAPTURE],
)
def test_duration_row_uses_the_produced_power_summary(tmp_path: Path, scope: MeasurementScope):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    for path, duration in ((baseline, 2.0), (candidate, 4.0)):
        summary = _produced_run(path, duration, scope=scope)
        assert summary["power"]["capture_duration_s"] == duration
        assert "duration_s" not in summary["power"]
        assert summary["latency"]["capture_duration_s"] == 19.0
    result = compare_runs(baseline, candidate)
    assert result.comparability.power_metrics_comparable
    row = next(m for m in result.metrics if m.name == "power.duration_s")
    assert (row.baseline, row.candidate, row.delta, row.delta_pct) == (2.0, 4.0, 2.0, 100.0)
    assert (row.unit, row.group) == ("s", "power")
    write_compare_artifacts(result, tmp_path / "comparison")
    artifact = json.loads((tmp_path / "comparison" / "compare_summary.json").read_text())
    serialized = next(m for m in artifact["metrics"] if m["name"] == "power.duration_s")
    assert serialized["delta"] == 2.0


def test_all_declared_metric_paths_are_live_in_a_produced_summary(tmp_path: Path):
    summary = _produced_run(tmp_path / "run", 2.0)
    assert {
        spec.name for spec in _METRIC_FIELDS if nested_get(summary, *spec.path) is None
    } == set()


@pytest.mark.parametrize("invalid_value", [None, True, "not a duration"])
def test_missing_or_nonnumeric_duration_never_uses_another_clock(tmp_path: Path, invalid_value):
    baseline = _produced_run(tmp_path / "baseline", 2.0)
    candidate = _produced_run(tmp_path / "candidate", 4.0)
    candidate["power"]["capture_duration_s"] = invalid_value
    candidate["power"]["duration_s"] = 99.0
    row = next(m for m in _compare_metrics(baseline, candidate) if m.name == "power.duration_s")
    assert row.baseline == 2.0
    assert row.candidate == invalid_value
    assert row.delta is None
    assert row.delta_pct is None
    del baseline["power"]["capture_duration_s"]
    del candidate["power"]["capture_duration_s"]
    assert not any(m.name == "power.duration_s" for m in _compare_metrics(baseline, candidate))


@pytest.mark.parametrize("mismatch", ["scope", "integrity"])
def test_incomparable_power_omits_duration_but_keeps_cycles(tmp_path: Path, mismatch: str):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    _produced_run(baseline, 2.0)
    _produced_run(
        candidate,
        4.0,
        scope=MeasurementScope.FREE_FORM_CAPTURE
        if mismatch == "scope"
        else MeasurementScope.GPIO_GATED_CLEAN_WINDOW,
        integrity=PowerIntegrity.DEGRADED if mismatch == "integrity" else PowerIntegrity.VALID,
    )
    result = compare_runs(baseline, candidate)
    assert not result.comparability.power_metrics_comparable
    assert not any(m.group == "power" for m in result.metrics)
    assert any(m.name == "total_cycles" for m in result.metrics)
