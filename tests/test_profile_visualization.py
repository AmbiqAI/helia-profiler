"""Independent saved-byte controls for reconstructed visualization semantics."""

import hashlib
import json
from pathlib import Path

import pytest

from helia_profiler._profile_visualization import export_visualization, read_visual_run
from helia_profiler.records import ResultReadError

from .test_records import bundle, edit_document


def profile(root: Path, cycles=(100, 300)):
    path = bundle(root)
    metadata = json.loads((root / "run_metadata.json").read_text())
    metadata.update(
        model={"sha256": "a" * 64},
        firmware={"system_clock_hz": 1000},
        config={"profiling": {"aggregation": "median"}},
    )
    edit_document(path, "run_metadata.json", lambda d: d.update(metadata))
    edit_document(
        path, "summary.json", lambda d: d.update(layers=len(cycles), total_cycles=sum(cycles))
    )
    layers = [
        {"id": i, "op": f"OP:{i}", "cycles": c, "ARM_PMU_CPU_CYCLES": c, "overflow": False}
        for i, c in enumerate(cycles)
    ]
    edit_document(
        path,
        "layers.json",
        lambda d: d.update(
            schema="hpx.profile-results",
            schema_version=1,
            metadata=metadata,
            summary={"system_clock_hz": 1000},
            overflow_detected=False,
            layers=layers,
        ),
    )
    return path


def mapping(path, runs, *, omit_last=False):
    data = {
        "schema": "hpx.visualization-mapping",
        "schema_version": 1,
        "model_sha256": "a" * 64,
        "basis": "Synthetic oracle; node IDs deliberately differ from event IDs",
        "runs": {
            r.manifest_sha256: [
                {
                    "event_id": layer.event_id,
                    "event_name": layer.name,
                    "graph_id": "main",
                    "node_id": str(10 + i),
                    "outputs": [[1, 2, 3]],
                }
                for i, layer in enumerate(r.layers)
                if not (omit_last and i == len(r.layers) - 1)
            ]
            for r in runs
        },
    }
    path.write_text(json.dumps(data))
    return path


def test_units_separate_totals_explicit_mapping_and_delta(tmp_path):
    a = read_visual_run(profile(tmp_path / "a"), "baseline")
    b = read_visual_run(profile(tmp_path / "b", (200, 150)), "candidate")
    out = export_visualization(
        [a, b], tmp_path / "out", mapping_path=mapping(tmp_path / "map.json", [a, b])
    )
    trace = json.loads((out / "comparison.perfetto.json").read_text())
    slices = [e for e in trace["traceEvents"] if e["ph"] == "B" and e["tid"] == 1]
    assert [e["ts"] for e in slices] == [0, 100000, 0, 200000]
    assert [e["ts"] for e in trace["traceEvents"] if e["ph"] == "E"] == [
        100000,
        400000,
        200000,
        350000,
    ]
    assert "not recorded wall-clock" in trace["otherData"]["time_basis"]
    totals = [e for e in trace["traceEvents"] if e["ph"] == "X" and e["tid"] == 2]
    assert [e["dur"] for e in totals] == [3000, 3000]  # Not scaled to operator sums.
    view = json.loads((out / "comparison.json").read_text())
    assert [x["delta_ms"] for x in view["layers"]] == [100, -150]
    assert [x["delta_pct"] for x in view["layers"]] == [100, -50]
    overlay = json.loads((out / "run-1.latency.overlay.json").read_text())
    assert overlay["main"]["results"]["10"]["value"] == 100
    assert "0" not in overlay["main"]["results"]
    delta = json.loads((out / "delta_ms.overlay.json").read_text())["main"]["results"]
    assert delta["10"]["bgColor"] != delta["11"]["bgColor"]


def test_no_mapping_does_not_invent_graph_join_or_overlays(tmp_path):
    run = read_visual_run(profile(tmp_path / "a"), "one")
    out = export_visualization([run], tmp_path / "view")
    assert not list(out.glob("*.overlay.json"))
    view = json.loads((out / "comparison.json").read_text())
    assert view["runs"][0]["unmapped_event_ids"] == [0, 1]


