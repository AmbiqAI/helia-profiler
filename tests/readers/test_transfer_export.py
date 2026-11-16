"""Offline exports use synthetic bundles and hand-authored chronological expectations."""

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from helia_profiler.api import load_result
from helia_profiler.cli.results_cmd import results_app
from helia_profiler.readers.native import ProfileResultRecord, ResultReadError
from helia_profiler.report.json_writer import _write_json
from helia_profiler.report.perfetto import export_transfer_trace
from helia_profiler.results import RunMetadata
from tests.readers.test_records import bundle, edit_document, edit_manifest
from tests.test_transfer_trace import TRACE, TWO_UNITS, _parse


def trace_bundle(root: Path, *, clock: int = 96_000_000) -> Path:
    path = bundle(root)
    result = _parse(TRACE)
    result = replace(result, meta=replace(result.meta, system_clock_hz=clock))
    result = replace(
        result,
        transfer_diagnostics=tuple(replace(r, cpu_hz=clock) for r in result.transfer_diagnostics),
    )
    document = json.loads(
        _write_json(
            result, None, RunMetadata(hpx_version="synthetic", run_id="synthetic-run"), root
        ).read_text()
    )
    edit_document(path, "summary.json", lambda d: d.update(engine="helia-aot"))
    edit_document(
        path,
        "run_metadata.json",
        lambda d: d.update(
            document["metadata"],
            engine={"type": "helia-aot"},
            linker_verifications=[],
            firmware={"system_clock_hz": clock},
        ),
    )
    edit_document(path, "layers.json", lambda d: (d.clear(), d.update(document)))
    return path


def profile(path: Path) -> ProfileResultRecord:
    record = load_result(path)
    assert isinstance(record, ProfileResultRecord)
    return record


def fingerprint(path: Path) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in path.parent.iterdir()
        if p.is_file()
    }


@pytest.mark.parametrize(
    ("clock", "start_us", "piece_us"),
    [
        (96_000_000, 0.2083333333333333, 0.0520833333333333),
        (250_000_000, 0.08, 0.02),
    ],
)
def test_actual_intervals_notifications_and_exact_raw_cycles(tmp_path, clock, start_us, piece_us):
    path = trace_bundle(tmp_path / "native", clock=clock)
    before = fingerprint(path)
    out = export_transfer_trace(profile(path), tmp_path / "trace.json")
    result = json.loads(out.read_text())
    events = result["traceEvents"]
    spans = [e for e in events if e["ph"] == "X"]
    assert len(spans) == 4
    operator = next(e for e in spans if e["args"].get("kind") == "operator")
    assert operator["ts"] == pytest.approx(start_us)
    assert operator["dur"] == pytest.approx(start_us)
    assert operator["args"] == {
        "start_cycles": 20,
        "end_cycles": 40,
        "kind": "operator",
        "id": 0,
        "status": 0,
    }
    (piece,) = [e for e in events if e["ph"] == "I"]
    assert piece["s"] == "t" and piece["ts"] == pytest.approx(piece_us)
    assert piece["args"] == {
        "cycles": 5,
        "sequence": 0,
        "ticket": 0,
        "bytes": 64,
        "status": 0,
        "binding_epoch": 1,
    }
    assert fingerprint(path) == before
    assert result["otherData"]["hpx"]["manifest_sha256"] == profile(path).manifest_sha256


def test_independent_origins_and_sequence_delivery_order(tmp_path):
    path = trace_bundle(tmp_path / "native")

    def edit(d):
        first = d["transfer_diagnostics"][0]
        first["stats"]["issues"] = 2
        trace = first["trace"]
        trace.update(sequence_start=0xFFFFFFFF, observed_count=2)
        trace["completions"] = [
            {"sequence": 0, "ticket": 0, "byte_count": 64, "status": 0, "cycles": 5},
            {"sequence": 0xFFFFFFFF, "ticket": 0, "byte_count": 64, "status": 0, "cycles": 6},
        ]
        second = json.loads(json.dumps(first))
        second["iteration"] = 1
        second["trace"]["binding_epoch"] = 2
        d["transfer_diagnostics"].append(second)

    edit_document(path, "layers.json", edit)
    out = json.loads(export_transfer_trace(profile(path), tmp_path / "out.json").read_text())
    invocations = [e for e in out["traceEvents"] if e["ph"] == "X" and "complete" in e["args"]]
    assert [(e["pid"], e["ts"]) for e in invocations] == [(1, 0), (2, 0)]
    threads = [e for e in out["traceEvents"] if e["name"] == "thread_name"]
    assert len({e["tid"] for e in threads}) == 8
    assert all(e["tid"] > 2 for e in threads)
    notifications = [e for e in out["traceEvents"] if e["ph"] == "I"]
    assert [e["args"]["sequence"] for e in notifications] == [0, 0xFFFFFFFF, 0, 0xFFFFFFFF]
    assert "do not share a wall clock" in out["otherData"]["hpx"]["time_basis"]


