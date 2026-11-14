from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from helia_profiler.capture.parser import parse_firmware_output
from helia_profiler.config import load_config
from helia_profiler.errors import CaptureError
from helia_profiler.pipeline import PipelineContext
from helia_profiler.report import write_report
from helia_profiler.report.json_writer import _write_json
from helia_profiler.results import RunMetadata, load_result_manifest
from tests.pipeline_context_helpers import set_profile_result

FIXTURE = Path(__file__).parent / "fixtures/transfer_evidence/run4_excerpt.txt"
SCHEMA = Path(__file__).parents[1] / "src/helia_profiler/data/profile_results.schema.v1.json"


def _session(*evidence: str) -> list[str]:
    return [
        "--- HPX_START ---",
        "--- HPX_PRESET cpu_0 ---",
        "--- HPX_ITER 0 ---",
        '"Layer","Op","ARM_PMU_CPU_CYCLES"',
        "0,ADD,100",
        *evidence,
        "--- HPX_END ---",
    ]


def test_board_transcript_retains_each_pass_and_iteration():
    pmu = parse_firmware_output(FIXTURE.read_text().splitlines())
    records = pmu.transfer_evidence
    assert [(r.preset, r.iteration) for r in records] == [
        ("memory_0", 0),
        ("memory_0", 1),
        ("cpu_0", 0),
        ("cpu_0", 1),
    ]
    first, second = records[:2]
    assert first.stats is not None and first.refills is not None and first.overlap is not None
    assert second.refills is not None
    assert first.stats.issues == 25
    assert first.stats.waits == 4
    assert first.stats.aborts == 0
    assert first.stats.peak_in_flight == 16
    assert first.stats.wait_checks_max == 299
    assert first.stats.unsampled == 0
    assert first.refills.units == {0: 3, 1: 0, 2: 0, 3: 0}
    assert second.refills.units == {0: 0, 1: 0, 2: 0, 3: 0}
    assert first.refills.operators == {0: 0, 1: 0, 2: 0, 3: 0}
    assert first.overlap.units[1].dma_in_progress is True
    assert first.overlap.units[0].dma_in_progress is False
    assert first.overlap.operators[2].reads_pending is False
    assert records[2].refills is None
    assert all(r.stats is not None and r.stats.peak_in_flight == 16 for r in records)


def test_no_evidence_keeps_existing_json_shape(tmp_path):
    pmu = parse_firmware_output(_session())
    assert pmu.transfer_evidence == ()
    payload = json.loads(_write_json(pmu, None, RunMetadata(), tmp_path).read_text())
    assert "transfer_evidence" not in payload
    Draft202012Validator(json.loads(SCHEMA.read_text())).validate(payload)


def test_json_preserves_unknown_zero_empty_and_partial_evidence(tmp_path):
    pmu = parse_firmware_output(
        _session(
            "HPX_TRANSFER_STATS=0,0,0,0,0,1",
            "HPX_TRANSFER_OVERLAP=;0:x/0",
            "HPX_TRANSFER_REFILLS=;0:4294967295",
        )
    )
    payload = json.loads(_write_json(pmu, None, RunMetadata(), tmp_path).read_text())
    record = payload["transfer_evidence"][0]
    assert record["preset"] == "cpu_0" and record["iteration"] == 0
    assert record["stats"] == dict(
        issues=0, waits=0, aborts=0, peak_in_flight=0, wait_checks_max=0, unsampled=1
    )
    assert record["overlap"] == {
        "units": {},
        "operators": {"0": {"dma_in_progress": None, "reads_pending": False}},
    }
    assert record["refills"] == {"units": {}, "operators": {"0": 4294967295}}
    validator = Draft202012Validator(json.loads(SCHEMA.read_text()))
    validator.validate(payload)
    del record["refills"]
    validator.validate(payload)
    record["stats"]["issues"] = -1
    assert not validator.is_valid(payload)


def test_partial_evidence_does_not_fabricate_other_fields(tmp_path):
    pmu = parse_firmware_output(_session("HPX_TRANSFER_OVERLAP=;0:x/1"))
    payload = json.loads(_write_json(pmu, None, RunMetadata(), tmp_path).read_text())
    record = payload["transfer_evidence"][0]
    assert set(record) == {"preset", "iteration", "overlap"}
    assert record["overlap"]["operators"]["0"]["dma_in_progress"] is None