def test_zero_denominator_remains_unavailable(tmp_path):
    runs = [
        read_visual_run(profile(tmp_path / name, values), name)
        for name, values in [("a", (0, 200)), ("b", (100, 200))]
    ]
    out = export_visualization(
        runs, tmp_path / "view", mapping_path=mapping(tmp_path / "map.json", runs)
    )
    rows = json.loads((out / "comparison.json").read_text())["layers"]
    assert rows[0]["delta_ms"] == 100
    assert rows[0]["delta_pct"] is None


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["summary"].update(system_clock_hz=0),
        lambda d: d["summary"].update(system_clock_hz=2000),
        lambda d: d["layers"][0].update(cycles=None),
        lambda d: d["layers"][0].update(cycles=-1),
        lambda d: d["layers"][0].update(cycles=True),
        lambda d: d["layers"][0].update(ARM_PMU_CPU_CYCLES=101),
        lambda d: d["layers"][0].update(overflow=True),
        lambda d: d["layers"][1].update(id=0),
        lambda d: d["layers"].pop(),
        lambda d: d["metadata"].update(run_id="other"),
    ],
)
def test_rejects_inconsistent_saved_profile(tmp_path, change):
    path = profile(tmp_path)
    edit_document(path, "layers.json", change)
    with pytest.raises(ValueError):
        read_visual_run(path, "invalid")


def test_native_hash_refusal_and_source_preservation(tmp_path):
    path = profile(tmp_path / "source")
    before = {p.name: p.read_bytes() for p in path.parent.iterdir()}
    run = read_visual_run(path, "one")
    with pytest.raises(ValueError, match="outside"):
        export_visualization([run], path.parent / "view")
    out = export_visualization([run], tmp_path / "view")
    with pytest.raises(FileExistsError):
        export_visualization([run], out)
    assert before == {p.name: p.read_bytes() for p in path.parent.iterdir()}
    (path.parent / "layers.json").write_text("{}")
    with pytest.raises(ResultReadError):
        read_visual_run(path, "tampered")


@pytest.mark.parametrize("fault", ["model", "name", "duplicate", "node", "boolean"])
def test_invalid_mapping_never_publishes_output(tmp_path, fault):
    run = read_visual_run(profile(tmp_path / "source"), "one")
    path = mapping(tmp_path / "map.json", [run])
    data = json.loads(path.read_text())
    rows = data["runs"][run.manifest_sha256]
    if fault == "model":
        data["model_sha256"] = "b" * 64
    elif fault == "name":
        rows[0]["event_name"] = "another op"
    elif fault == "duplicate":
        rows.append(rows[0])
    elif fault == "boolean":
        rows[0]["event_id"] = True
    else:
        rows[1]["node_id"] = rows[0]["node_id"]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        export_visualization([run], tmp_path / "out", mapping_path=path)
    assert not (tmp_path / "out").exists()


def test_firmware_enrichment_is_not_a_new_run(tmp_path):
    path = profile(tmp_path)
    edit_document(path, "layers.json", lambda d: d["metadata"].pop("firmware"))
    assert read_visual_run(path, "one").clock_hz == 1000


def test_mapping_bytes_remain_identified(tmp_path):
    run = read_visual_run(profile(tmp_path / "source"), "one")
    path = mapping(tmp_path / "map.json", [run], omit_last=True)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    out = export_visualization([run], tmp_path / "out", mapping_path=path)
    view = json.loads((out / "comparison.json").read_text())
    assert view["mapping"]["sha256"] == digest
    assert view["runs"][0]["unmapped_event_ids"] == [1]


@pytest.mark.parametrize("key", ["target_lifecycle", "power_terminal", "on_device_power"])
def test_native_post_capture_enrichment_is_accepted(tmp_path, key):
    path = profile(tmp_path)
    edit_document(path, "run_metadata.json", lambda d: d.update({key: {"status": "recorded"}}))
    assert read_visual_run(path, "one").clock_hz == 1000
    edit_document(path, "layers.json", lambda d: d["metadata"].update({key: {"status": "other"}}))
    with pytest.raises(ValueError, match="differs"):
        read_visual_run(path, "one")