def test_incomplete_missing_endpoints_are_not_filled(tmp_path):
    path = trace_bundle(tmp_path / "native")

    def edit(d):
        r = d["transfer_diagnostics"][0]
        r.update(complete=False, run_status=-1)
        r["trace"].update(observed_count=2, dropped_count=1)
        r["trace"]["spans"][0].update(end_cycles=None, status=None)
        r["trace"]["spans"][1].update(start_cycles=None, end_cycles=None, status=None)

    edit_document(path, "layers.json", edit)
    out = json.loads(export_transfer_trace(profile(path), tmp_path / "out.json").read_text())
    events = out["traceEvents"]
    assert len([e for e in events if e["ph"] == "X"]) == 2
    assert any(e["name"].startswith("INCOMPLETE:") for e in events)
    (unpaired,) = [e for e in events if e["name"].startswith("UNPAIRED")]
    assert unpaired["ph"] == "I" and unpaired["args"]["cycles"] == 1


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.update(cpu_hz=True),
        lambda r: r.update(cpu_hz=250_000_000),
        lambda r: r.update(run_status=-1),
        lambda r: r.update(complete=1),
        lambda r: r["trace"].update(schema_version=2),
        lambda r: r["trace"].update(clock_valid=False),
        lambda r: r["trace"].update(operator_count=0xFFFFFFFF),
        lambda r: r["trace"].update(observed_count=4),
        lambda r: r["trace"]["spans"].pop(),
        lambda r: r["trace"]["spans"][1].update(start_cycles=9),
        lambda r: r["trace"]["spans"][0].update(status=2),
        lambda r: r["trace"]["spans"].append(r["trace"]["spans"][0]),
        lambda r: r["trace"]["completions"][0].update(sequence=1),
        lambda r: r["trace"]["completions"][0].update(cycles=96_001),
        lambda r: r["trace"]["completions"][0].update(byte_count=False),
        lambda r: r["trace"]["completions"][0].update(status=7),
        lambda r: r["stats"].update(waits=0),
        lambda r: r["stats"].update(aborts=1),
    ],
)
def test_bad_saved_variants_refuse_before_output(tmp_path, change):
    path = trace_bundle(tmp_path / "native")
    edit_document(path, "layers.json", lambda d: change(d["transfer_diagnostics"][0]))
    with pytest.raises(ResultReadError):
        export_transfer_trace(profile(path), tmp_path / "absent.json")
    assert not (tmp_path / "absent.json").exists()


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["transfer_diagnostics"].append(d["transfer_diagnostics"][0]),
        lambda d: d["metadata"].update(run_id="another-run"),
        lambda d: d["summary"].update(system_clock_hz=250_000_000),
        lambda d: d["layers"][0].update(id=5),
    ],
)
def test_cross_record_context_refusals(tmp_path, change):
    path = trace_bundle(tmp_path / "native")
    edit_document(path, "layers.json", change)
    with pytest.raises(ResultReadError):
        export_transfer_trace(profile(path), tmp_path / "absent.json")


@pytest.mark.parametrize(
    "profiling",
    [
        {"transfer_diagnostics": "timing"},
        {"transfer_diagnostic_iterations": 2},
        {"transfer_trace_max_pieces": 3},
        {"stream_evidence": True},
    ],
)
def test_recorded_configuration_is_not_silently_overridden(tmp_path, profiling):
    path = trace_bundle(tmp_path / "native")
    edit_document(path, "run_metadata.json", lambda d: d.update(config={"profiling": profiling}))
    edit_document(
        path, "layers.json", lambda d: d["metadata"].update(config={"profiling": profiling})
    )
    with pytest.raises(ResultReadError):
        export_transfer_trace(profile(path), tmp_path / "absent.json")


