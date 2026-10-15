"""Native evidence controls use independently constructed, synthetic saved bytes."""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path, PureWindowsPath

import pytest

from helia_profiler.api import inspect_result, load_result
from helia_profiler.records import ResultReadError
from helia_profiler.results.run_summary import RUN_SUMMARY_SCHEMA_VERSION


def test_public_reader_import_is_hardware_free(tmp_path):
    probe = tmp_path / "offline_import.py"
    probe.write_text("""
import sys
from helia_profiler.api import load_result, inspect_result, observations, export_result
from helia_profiler.cli import main
try:
    main(["results", "--help"])
except SystemExit as exc:
    assert exc.code == 0
forbidden = ("pylink", "pyjoulescope_driver", "neuralspotx", "helia_profiler.target",
             "helia_profiler.power.joulescope", "helia_profiler.profiler")
assert not [name for name in sys.modules if name.startswith(forbidden)]
""")
    guard = Path(__file__).resolve().parents[1] / "tools" / "software_only.py"
    result = subprocess.run(
        [sys.executable, str(guard), "python", str(probe)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def bundle(root: Path, *, status="complete") -> Path:
    root.mkdir(exist_ok=True)
    documents = {
        "summary.json": (
            "hpx.summary",
            {
                "schema": "hpx.run-summary",
                "schema_version": RUN_SUMMARY_SCHEMA_VERSION,
                "engine": "tflm",
                "layers": 1,
                "total_cycles": 2**53 + 1,
                "overflow_detected": False,
                "latency": {
                    "device_profiled_infer_count": 3,
                    "device_profiled_infer_total_us": 9000,
                    "device_profiled_infer_avg_us": 3000,
                },
                "memory": {"arena_size": 32768, "allocated_arena": 24000},
            },
        ),
        "run_metadata.json": (
            "hpx.run-metadata",
            {
                "schema": "hpx.run-metadata",
                "schema_version": 1,
                "run_id": "synthetic-run",
                "hpx_version": "synthetic",
                "engine": {"type": "tflm"},
            },
        ),
        "layers.json": ("hpx.profile-layers", {"layers": [{"cycles": 2**53 + 1}]}),
    }
    artifacts = []
    for name, (role, data) in documents.items():
        raw = json.dumps(data).encode()
        (root / name).write_bytes(raw)
        artifacts.append(
            {
                "path": name,
                "name": role,
                "optional": False,
                "media_type": "application/json",
                "size_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    manifest = {
        "schema": "hpx.result-manifest",
        "schema_version": 1,
        "bundle_type": "profile",
        "run_id": "synthetic-run",
        "timestamp": "2026-10-15T00:00:00Z",
        "hpx_version": "synthetic",
        "status": status,
        "validity": "valid",
        "issues": [],
        "provenance": {},
        "comparability": {},
        "artifacts": artifacts,
    }
    path = root / "result_manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def edit_manifest(path, edit):
    data = json.loads(path.read_bytes())
    edit(data)
    path.write_text(json.dumps(data))


def edit_document(path, name, edit):
    item = path.parent / name
    data = json.loads(item.read_bytes())
    edit(data)
    raw = json.dumps(data).encode()
    item.write_bytes(raw)

    def update(manifest):
        ref = next(a for a in manifest["artifacts"] if a["path"] == name)
        ref.update(size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())

    edit_manifest(path, update)


def test_complete_relocated_and_exact_integer(tmp_path):
    path = bundle(tmp_path / "first")
    first = load_result(path)
    shutil.copytree(path.parent, tmp_path / "moved")
    moved = load_result(tmp_path / "moved")
    assert moved.manifest_sha256 == first.manifest_sha256
    assert moved.summary is not None
    assert moved.summary.latency is not None
    assert moved.summary.memory is not None
    assert moved.summary.total_cycles == 9007199254740993
    assert moved.summary.latency.device_profiled_infer_avg_us == 3000
    assert moved.summary.memory.allocated_arena == 24000


def test_verified_failure_is_not_evidence_failure(tmp_path):
    path = bundle(tmp_path, status="failed")
    edit_manifest(
        path,
        lambda d: d.update(
            artifacts=[],
            validity="invalid",
            issues=[
                {"code": "capture.failure", "severity": "error", "message": "retained failure"}
            ],
        ),
    )
    result = inspect_result(path)
    assert result.code is None
    assert result.result is not None
    assert result.result.manifest.status.value == "failed"
    assert result.result.summary is None


@pytest.mark.parametrize("marker", ["file", "dangling_symlink"])
def test_publication_marker_overrides_previous_valid_manifest(tmp_path, marker):
    path = bundle(tmp_path)
    pending = tmp_path / ".result-publication-incomplete"
    pending.write_text("") if marker == "file" else pending.symlink_to("absent")
    assert inspect_result(path).code == "incomplete_publication"


@pytest.mark.parametrize("change", ["bytes", "missing", "escape", "duplicate", "duplicate_role"])
def test_integrity_refusals(tmp_path, change):
    path = bundle(tmp_path / "run")
    if change == "bytes":
        (path.parent / "layers.json").write_text("{}")
    elif change == "missing":
        (path.parent / "summary.json").unlink()
    elif change == "escape":
        source = path.parent / "summary.json"
        source.rename(tmp_path / "outside.json")
        source.symlink_to(tmp_path / "outside.json")
    elif change == "duplicate":
        edit_manifest(path, lambda d: d["artifacts"].append(d["artifacts"][0].copy()))
    else:
        shutil.copyfile(path.parent / "summary.json", path.parent / "copy.json")
        edit_manifest(
            path, lambda d: d["artifacts"].append(dict(d["artifacts"][0], path="copy.json"))
        )
    assert inspect_result(path).result is None
    with pytest.raises(ResultReadError):
        load_result(path)


@pytest.mark.parametrize("value", ["../summary.json", "C:/summary.json", "a\\b", "./summary.json"])
def test_cross_platform_containment(tmp_path, value):
    path = bundle(tmp_path)
    edit_manifest(path, lambda d: d["artifacts"][0].update(path=value))
    checked = inspect_result(path)
    assert checked.result is None
    # Native manifest validation rejects host-absolute paths before portability checks.
    if Path(value).is_absolute():
        assert checked.code == "invalid_evidence"
        assert checked.message is not None
        assert "non-empty relative path" in checked.message
    else:
        assert checked.code == "invalid_path"


@pytest.mark.parametrize(
    "key,value", [("run_id", "other"), ("hpx_version", "other"), ("engine", {"type": "helia-aot"})]
)
def test_hash_valid_metadata_must_bind_native_identity(tmp_path, key, value):
    path = bundle(tmp_path)
    edit_document(path, "run_metadata.json", lambda d: d.update({key: value}))
    assert inspect_result(path).code == "identity_mismatch"


@pytest.mark.parametrize(
    "text,code",
    [
        ('{"schema":1,"schema":2}', "duplicate_key"),
        ('{"x":1e999}', "invalid_number"),
        ('{"x":NaN}', "invalid_number"),
    ],
)
def test_ambiguous_json_rejected(tmp_path, text, code):
    path = tmp_path / "result_manifest.json"
    path.write_text(text)
    assert inspect_result(path).code == code


def test_no_implicit_legacy_or_fixture_adoption(tmp_path):
    path = bundle(tmp_path)
    edit_manifest(path, lambda d: d.update(bundle_type="fixture"))
    assert inspect_result(path).code == "unsupported_kind"
    path.unlink()
    assert inspect_result(tmp_path).result is None


def test_incomplete_status_is_not_a_verified_failure(tmp_path):
    assert inspect_result(bundle(tmp_path, status="incomplete")).code == "incomplete_result"


@pytest.mark.parametrize("version", [True, 10, 13])
def test_unsupported_summary_semantics_are_explicit(tmp_path, version):
    path = bundle(tmp_path)
    edit_document(path, "summary.json", lambda d: d.update(schema_version=version))
    assert inspect_result(path).code == "unsupported_schema"


@pytest.mark.parametrize("value", [True, 0.5, -1])
def test_memory_bytes_are_not_coerced(tmp_path, value):
    path = bundle(tmp_path)
    edit_document(path, "summary.json", lambda d: d["memory"].update(arena_size=value))
    assert inspect_result(path).code == "invalid_summary"


def test_view_keeps_scope_counts_missing_memory_and_identity(tmp_path):
    from helia_profiler.api import observations

    path = bundle(tmp_path)
    view = observations(load_result(path))
    items = {item["id"]: item for item in view["observations"]}
    timing = items["latency.profiled_invoke.v1"]
    assert (timing["value"], timing["unit"], timing["sample_count"], timing["scope"]) == (
        3.0,
        "ms",
        3,
        "profiled_invoke",
    )
    assert items["latency.restore_invoke.v1"]["value"] is None
    assert items["memory.linked_bss.v1"]["unavailable_reason"] == "not_recorded"
    assert items["memory.arena_capacity.v1"]["value"] == 32768
    assert items["memory.arena_allocated.v1"]["value"] == 24000
    assert view["workload"]["input_identity"] is None
    assert view["producer"]["revision"] is None


def test_failed_run_never_exports_completed_measurements(tmp_path):
    from helia_profiler.api import observations

    view = observations(load_result(bundle(tmp_path, status="failed")))
    assert view["outcome"]["status"] == "failed"
    assert all(item["value"] is None for item in view["observations"])


def test_failed_run_with_zero_completed_invocations_stays_readable(tmp_path):
    from helia_profiler.api import observations

    path = bundle(tmp_path, status="failed")
    edit_document(
        path,
        "summary.json",
        lambda d: d["latency"].update(
            device_profiled_infer_count=0, device_profiled_infer_avg_us=None
        ),
    )
    view = observations(load_result(path))
    timing = view["observations"][0]
    assert view["outcome"]["status"] == "failed"
    assert timing["value"] is None
    assert timing["sample_count"] is None
    assert timing["unavailable_reason"] == "native_run_not_complete"


def test_completed_run_with_zero_count_cannot_export_a_mean(tmp_path):
    from helia_profiler.api import observations

    path = bundle(tmp_path)
    edit_document(
        path, "summary.json", lambda d: d["latency"].update(device_profiled_infer_count=0)
    )
    with pytest.raises(ResultReadError, match="count must be positive"):
        observations(load_result(path))


def test_view_preserves_large_byte_integer_and_clock_evidence(tmp_path):
    from helia_profiler.api import observations

    path = bundle(tmp_path)
    edit_document(path, "summary.json", lambda d: d["memory"].update(arena_size=2**53 + 1))
    edit_document(
        path,
        "run_metadata.json",
        lambda d: d.update(firmware={"system_clock_hz": 96000000, "measured_clock_hz": 95976631}),
    )
    view = observations(load_result(path))
    items = {item["id"]: item for item in view["observations"]}
    assert items["memory.arena_capacity.v1"]["value"] == 9007199254740993
    assert type(items["memory.arena_capacity.v1"]["value"]) is int
    timing = items["latency.profiled_invoke.v1"]
    assert "artifact:run_metadata.json" in timing["evidence"]
    assert [(c["role"], c["value_hz"]) for c in timing["basis"]["clocks"]] == [
        ("reported", 96000000),
        ("observed", 95976631),
    ]


def test_export_cannot_overwrite_native_or_existing_output(tmp_path):
    from helia_profiler.api import export_result

    path = bundle(tmp_path)
    record = load_result(path)
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        export_result(record, path)
    assert path.read_bytes() == before
    out = tmp_path / "export.json"
    export_result(record, out)
    assert json.loads(out.read_bytes())["schema_version"] == "1.0-draft"


def test_result_cli_uses_same_reader_without_operational_imports(tmp_path):
    from typer.testing import CliRunner

    from helia_profiler.api import observations
    from helia_profiler.cli.results_cmd import app

    path = bundle(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["show", str(path)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == observations(load_result(path))
    (tmp_path / ".result-publication-incomplete").write_text("")
    result = runner.invoke(app, ["inspect", str(path)])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["result"] is None
    assert json.loads(result.stdout)["status"] == "incomplete"


@pytest.mark.parametrize("kind", ["verdict", "model"])
def test_contradictory_native_identity_cannot_export_metrics(tmp_path, kind):
    path = bundle(tmp_path)
    if kind == "verdict":
        edit_document(path, "summary.json", lambda d: d.update(validity="invalid"))
    else:
        edit_manifest(path, lambda d: d["provenance"].update(model={"sha256": "a" * 64}))
        edit_document(path, "run_metadata.json", lambda d: d.update(model={"sha256": "b" * 64}))
    assert inspect_result(path).code == "identity_mismatch"


def test_extreme_latency_inspection_is_structured(tmp_path):
    from typer.testing import CliRunner

    from helia_profiler.cli.results_cmd import app

    path = bundle(tmp_path)
    edit_document(
        path, "summary.json", lambda d: d["latency"].update(device_profiled_infer_avg_us=10**400)
    )
    invocation = CliRunner().invoke(app, ["inspect", str(path)])
    assert invocation.exit_code == 1
    response = json.loads(invocation.stdout)
    assert response["status"] == "invalid"
    assert response["result"] is None
    assert response["issues"][0]["code"] == "invalid_measurement"


@pytest.mark.parametrize("implementation_first", [False, True])
def test_cli_application_supported_import_orders(tmp_path, implementation_first):
    probe = tmp_path / "cli_export.py"
    probe.write_text("""
import sys, importlib.abc, importlib.util
sentinel = object()
class AppStub(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path, target=None):
        if fullname == "helia_profiler.cli.app":
            return importlib.util.spec_from_loader(fullname, self)
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        module.app = sentinel
sys.meta_path.insert(0, AppStub())
if sys.argv[1] == "True":
    from helia_profiler.cli.app import app as first
else:
    from helia_profiler.cli import app as first
    from helia_profiler.cli import app as repeated
    assert repeated is first
from helia_profiler.cli.app import app as second
from helia_profiler.cli.app import app as third
assert first is second is third is sentinel
""")
    guard = Path(__file__).resolve().parents[1] / "tools" / "software_only.py"
    result = subprocess.run(
        [sys.executable, str(guard), "python", str(probe), str(implementation_first)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "change", ["message", "code", "severity", "context", "extension", "missing", "extra"]
)
def test_summary_issue_contradiction_refuses_verified_bundle(tmp_path, change):
    path = bundle(tmp_path)
    issue = {
        "code": "clock.warning",
        "severity": "warning",
        "message": "retained warning",
        "context": {"clock": 1},
        "future": 7,
    }
    edit_manifest(path, lambda d: d.update(validity="degraded", issues=[issue]))
    other = dict(issue)
    if change == "context":
        other["context"] = {"clock": 2}
    elif change == "extension":
        other["future"] = 8
    elif change not in ("missing", "extra"):
        other[change] = "different"
    issues = [] if change == "missing" else [other, other] if change == "extra" else [other]
    edit_document(path, "summary.json", lambda d: d.update(validity="degraded", issues=issues))
    assert inspect_result(path).code == "identity_mismatch"


@pytest.mark.parametrize("issues", [None, {}, "warning", [None], [{"code": "x"}]])
def test_malformed_present_summary_issues_refuse(tmp_path, issues):
    path = bundle(tmp_path)
    edit_document(path, "summary.json", lambda d: d.update(issues=issues))
    assert inspect_result(path).result is None


def test_matching_normalized_issues_and_missing_summary_issues(tmp_path):
    from helia_profiler.api import observations

    path = bundle(tmp_path)
    issue = {
        "code": "clock.warning",
        "severity": "warning",
        "message": "retained warning",
        "future": {"value": 7},
    }
    edit_manifest(path, lambda d: d.update(validity="degraded", issues=[issue]))
    # Older summaries may omit issues; no missing evidence is invented.
    assert inspect_result(path).code is None
    edit_document(
        path,
        "summary.json",
        lambda d: d.update(validity="degraded", issues=[dict(issue, context={})]),
    )
    record = load_result(path)
    assert observations(record)["outcome"]["reason"] == "retained warning"
    assert record.manifest.issues[0].extra == {"future": {"value": 7}}


@pytest.mark.parametrize(
    "field,section,key,value",
    [
        ("engine", "engine", "type", "tflm"),
        ("model_sha256", "model", "sha256", "a" * 64),
        ("board", "platform", "board", "apollo510_evb"),
        ("engine_version", "engine", "version", "1.0"),
    ],
)
@pytest.mark.parametrize(
    "case",
    [
        "matching",
        "contradictory",
        "manifest_null",
        "native_null",
        "manifest_missing",
        "native_missing",
    ],
)
def test_comparability_overlapping_identity(tmp_path, field, section, key, value, case):
    path = bundle(tmp_path)
    native = None if case == "native_null" else value
    if case != "native_missing":
        edit_document(
            path, "run_metadata.json", lambda d: d.setdefault(section, {}).update({key: native})
        )
    elif field == "engine":
        edit_document(path, "run_metadata.json", lambda d: d.pop("engine"))
    declared = (
        "different" if case == "contradictory" else None if case == "manifest_null" else value
    )
    if case != "manifest_missing":
        edit_manifest(path, lambda d: d["comparability"].update({field: declared}))
    checked = inspect_result(path)
    assert checked.code == ("identity_mismatch" if case == "contradictory" else None)


def test_comparability_checks_summary_without_metadata(tmp_path):
    path = bundle(tmp_path, status="failed")
    edit_manifest(
        path,
        lambda d: d.update(
            artifacts=[a for a in d["artifacts"] if a["name"] != "hpx.run-metadata"],
            comparability={"engine": "helia-aot"},
        ),
    )
    assert inspect_result(path).code == "identity_mismatch"


def test_comparability_model_checks_retained_provenance(tmp_path):
    path = bundle(tmp_path)
    edit_manifest(
        path,
        lambda d: d.update(
            provenance={"model": {"sha256": "a" * 64}},
            comparability={"model_sha256": "b" * 64},
        ),
    )
    assert inspect_result(path).code == "identity_mismatch"


@pytest.mark.parametrize(
    "field,path", [("engine", ("engine", "type")), ("board", ("target", "board"))]
)
def test_native_config_identity_also_agrees_without_manifest_override(tmp_path, field, path):
    manifest = bundle(tmp_path)
    edit_document(
        manifest,
        "run_metadata.json",
        lambda d: d.update(
            platform={"board": "apollo510_evb"},
            config={path[0]: {path[1]: "different"}},
        ),
    )
    assert inspect_result(manifest).code == "identity_mismatch"


def test_windows_native_path_rejection_precedes_portable_checks(tmp_path, monkeypatch):
    import helia_profiler.results.manifest as native_manifest

    path = bundle(tmp_path)
    edit_manifest(path, lambda d: d["artifacts"][0].update(path="C:/summary.json"))
    monkeypatch.setattr(native_manifest, "Path", PureWindowsPath)
    checked = inspect_result(path)
    assert checked.result is None
    assert checked.code == "invalid_evidence"
    assert checked.message is not None
    assert "non-empty relative path" in checked.message