def test_visualization_import_never_attempts_model_or_device_modules(tmp_path):
    import subprocess
    import sys

    script = tmp_path / "import_probe.py"
    script.write_text("""
import sys
class Refuse:
    def find_spec(self, name, *args):
        if name.split('.')[0] in {'ai_edge_litert', 'helia_aot', 'tensorflow', 'pylink', 'pyjoulescope_driver', 'neuralspotx'}:
            raise RuntimeError('Forbidden import attempted: ' + name)
sys.meta_path.insert(0, Refuse())
from helia_profiler._profile_visualization import read_visual_run, export_visualization
""")
    completed = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=15
    )
    assert completed.returncode == 0, completed.stderr


def test_fractional_cost_boundaries_are_shared_not_added_durations(tmp_path):
    path = profile(tmp_path / "source", (83, 61, 107))
    edit_document(
        path, "run_metadata.json", lambda d: d["firmware"].update(system_clock_hz=96000000)
    )
    edit_document(
        path,
        "layers.json",
        lambda d: (
            d["summary"].update(system_clock_hz=96000000),
            d["metadata"]["firmware"].update(system_clock_hz=96000000),
        ),
    )
    run = read_visual_run(path, "one")
    out = export_visualization([run], tmp_path / "view")
    events = [
        e
        for e in json.loads((out / "run-1.perfetto.json").read_text())["traceEvents"]
        if e["tid"] == 1 and e["ph"] != "M"
    ]
    assert [e["ph"] for e in events] == ["B", "E", "B", "E", "B", "E"]
    assert events[1]["ts"] == events[2]["ts"]
    assert events[3]["ts"] == events[4]["ts"]
    assert events[-1]["ts"] == round(251 / 96000000 * 1e9) / 1000


def test_complete_pmu_metric_is_stacked_with_exact_layer_totals(tmp_path):
    path = profile(tmp_path / "source")
    edit_document(
        path,
        "layers.json",
        lambda d: [
            row.update(ARM_PMU_STALL_BACKEND=value)
            for row, value in zip(d["layers"], (0, 75), strict=True)
        ],
    )
    out = export_visualization([read_visual_run(path, "one")], tmp_path / "view")
    trace = json.loads((out / "comparison.perfetto.json").read_text())
    track = [e for e in trace["traceEvents"] if e["ph"] == "C" and "STALL_BACKEND" in e["name"]]
    assert [e["ts"] for e in track] == [0, 100000, 400000]
    assert [e["args"]["per-layer total"] for e in track] == [0, 75, 0]
    assert "not sampled" in trace["otherData"]["pmu_basis"]


def test_missing_pmu_metric_is_a_gap_not_a_held_or_zero_value(tmp_path):
    path = profile(tmp_path / "source", (100, 100, 100))
    edit_document(
        path,
        "layers.json",
        lambda d: (
            d["layers"][0].update(ARM_PMU_MEM_ACCESS=12),
            d["layers"][2].update(ARM_PMU_MEM_ACCESS=0),
        ),
    )
    out = export_visualization([read_visual_run(path, "one")], tmp_path / "view")
    events = json.loads((out / "comparison.perfetto.json").read_text())["traceEvents"]
    assert not [e for e in events if e["ph"] == "C" and "MEM_ACCESS" in e["name"]]
    tid = next(
        e["tid"]
        for e in events
        if e["ph"] == "M" and "MEM_ACCESS" in e.get("args", {}).get("name", "")
    )
    slices = [e for e in events if e["ph"] != "M" and e.get("tid") == tid]
    assert [e["ph"] for e in slices] == ["B", "E", "B", "E"]
    assert [e["ts"] for e in slices] == [0, 100000, 200000, 300000]
    assert [e["args"]["value"] for e in slices if e["ph"] == "B"] == [12, 0]


def test_zero_width_operator_counter_keeps_identity_as_slice(tmp_path):
    path = profile(tmp_path / "source", (0, 100))
    out = export_visualization([read_visual_run(path, "one")], tmp_path / "view")
    events = json.loads((out / "comparison.perfetto.json").read_text())["traceEvents"]
    assert not [e for e in events if e["ph"] == "C"]
    values = [e["args"]["value"] for e in events if e["ph"] == "B" and e.get("tid", 0) >= 3]
    assert values == [0, 100]