@pytest.mark.parametrize("what", ["artifact", "manifest"])
def test_stale_loaded_record_is_rechecked(tmp_path, what):
    path = trace_bundle(tmp_path / "native")
    record = profile(path)
    if what == "artifact":
        (path.parent / "layers.json").write_text("{}")
    else:
        edit_manifest(path, lambda d: d.update(provenance={"new": "facts"}))
    with pytest.raises(ResultReadError):
        export_transfer_trace(record, tmp_path / "absent.json")


def test_exclusive_output_and_model_only_refusal(tmp_path):
    path = trace_bundle(tmp_path / "native")
    record = profile(path)
    before = fingerprint(path)
    with pytest.raises(FileExistsError):
        export_transfer_trace(record, path.parent / "layers.json")
    assert fingerprint(path) == before
    record = replace(record, manifest=replace(record.manifest, bundle_type="profile-model-only"))
    with pytest.raises(ResultReadError, match="per-layer profile"):
        export_transfer_trace(record, tmp_path / "absent.json")


def test_cli_json_default_and_explicit_perfetto(tmp_path):
    path = trace_bundle(tmp_path / "native")
    runner = CliRunner()
    for name, flags in (
        ("default", []),
        ("json", ["--format", "json"]),
        ("perfetto", ["--format", "perfetto"]),
    ):
        result = runner.invoke(
            results_app, ["export", str(path), "--output", str(tmp_path / name), *flags]
        )
        assert result.exit_code == 0, result.output + str(result.exception)
    assert (tmp_path / "default").read_bytes() == (tmp_path / "json").read_bytes()
    assert "traceEvents" in json.loads((tmp_path / "perfetto").read_text())
    result = runner.invoke(
        results_app,
        ["export", str(path), "--output", str(tmp_path / "perfetto"), "--format", "perfetto"],
    )
    assert result.exit_code == 1 and '"code": "export_failed"' in result.output


def test_no_trace_profile_has_specific_cli_error(tmp_path):
    path = trace_bundle(tmp_path / "native")
    edit_document(path, "layers.json", lambda d: d.update(transfer_diagnostics=[]))
    with pytest.raises(ResultReadError) as error:
        export_transfer_trace(profile(path), tmp_path / "absent.json")
    assert error.value.code == "no_transfer_trace"


