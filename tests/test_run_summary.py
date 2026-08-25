"""Contracts for the typed run-summary model (#202 Part A).

Three promises under test: the producer round-trips byte-identically through
the model (the golden digests are the system-level proof; the identity test
here is the fast local form), the reader is tolerant across schema
generations, and cross-version interpretation lives on the model's
properties rather than at read sites.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helia_profiler.errors import ReportError
from helia_profiler.report.summary import _write_summary
from helia_profiler.results.run_summary import (
    RUN_SUMMARY_SCHEMA_VERSION,
    LatencySection,
    PowerSection,
    RunSummary,
    load_run_summary,
)

from test_report import _attach_power_terminal, _gated_power_ctx


def test_producer_roundtrip_is_identity(tmp_path: Path) -> None:
    """from_dict -> to_dict reproduces a real produced artifact exactly —
    values, key order, nesting. The golden digests pin this at the artifact
    level; this is the same claim in one fast assertion."""
    ctx = _gated_power_ctx(
        tmp_path, clean_infer_count=233, clean_infer_avg_us=21532, duration_s=4.427
    )
    _attach_power_terminal(ctx, elapsed_us=4_427_500, count=233)
    out_path = _write_summary(ctx, tmp_path)
    produced = json.loads(out_path.read_text())

    round_tripped = RunSummary.from_dict(produced).to_dict()

    assert json.dumps(round_tripped, indent=2, default=str) == json.dumps(
        produced, indent=2, default=str
    )


def test_reader_preserves_unknown_keys_and_reads_canonical_fields(tmp_path: Path) -> None:
    data = {
        "schema": "hpx.run-summary",
        "schema_version": RUN_SUMMARY_SCHEMA_VERSION + 1,
        "engine": "helia-rt",
        "layers": 13,
        "total_cycles": 123456,
        "overflow_detected": False,
        "a_future_field": {"x": 1},
        "power": {
            "energy_j": 0.0016,
            "a_future_power_field": 7,
        },
    }
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(data))

    summary = load_run_summary(path)

    assert summary.layers == 13
    assert summary.extras == {"a_future_field": {"x": 1}}
    assert summary.power is not None
    assert summary.power.energy_j == 0.0016
    assert summary.power.extras == {"a_future_power_field": 7}


def test_legacy_energy_and_current_spellings() -> None:
    """The validation runner's three-generation fallbacks, now on the model:
    precedence is the legacy explicit-unit keys first, then the canonical
    SI field scaled."""
    legacy = PowerSection.from_dict(
        {"total_energy_uj": 1600.0, "energy_j": 0.0999, "avg_current_ma": 4.5}
    )
    assert legacy.energy_uj == 1600.0
    assert legacy.avg_current_ma == 4.5

    canonical = PowerSection.from_dict(
        {"energy_j": 0.0016, "avg_current_a": 0.004, "avg_power_w": 0.008}
    )
    assert canonical.energy_uj == pytest.approx(1600.0)
    assert canonical.avg_current_ma == pytest.approx(4.0)
    assert canonical.avg_power_mw == pytest.approx(8.0)

    middle = PowerSection.from_dict({"energy_uJ": 5.0})
    assert middle.energy_uj == 5.0


def test_gate_duration_unarbitrated_failure_interpretation() -> None:
    """The #142/#181 arbitration as a property: valid=false is a failure
    only when no drift note reclassified it — misreading exactly this is
    how the runner failed healthy cold-boot runs (#195)."""
    arbitrated = PowerSection.from_dict(
        {
            "gate_duration_integrity": {"valid": False},
            "gated_window_reference_drift": "window ran 11.8% short ...",
        }
    )
    assert arbitrated.gate_duration_unarbitrated_failure is False

    unarbitrated = PowerSection.from_dict({"gate_duration_integrity": {"valid": False}})
    assert unarbitrated.gate_duration_unarbitrated_failure is True

    healthy = PowerSection.from_dict({"gate_duration_integrity": {"valid": True}})
    assert healthy.gate_duration_unarbitrated_failure is False

    unrecorded = PowerSection.from_dict({})
    assert unrecorded.gate_duration_unarbitrated_failure is False


def test_latency_numbers_carried_verbatim_and_headline_precedence() -> None:
    """A tolerant reader must not round what an artifact wrote (the old
    reader accepted float microseconds), and the headline latency prefers
    the clean window over the profiled average."""
    latency = LatencySection.from_dict(
        {
            "device_clean_infer_avg_us": 41.5,
            "device_profiled_infer_avg_us": 99,
        }
    )
    assert latency.device_clean_infer_avg_us == 41.5
    assert latency.best_latency_avg_us == 41.5

    profiled_only = LatencySection.from_dict({"device_profiled_infer_avg_us": 99})
    assert profiled_only.best_latency_avg_us == 99.0


def _shape_check(summary: dict) -> None:
    from helia_profiler.report.summary import _assert_model_owns_the_shape

    model = RunSummary.from_dict(summary)
    rendered = json.dumps(model.to_dict(), indent=2, default=str)
    _assert_model_owns_the_shape(model, rendered, summary)


def _base_summary(**overrides: object) -> dict:
    """A producer-shaped dict: sections inserted in canonical emission order
    (the equality chokepoint checks bytes, so order matters — as it should)."""
    out: dict = {
        "schema": "hpx.run-summary",
        "schema_version": RUN_SUMMARY_SCHEMA_VERSION,
        "engine": "helia-rt",
        "layers": 1,
        "total_cycles": 100.0,
        "overflow_detected": False,
        "top_layers": [],
    }
    for key in ("memory", "binary", "power", "latency"):
        if key in overrides:
            out[key] = overrides.pop(key)
    out["validity"] = "valid"
    out["issues"] = []
    out.update(overrides)
    return out


@pytest.mark.parametrize(
    "overrides",
    [
        {"a_rogue_top_level_key": 1},
        {"power": {"energy_j": 0.1, "rogue_key": 1}},
        {"latency": {"capture_duration_s": 1.0, "rogue_key": 1}},
        {"memory": {"arena_size": 4, "rogue_key": 1}},
        {"binary": {"text": 1, "data": 2, "bss": 3, "total": 6, "rogue_key": 1}},
    ],
)
def test_unmodelled_producer_key_fails_loudly(overrides: dict) -> None:
    """The model IS the schema: a writer key the model does not declare must
    raise at write time in EVERY section, not ship as an untyped field."""
    with pytest.raises(ReportError, match="rogue"):
        _shape_check(_base_summary(**overrides))


def test_wrong_typed_section_fails_loudly_instead_of_vanishing() -> None:
    """#205 review: a wrong-typed section becomes None in from_dict and
    would silently VANISH from the artifact under a keyset-only check --
    the round-trip equality assert makes it a producer-time failure."""
    with pytest.raises(ReportError, match="diverged"):
        _shape_check(_base_summary(binary="oops"))
    with pytest.raises(ReportError, match="diverged"):
        _shape_check(_base_summary(memory={"arena_size": "N/A"}))


def test_healthy_producer_shape_passes_the_check() -> None:
    _shape_check(
        _base_summary(
            power={"energy_j": 0.1, "measurement_scope": "gpio_gated_clean_window"},
            binary={"text": 1, "data": 2, "bss": 3, "total": 6},
        )
    )


def test_minimal_old_artifact_loads(tmp_path: Path) -> None:
    """A pre-v2-shaped document (no schema fields, no power) still loads —
    the reader owes older artifacts tolerance, not a version gate."""
    path = tmp_path / "summary.json"
    path.write_text(json.dumps({"engine": "tflm", "layers": 3, "total_cycles": 42}))

    summary = load_run_summary(path)

    assert summary.engine == "tflm"
    assert summary.schema_version == 1
    assert summary.power is None
    assert summary.validity is None


def test_legacy_pair_precedence() -> None:
    """Both spellings present: the older explicit-unit key wins, in the
    documented order (kills the lens-2 surviving precedence mutants)."""
    both_energy = PowerSection.from_dict({"total_energy_uj": 1.0, "energy_uJ": 2.0})
    assert both_energy.energy_uj == 1.0
    both_current = PowerSection.from_dict(
        {"avg_current_ma": 3.0, "avg_current_a": 0.004}
    )
    assert both_current.avg_current_ma == 3.0
    both_power = PowerSection.from_dict({"avg_power_mw": 5.0, "avg_power_w": 0.008})
    assert both_power.avg_power_mw == 5.0


def test_non_dict_root_raises_value_error(tmp_path: Path) -> None:
    path = tmp_path / "summary.json"
    path.write_text("[1, 2, 3]")
    with pytest.raises(ValueError, match="not a JSON object"):
        load_run_summary(path)


def test_hostile_shapes_read_as_absent_not_crash() -> None:
    """#205 review F1/F4: non-iterable list fields and non-finite numbers
    must degrade to absence -- a hostile artifact fails ONE case downstream,
    never the reader itself."""
    hostile = RunSummary.from_dict(
        {
            "engine": "x",
            "layers": 1,
            "total_cycles": float("nan"),
            "overflow_detected": False,
            "issues": 7,
            "top_layers": 5,
        }
    )
    assert hostile.issues == ()
    assert hostile.top_layers == ()
    assert hostile.total_cycles_int is None

    infinite = RunSummary.from_dict({"engine": "x", "total_cycles": float("inf")})
    assert infinite.total_cycles_int is None


def test_null_legacy_keys_fall_through_to_canonical() -> None:
    """A null-valued legacy key is garbage, not a value: the property falls
    through to the next source (the old read sites crashed with TypeError)."""
    section = PowerSection.from_dict({"total_energy_uj": None, "energy_j": 2e-6})
    assert section.energy_uj == pytest.approx(2.0)
    assert PowerSection.from_dict({"avg_current_ma": None}).avg_current_ma is None
