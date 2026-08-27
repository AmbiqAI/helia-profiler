"""The probe dimension must be written by the real writer, on the real gate.

`hpx compare` blocks power deltas when `power_clean_window_probe` differs --
a `busy_loop` window measures a calibrated CPU spin rather than the model, so
the pair reports the difference between two physical quantities as a
regression (#125 item 4).

Everything downstream of the manifest is checked in tests/test_comparability.py
against hand-built manifests. That is the wrong place to check the WRITER:
mutating `report/manifest.py` to stop recording the dimension, or to record it
for a run that measured no power, left those tests green. This file drives
`_comparability()` itself so both halves are pinned.

The second test is the one that matters most. An earlier, broader version of
this dimension was a digest of the whole window context, recorded
unconditionally -- and because the power floor raises `window_target_ms` only
when power is enabled, it moved on `power.enabled` alone. Comparing a quick
latency run against a power-instrumented one then suppressed the candidate's
real power numbers and reported "the measured window differs", which the user
had not chosen. Recording only for runs that measured power is what prevents
that, and it is the same gate every sibling power dimension already uses.
"""

from __future__ import annotations

from tests.pipeline_context_helpers import set_power_result

from pathlib import Path

import pytest

from helia_profiler.power.metadata import MeasurementScope, PowerMetadata
from helia_profiler.power.base import PowerResult, PowerSummary
from helia_profiler.report.manifest import _comparability

from .conftest import make_pmu_ctx


def _ctx(tmp_path: Path, *, probe: str, measured_power: bool):
    tmp_path.mkdir(parents=True, exist_ok=True)
    ctx = make_pmu_ctx(
        tmp_path,
        board="apollo4p_blue_kbr_evb",
        power_enabled=True,
        extra={"profiling": {"clean_window_probe": probe}},
    )
    if measured_power:
        set_power_result(
            ctx,
            PowerResult(
                summary=PowerSummary(0.01, 0.02, 0.03, 0.1, 1.0, 10),
                metadata=PowerMetadata(measurement_scope=MeasurementScope.GPIO_GATED_CLEAN_WINDOW),
            ),
        )
    return ctx


@pytest.mark.parametrize("probe", ["infer", "busy_loop"])
def test_a_power_run_records_the_probe_it_used(tmp_path: Path, probe: str):
    ctx = _ctx(tmp_path / probe, probe=probe, measured_power=True)

    dimensions = _comparability(ctx)

    assert dimensions["power_clean_window_probe"] == probe


def test_a_run_that_measured_no_power_records_no_probe(tmp_path: Path):
    """Missing is unknown, and unknown is skipped -- never "different".

    A value here would block a power-vs-no-power comparison that works today,
    which is a worse outcome than the blindness the dimension exists to fix.
    """
    ctx = _ctx(tmp_path, probe="busy_loop", measured_power=False)

    dimensions = _comparability(ctx)

    assert dimensions.get("power_clean_window_probe") is None


def test_the_probe_sits_with_the_dimensions_that_share_its_gate(tmp_path: Path):
    """Pins the gate itself, not just today's values.

    Every power dimension appears exactly when a power result does. If the
    probe ever drifts out of that block it starts answering a question an
    unpowered run cannot answer.
    """
    powered = _comparability(_ctx(tmp_path / "on", probe="infer", measured_power=True))
    unpowered = _comparability(_ctx(tmp_path / "off", probe="infer", measured_power=False))

    power_keys = {key for key in powered if key.startswith("power_")}
    assert "power_clean_window_probe" in power_keys
    assert not power_keys & set(unpowered), (
        f"a run with no power result recorded power dimensions: {power_keys & set(unpowered)}"
    )