def test_export_imports_remain_hardware_and_compiler_free(tmp_path):
    probe = tmp_path / "offline.py"
    probe.write_text("""import sys
from helia_profiler.report.perfetto import export_transfer_trace
from helia_profiler.cli import main
try:
    main(["results", "export", "--help"])
except SystemExit as exc:
    assert exc.code == 0
forbidden = ("pylink", "pyjoulescope_driver", "neuralspotx", "helia_profiler.target",
             "helia_profiler.profiler", "helia_aot")
assert not [n for n in sys.modules if n.startswith(forbidden)]
""")
    guard = Path(__file__).resolve().parents[2] / "tools/software_only.py"
    result = subprocess.run(
        [sys.executable, str(guard), "python", str(probe)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_incomplete_conflicting_calls_stay_retained_but_cannot_be_drawn(tmp_path):
    lines = [
        s.replace("SPAN=operator,0,20,40,0", "SPAN=operator,0,9,40,0").replace(
            "96000,0,1", "96000,0,0"
        )
        for s in TRACE
    ]
    assert not _parse(lines).transfer_diagnostics[0].complete
    path = trace_bundle(tmp_path / "native")

    def edit(d):
        record = d["transfer_diagnostics"][0]
        record["complete"] = False
        record["trace"]["spans"][1]["start_cycles"] = 9

    edit_document(path, "layers.json", edit)
    with pytest.raises(ResultReadError, match="overlaps sequential"):
        export_transfer_trace(profile(path), tmp_path / "absent.json")


def test_degraded_source_status_is_visible(tmp_path):
    path = trace_bundle(tmp_path / "native")
    edit_manifest(path, lambda d: d.update(validity="degraded"))
    out = json.loads(export_transfer_trace(profile(path), tmp_path / "out.json").read_text())
    assert any(e["name"] == "SOURCE complete/degraded" for e in out["traceEvents"])
    (invocation,) = [e for e in out["traceEvents"] if e["ph"] == "X" and "complete" in e["args"]]
    assert invocation["args"]["source_validity"] == "degraded"


@pytest.mark.parametrize("media", ["text/csv", "application/json"])
def test_exactly_one_json_table_while_csv_may_share_role(tmp_path, media):
    path = trace_bundle(tmp_path / "native")
    raw = (
        (path.parent / "layers.json").read_bytes()
        if media == "application/json"
        else b"id,op\n0,op\n"
    )
    name = "another.json" if media == "application/json" else "layers.csv"
    (path.parent / name).write_bytes(raw)
    edit_manifest(
        path,
        lambda d: d["artifacts"].append(
            {
                "path": name,
                "name": "hpx.profile-layers",
                "media_type": media,
                "size_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        ),
    )
    if media == "application/json":
        with pytest.raises(ResultReadError, match="one native JSON"):
            export_transfer_trace(profile(path), tmp_path / "absent.json")
    else:
        export_transfer_trace(profile(path), tmp_path / "out.json")


@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.parametrize("cycles", [0, 61])
def test_known_completion_causality_applies_to_partial_and_complete(tmp_path, complete, cycles):
    path = trace_bundle(tmp_path / "native")

    def edit(d):
        r = d["transfer_diagnostics"][0]
        r["complete"] = complete
        r["trace"]["completions"][0]["cycles"] = cycles

    edit_document(path, "layers.json", edit)
    with pytest.raises(ResultReadError, match="causally inconsistent"):
        export_transfer_trace(profile(path), tmp_path / "absent.json")


def test_partial_wait_cannot_precede_its_known_issue(tmp_path):
    path = trace_bundle(tmp_path / "native")

    def edit(d):
        r = d["transfer_diagnostics"][0]
        r["complete"] = False
        r["trace"].update(completions=[], observed_count=0)
        r["trace"]["spans"][2].update(start_cycles=0, end_cycles=1)

    edit_document(path, "layers.json", edit)
    with pytest.raises(ResultReadError, match="waits before unit issue"):
        export_transfer_trace(profile(path), tmp_path / "absent.json")


def test_failed_wait_and_unknown_issue_endpoints_remain_inspectable(tmp_path):
    path = trace_bundle(tmp_path / "native")

    def edit(d):
        r = d["transfer_diagnostics"][0]
        r.update(complete=False, run_status=-1)
        r["trace"]["spans"][2]["status"] = -1
        r["trace"]["completions"][0]["cycles"] = 61

    edit_document(path, "layers.json", edit)
    export_transfer_trace(profile(path), tmp_path / "late-after-failed-wait.json")

    def unknown(d):
        r = d["transfer_diagnostics"][0]
        r["trace"]["spans"][0].update(start_cycles=None, end_cycles=None, status=None)
        r["trace"]["completions"][0]["cycles"] = 0

    edit_document(path, "layers.json", unknown)
    export_transfer_trace(profile(path), tmp_path / "unknown-issue.json")


@pytest.mark.parametrize(
    "profiling",
    [
        {"transfer_diagnostics": "off"},
        {"transfer_diagnostic_iterations": 2},
        {"transfer_trace_max_pieces": 3},
        {"stream_evidence": True},
    ],
)
def test_absent_embedded_config_does_not_disable_verified_config(tmp_path, profiling):
    path = trace_bundle(tmp_path / "native")
    edit_document(path, "run_metadata.json", lambda d: d.update(config={"profiling": profiling}))
    with pytest.raises(ResultReadError, match="recorded"):
        export_transfer_trace(profile(path), tmp_path / "absent.json")


@pytest.mark.parametrize("complete", [False, True])
def test_fresh_binding_peak_is_required_only_for_complete_admission(tmp_path, complete):
    path = trace_bundle(tmp_path / "native")

    def edit(d):
        r = d["transfer_diagnostics"][0]
        r["complete"] = complete
        r["stats"]["peak_in_flight"] = 2

    edit_document(path, "layers.json", edit)
    if complete:
        with pytest.raises(ResultReadError, match="accounting"):
            export_transfer_trace(profile(path), tmp_path / "absent.json")
    else:
        export_transfer_trace(profile(path), tmp_path / "partial.json")


@pytest.mark.parametrize("endpoint", ["start", "end"])
@pytest.mark.parametrize("cycles", [10, 20, 30, 40, 45])
def test_unpaired_endpoints_constrain_known_foreground_intervals(tmp_path, endpoint, cycles):
    path = trace_bundle(tmp_path / "native")

    def edit(d):
        r = d["transfer_diagnostics"][0]
        r["complete"] = False
        r["trace"]["spans"][0].update(
            start_cycles=cycles if endpoint == "start" else None,
            end_cycles=cycles if endpoint == "end" else None,
            status=None if endpoint == "start" else 0,
        )
        r["trace"]["completions"][0]["cycles"] = 45

    edit_document(path, "layers.json", edit)
    if cycles == 30:
        with pytest.raises(ResultReadError, match="overlaps sequential"):
            export_transfer_trace(profile(path), tmp_path / "absent.json")
    else:
        result = json.loads(export_transfer_trace(profile(path), tmp_path / "out.json").read_text())
        (endpoint_event,) = [e for e in result["traceEvents"] if e["name"].startswith("UNPAIRED")]
        assert endpoint_event["ph"] == "I" and endpoint_event["args"]["cycles"] == cycles


def test_partial_operator_positions_keep_their_known_order(tmp_path):
    path = trace_bundle(tmp_path / "native")
    edit_document(path, "summary.json", lambda d: d.update(layers=2))

    def edit(d):
        d["layers"].append({"id": 1, "op": "second"})
        r = d["transfer_diagnostics"][0]
        r["complete"] = False
        r["trace"]["operator_count"] = 2
        r["trace"]["spans"][1].update(start_cycles=50, end_cycles=None, status=None)
        r["trace"]["spans"].append(
            {
                "kind": "operator",
                "id": 1,
                "start_cycles": 40,
                "end_cycles": 45,
                "status": 0,
            }
        )

    edit_document(path, "layers.json", edit)
    with pytest.raises(ResultReadError, match="reverses operator"):
        export_transfer_trace(profile(path), tmp_path / "absent.json")


@pytest.mark.parametrize("contradictory", [False, True])
@pytest.mark.parametrize("missing_issue_end", [False, True])
def test_partial_acceptance_order_uses_available_bounds(tmp_path, contradictory, missing_issue_end):
    path = trace_bundle(tmp_path / "native")

    def edit(d):
        r = _parse(TWO_UNITS).transfer_diagnostics[0].to_dict()
        r["complete"] = False
        if contradictory:
            pieces = [dict(p) for p in r["trace"]["completions"]]
            pieces[0]["sequence"], pieces[1]["sequence"] = 1, 0
            r["trace"]["completions"] = pieces
        if missing_issue_end:
            spans = [dict(s) for s in r["trace"]["spans"]]
            spans[0].update(end_cycles=None, status=None)
            r["trace"]["spans"] = spans
        d["transfer_diagnostics"] = [r]

    edit_document(path, "layers.json", edit)
    if contradictory:
        with pytest.raises(ResultReadError, match="sequence contradicts"):
            export_transfer_trace(profile(path), tmp_path / "absent.json")
    else:
        export_transfer_trace(profile(path), tmp_path / "out.json")


@pytest.mark.parametrize("issue_mask", range(4))
@pytest.mark.parametrize("wait_mask", range(4))
@pytest.mark.parametrize("reversed_order", [False, True])
def test_partial_issue_wait_order_for_every_endpoint_presence(
    tmp_path, issue_mask, wait_mask, reversed_order
):
    path = trace_bundle(tmp_path / "native")

    def edit(d):
        r = d["transfer_diagnostics"][0]
        r["complete"] = False
        r["trace"].update(completions=[], observed_count=0)
        issue_times, wait_times = ((50, 60), (10, 20)) if reversed_order else ((10, 20), (50, 60))
        for span, mask, times in (
            (r["trace"]["spans"][0], issue_mask, issue_times),
            (r["trace"]["spans"][2], wait_mask, wait_times),
        ):
            span.update(
                start_cycles=times[0] if mask & 1 else None,
                end_cycles=times[1] if mask & 2 else None,
                status=0 if mask & 2 else None,
            )

    edit_document(path, "layers.json", edit)
    # Every known issue point is later than every known wait point in the reversed fixture.
    if reversed_order and issue_mask and wait_mask:
        with pytest.raises(ResultReadError, match="waits before unit issue"):
            export_transfer_trace(profile(path), tmp_path / "absent.json")
    else:
        export_transfer_trace(profile(path), tmp_path / "out.json")
