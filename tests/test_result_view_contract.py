"""The producer projection conforms to the pinned BENCH-owned draft, not a copied model."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from helia_profiler.api import load_result, observations

from .test_records import bundle, edit_document

CONTRACT = Path(__file__).parent / "fixtures" / "result_contract_v1"


@pytest.mark.parametrize("workload", [None, "unknown", "aot_raw_zero_refill_included_v1"])
def test_timing_basis_resolves_retained_count_and_scope(tmp_path, workload):
    path = bundle(tmp_path)

    def update(data):
        data["latency"].update(device_clean_infer_avg_us=2500, device_clean_infer_count=3)
        if workload is not None:
            data["latency"]["clean_workload"] = workload

    edit_document(path, "summary.json", update)
    view = observations(load_result(path))
    items = {item["id"]: item for item in view["observations"]}
    profiled = items["latency.profiled_invoke.v1"]
    assert {s["pointer"] for s in profiled["basis"]["sources"]} == {
        "/latency/device_profiled_infer_avg_us",
        "/latency/device_profiled_infer_count",
    }
    clean = items["latency.restore_invoke.v1"]
    if workload == "aot_raw_zero_refill_included_v1":
        assert clean["value"] == 2.5
        assert "/latency/clean_workload" in {s["pointer"] for s in clean["basis"]["sources"]}
    else:
        assert clean["value"] is None
        assert clean["unavailable_reason"] == "clean_workload_scope_not_recorded"
    artifacts = {a["id"]: a for a in view["native_result"]["artifacts"]}
    for item in view["observations"]:
        if not item["basis"]:
            continue
        for source in item["basis"]["sources"]:
            data = json.loads((tmp_path / artifacts[source["artifact"]]["location"]).read_bytes())
            for part in source["pointer"].split("/")[1:]:
                data = data[part.replace("~1", "/").replace("~0", "~")]
    Draft202012Validator(json.loads((CONTRACT / "result.schema.json").read_bytes())).validate(view)


def test_native_mapping_matches_authoritative_wire_shape(tmp_path):
    schema = json.loads((CONTRACT / "result.schema.json").read_bytes())
    view = observations(load_result(bundle(tmp_path)))
    Draft202012Validator(schema).validate(view)
    artifacts = {ref["id"] for ref in view["native_result"]["artifacts"]}
    assert len(artifacts) == len(view["native_result"]["artifacts"])
    assert len({item["id"] for item in view["observations"]}) == len(view["observations"])
    for item in view["observations"]:
        assert set(item["evidence"]) <= artifacts
        if item["basis"]:
            for source in item["basis"]["sources"]:
                assert source["artifact"] in artifacts


def test_cli_inspection_matches_authoritative_envelope(tmp_path):
    from typer.testing import CliRunner

    from helia_profiler.cli.results_cmd import app

    result = json.loads((CONTRACT / "result.schema.json").read_bytes())
    schema = json.loads((CONTRACT / "inspection.schema.json").read_bytes())
    registry = Registry().with_resource(result["$id"], Resource.from_contents(result))
    validator = Draft202012Validator(schema, registry=registry)
    path = bundle(tmp_path)
    for valid in (True, False):
        if not valid:
            (tmp_path / "summary.json").write_text("{}")
        invocation = CliRunner().invoke(app, ["inspect", str(path)])
        assert invocation.exit_code == (0 if valid else 1)
        validator.validate(json.loads(invocation.stdout))


def test_the_vendored_contract_is_the_recorded_bench_commit():
    import hashlib

    origin = json.loads((CONTRACT / "ORIGIN.json").read_bytes())
    for name, entry in origin["files"].items():
        assert hashlib.sha256((CONTRACT / name).read_bytes()).hexdigest() == entry["sha256"]
        assert entry["source"] == f"AmbiqAI/helia-benchmark@{origin['commit'][:7]}:" + (
            f"contracts/results/v1/{name}"
        )
    schema = json.loads((CONTRACT / "result.schema.json").read_bytes())
    example = json.loads((CONTRACT / "examples" / "hpx.json").read_bytes())
    Draft202012Validator(schema).validate(example)