@pytest.mark.parametrize(
    "line",
    [
        "HPX_TRANSFER_STATS=1,2,3,4,5",
        "HPX_TRANSFER_STATS=1,2,3,4,5,6,7",
        "HPX_TRANSFER_STATS=-1,2,3,4,5,6",
        "HPX_TRANSFER_STATS=4294967296,2,3,4,5,6",
        "HPX_TRANSFER_STATS=1,2,no,4,5,6",
        "HPX_TRANSFER_STATS",
        "HPX_TRANSFER_OVERLAP=0:2/1;0:0/0",
        "HPX_TRANSFER_OVERLAP=0:x/x;0:0/0",
        "HPX_TRANSFER_OVERLAP=0:1/1,0:0/0;0:0/0",
        "HPX_TRANSFER_OVERLAP=0:1/1",
        "HPX_TRANSFER_OVERLAP=0:1/1;0:0/0;",
        "HPX_TRANSFER_REFILLS=0:-1;0:0",
        "HPX_TRANSFER_REFILLS=0:0;0:0,0:1",
        "HPX_TRANSFER_REFILLS=0:0,;0:0",
        "HPX_TRANSFER_REFILLS=0:0;no:0",
    ],
)
def test_malformed_evidence_quotes_the_offending_line(line):
    with pytest.raises(CaptureError) as exc:
        parse_firmware_output(_session(line))
    assert line in str(exc.value)


def test_duplicate_evidence_is_not_silently_overwritten():
    line = "HPX_TRANSFER_STATS=1,2,3,4,5,6"
    with pytest.raises(CaptureError) as exc:
        parse_firmware_output(_session(line, line))
    assert line in str(exc.value)


def test_evidence_requires_an_instrumented_iteration():
    line = "HPX_TRANSFER_STATS=1,2,3,4,5,6"
    with pytest.raises(CaptureError) as exc:
        parse_firmware_output(["--- HPX_START ---", line, "--- HPX_END ---"])
    assert line in str(exc.value)


def test_repeated_iteration_id_with_evidence_is_rejected():
    line = "HPX_TRANSFER_STATS=1,2,3,4,5,6"
    lines = _session(line)
    lines[-1:] = lines[2:]
    with pytest.raises(CaptureError, match="iteration"):
        parse_firmware_output(lines)


def test_legacy_single_pass_uses_default_preset_identity():
    lines = [s for s in _session("HPX_TRANSFER_STATS=1,2,3,4,5,6") if "HPX_PRESET" not in s]
    records = parse_firmware_output(lines).transfer_evidence
    assert (records[0].preset, records[0].iteration) == ("_default", 0)


def test_missing_iteration_evidence_is_not_carried_forward():
    lines = _session("HPX_TRANSFER_STATS=1,2,3,4,5,6")
    lines[-1:] = [s.replace("ITER 0", "ITER 1") for s in _session()[2:]]
    records = parse_firmware_output(lines).transfer_evidence
    assert len(records) == 1 and records[0].iteration == 0


@pytest.mark.parametrize("output_format", ["csv", "json"])
def test_saved_bundle_contains_evidence_in_both_output_modes(tmp_path, output_format):
    config = load_config(
        None,
        {
            "model": {"path": "test.tflite"},
            "engine": {"type": "helia-aot"},
            "output": {"dir": tmp_path, "format": output_format, "model_explorer": False},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.run_metadata = RunMetadata(
        hpx_version="test", run_id="transfer-evidence", timestamp="2026-11-14T00:00:00Z"
    )
    set_profile_result(ctx, parse_firmware_output(FIXTURE.read_text().splitlines()))
    lock = tmp_path / "source.lock"
    lock.write_text("schema_version: 4\ntargets: {}\n")
    ctx.dependency_lock_path = lock
    paths = write_report(ctx)
    doc = json.loads((tmp_path / "profile_results.json").read_text())
    assert len(doc["transfer_evidence"]) == 4
    assert doc["transfer_evidence"][0]["refills"]["units"]["0"] == 3
    assert (tmp_path / "profile_results.csv").exists() == (output_format == "csv")
    manifest = load_result_manifest(paths[-1], verify=True)
    artifact = next(a for a in manifest.artifacts if a.path == "profile_results.json")
    assert artifact.schema == "hpx.profile-results" and artifact.schema_version == 1
    Draft202012Validator(json.loads(SCHEMA.read_text())).validate(doc)

    if output_format == "csv":
        set_profile_result(ctx, parse_firmware_output(_session()))
        write_report(ctx)
        assert not (tmp_path / "profile_results.json").exists()
