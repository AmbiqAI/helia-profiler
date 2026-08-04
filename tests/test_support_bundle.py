"""Tests for the ``hpx doctor --bundle`` field-diagnostics support archive.

Covers: collector partial failure (missing workspace/config/tools), offline
operation, opt-in raw probe identifiers, exact Stage 5 dependency lock
provenance inclusion, deterministic archive naming/manifest/member order,
archive self-verification, and rejection of malformed/hostile archives.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from unittest import mock

import pytest
from neuralspotx.nsx_lock import LockKind, NsxLock, ResolvedModule, hash_manifest, write_lock

from helia_profiler.config import load_config
from helia_profiler.dependencies import create_workspace, prepare_locked_dependencies
from helia_profiler.engines.base import EngineArtifacts
from helia_profiler.errors import ReportError
from helia_profiler.pipeline import PipelineContext
from helia_profiler.results.support_bundle import (
    SUPPORT_BUNDLE_SCHEMA,
    SUPPORT_BUNDLE_SCHEMA_VERSION,
    SupportBundleManifest,
    SupportBundleSection,
)
from helia_profiler.results import ResultArtifact
from helia_profiler.stages.resolve_platform import ResolvePlatformStage
from helia_profiler.support_bundle import (
    SupportBundleOptions,
    collect_support_bundle,
    content_fingerprint,
    verify_support_bundle,
    write_support_bundle,
)

pytestmark = pytest.mark.timeout(30)


# ---------------------------------------------------------------------------
# Shared fixture helpers (mirrors tests/test_dependencies.py's _context /
# _write_valid_lock, kept local so this file has no cross-test-module
# coupling).
# ---------------------------------------------------------------------------


def _prepared_workspace(tmp_path: Path, *, credentialed_url: bool = False) -> Path:
    """Build a real dependency workspace with a frozen exact lock, offline-safe.

    Returns the ``profiler_app`` directory suitable for
    ``SupportBundleOptions(workspace=...)``.
    """
    model = tmp_path / "model.tflite"
    model.write_bytes(b"TFL3")
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "tflm"},
            "target": {"board": "apollo510_evb"},
            "work_dir": str(tmp_path / "work"),
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path / "work")
    ResolvePlatformStage().run(ctx)
    ctx.engine_artifacts = EngineArtifacts()
    ctx.dependency_workspace = create_workspace(ctx)
    ctx.firmware_dir = ctx.dependency_workspace.root / "profiler_app"
    ctx.firmware_dir.mkdir(parents=True, exist_ok=True)
    (ctx.firmware_dir / "nsx.yml").write_text(
        "target:\n  board: apollo510_evb\nmodules:\n  - name: demo\n", encoding="utf-8"
    )
    module_dir = ctx.firmware_dir / "modules" / "demo"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "nsx-module.yaml").write_text("name: demo\n", encoding="utf-8")

    url = (
        "https://user:hunter2@example.invalid/demo.git"
        if credentialed_url
        else "https://example.invalid/demo.git"
    )
    lock = NsxLock(
        generated_at="2026-08-03T00:00:00+00:00",
        nsx_tool_version="0.7.10",
        manifest_hash=hash_manifest(ctx.firmware_dir / "nsx.yml"),
        target={"board": "apollo510_evb"},
        modules={
            "demo": ResolvedModule(
                project="demo-project",
                kind=LockKind.GIT,
                constraint="v1.2.3",
                tag="v1.2.3",
                commit="a" * 40,
                url=url,
                vendored_at="modules/demo",
                content_hash="sha256:" + "b" * 64,
                acquired_at="2026-08-03T00:00:00+00:00",
            )
        },
    )
    write_lock(ctx.firmware_dir, lock, "apollo510_evb")

    with mock.patch("helia_profiler.dependencies.nsx_cli.sync", lambda *a, **kw: None):
        prepare_locked_dependencies(ctx)

    assert ctx.firmware_dir is not None
    return ctx.firmware_dir


def _section(options: SupportBundleOptions, name: str) -> SupportBundleSection:
    collection = collect_support_bundle(options)
    section = collection.manifest.section(name)
    assert section is not None, f"missing section {name!r}"
    return section


# ---------------------------------------------------------------------------
# Offline operation and collector partial failure — no workspace, no config,
# no probes/ports, no network.
# ---------------------------------------------------------------------------


def test_collect_support_bundle_fully_offline_never_raises() -> None:
    options = SupportBundleOptions(include_probes=False, include_ports=False)

    collection = collect_support_bundle(options)

    names = {section.name for section in collection.manifest.sections}
    assert names == {
        "checks",
        "compatibility",
        "dependencies",
        "nsx.lock",
        "modules",
        "config",
        "probes",
        "ports",
    }


def test_collect_support_bundle_marks_missing_workspace_unavailable() -> None:
    options = SupportBundleOptions(include_probes=False, include_ports=False)

    collection = collect_support_bundle(options)

    dependencies = collection.manifest.section("dependencies")
    lock_section = collection.manifest.section("nsx.lock")
    assert dependencies is not None and not dependencies.available
    assert "no --workspace given" in dependencies.reason
    assert lock_section is not None and not lock_section.available
    assert "dependencies.json" not in collection.members
    assert "nsx.lock" not in collection.members


def test_collect_support_bundle_marks_unpreparred_workspace_unavailable(tmp_path: Path) -> None:
    empty_dir = tmp_path / "not-prepared"
    empty_dir.mkdir()
    options = SupportBundleOptions(
        workspace=empty_dir, include_probes=False, include_ports=False
    )

    collection = collect_support_bundle(options)

    dependencies = collection.manifest.section("dependencies")
    assert dependencies is not None and not dependencies.available
    assert dependencies.reason  # a human-readable LockError message


def test_collect_support_bundle_marks_missing_config_unavailable() -> None:
    options = SupportBundleOptions(include_probes=False, include_ports=False)

    section = _section(options, "config")

    assert not section.available
    assert "no --config given" in section.reason


def test_collect_support_bundle_marks_unresolvable_config_unavailable(tmp_path: Path) -> None:
    bad_config = tmp_path / "bad.yml"
    bad_config.write_text("target:\n  board: apollo510_evb\n", encoding="utf-8")
    options = SupportBundleOptions(
        config_path=bad_config, include_probes=False, include_ports=False
    )

    section = _section(options, "config")

    assert not section.available
    assert "model.path" in section.reason


def test_collect_support_bundle_no_probes_no_ports_flags() -> None:
    options = SupportBundleOptions(include_probes=False, include_ports=False)

    collection = collect_support_bundle(options)

    probes = collection.manifest.section("probes")
    ports = collection.manifest.section("ports")
    assert probes is not None and not probes.available and probes.reason == "--no-probes"
    assert ports is not None and not ports.available and ports.reason == "--no-ports"


def test_collect_support_bundle_marks_probes_unavailable_without_hardware(monkeypatch) -> None:
    from helia_profiler.errors import CaptureError

    def _raise() -> list:
        raise CaptureError("JLinkExe not found")

    monkeypatch.setattr("helia_profiler.target.probe.jlink.find_jlink_exe", _raise)
    options = SupportBundleOptions(include_probes=True, include_ports=False)

    section = _section(options, "probes")

    assert not section.available
    assert section.reason


def test_collect_support_bundle_marks_ports_unavailable_when_pyserial_missing(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "serial.tools" or name.startswith("serial"):
            raise ImportError("no module named serial")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    options = SupportBundleOptions(include_probes=False, include_ports=True)

    section = _section(options, "ports")

    assert not section.available
    assert section.reason


def test_collect_support_bundle_always_includes_checks_and_compatibility() -> None:
    options = SupportBundleOptions(include_probes=False, include_ports=False)

    collection = collect_support_bundle(options)

    checks = collection.manifest.section("checks")
    compatibility = collection.manifest.section("compatibility")
    assert checks is not None and checks.available
    assert compatibility is not None and compatibility.available
    checks_payload = json.loads(collection.members["checks.json"])
    assert "checks" in checks_payload and "versions" in checks_payload
    compatibility_payload = json.loads(collection.members["compatibility.json"])
    assert compatibility_payload["baseline_id"]


# ---------------------------------------------------------------------------
# Exact Stage 5 dependency lock provenance inclusion.
# ---------------------------------------------------------------------------


def test_collect_support_bundle_includes_exact_stage5_provenance(tmp_path: Path) -> None:
    app_dir = _prepared_workspace(tmp_path)
    options = SupportBundleOptions(workspace=app_dir, include_probes=False, include_ports=False)

    collection = collect_support_bundle(options)

    dependencies = collection.manifest.section("dependencies")
    lock_section = collection.manifest.section("nsx.lock")
    assert dependencies is not None and dependencies.available
    assert lock_section is not None and lock_section.available

    payload = json.loads(collection.members["dependencies.json"])
    assert payload["lock_mode"] == "reused" or payload["lock_mode"] == "resolved"
    assert payload["resolved"][0]["peeled_commit"] == "a" * 40
    assert payload["resolved"][0]["content_hash"]["value"] == "b" * 64
    assert len(payload["lock_sha256"]) == 64
    assert len(payload["baseline_fingerprint"]) == 64
    assert len(payload["workspace_fingerprint"]) == 64

    # The exact on-disk nsx.lock bytes are embedded, not just a summary.
    exact_lock_bytes = (app_dir / "nsx.lock").read_bytes()
    assert collection.members["nsx.lock"] == exact_lock_bytes


def test_collect_support_bundle_redacts_credentials_in_embedded_lock(tmp_path: Path) -> None:
    app_dir = _prepared_workspace(tmp_path, credentialed_url=True)
    options = SupportBundleOptions(workspace=app_dir, include_probes=False, include_ports=False)

    collection = collect_support_bundle(options)

    lock_text = collection.members["nsx.lock"].decode("utf-8")
    assert "hunter2" not in lock_text
    assert "<redacted>@example.invalid" in lock_text
    assert collection.manifest.redaction["urls"] >= 1


def test_collect_support_bundle_module_inventory_includes_baseline_and_resolved(
    tmp_path: Path,
) -> None:
    app_dir = _prepared_workspace(tmp_path)
    options = SupportBundleOptions(workspace=app_dir, include_probes=False, include_ports=False)

    collection = collect_support_bundle(options)

    modules = json.loads(collection.members["modules.json"])
    assert "nsx-pmu-armv8m" in modules["baseline"]
    assert "demo" in modules["resolved"]
    assert modules["resolved"]["demo"]["peeled_commit"] == "a" * 40


def test_collect_support_bundle_module_inventory_baseline_only_without_workspace() -> None:
    options = SupportBundleOptions(include_probes=False, include_ports=False)

    collection = collect_support_bundle(options)

    modules = json.loads(collection.members["modules.json"])
    assert modules["baseline"]
    assert modules["resolved"] == {}


# ---------------------------------------------------------------------------
# Opt-in raw probe identifiers.
# ---------------------------------------------------------------------------


def test_collect_support_bundle_redacts_probe_serials_by_default(monkeypatch) -> None:
    from helia_profiler.target.probe.jlink import JLinkProbe

    monkeypatch.setattr(
        "helia_profiler.target.probe.jlink.list_connected_probes",
        lambda: [JLinkProbe(serial="1160002204", product="J-Link OB", connection="USB")],
    )
    options = SupportBundleOptions(include_probes=True, include_ports=False, raw_probe_ids=False)

    collection = collect_support_bundle(options)

    payload = json.loads(collection.members["probes.json"])
    assert payload[0]["serial"] != "1160002204"
    assert payload[0]["serial"].startswith("<redacted-serial:")
    assert collection.manifest.redaction["raw_probe_ids"] is False
    assert collection.manifest.redaction["serials"] >= 1


def test_collect_support_bundle_raw_probe_ids_opt_in_keeps_serial(monkeypatch) -> None:
    from helia_profiler.target.probe.jlink import JLinkProbe

    monkeypatch.setattr(
        "helia_profiler.target.probe.jlink.list_connected_probes",
        lambda: [JLinkProbe(serial="1160002204", product="J-Link OB", connection="USB")],
    )
    options = SupportBundleOptions(include_probes=True, include_ports=False, raw_probe_ids=True)

    collection = collect_support_bundle(options)

    payload = json.loads(collection.members["probes.json"])
    assert payload[0]["serial"] == "1160002204"
    assert collection.manifest.redaction["raw_probe_ids"] is True


# ---------------------------------------------------------------------------
# No forbidden extensions/content — structural allowlist.
# ---------------------------------------------------------------------------


def test_collect_support_bundle_members_are_json_or_exact_lock_only(tmp_path: Path) -> None:
    app_dir = _prepared_workspace(tmp_path)
    options = SupportBundleOptions(workspace=app_dir, include_probes=False, include_ports=False)

    collection = collect_support_bundle(options)

    for name in collection.members:
        assert name == "nsx.lock" or name.endswith(".json")
        assert not name.endswith((".tflite", ".elf", ".bin", ".axf", ".hex", ".o", ".a"))
    for artifact in collection.manifest.artifacts:
        assert artifact.path == "nsx.lock" or artifact.path.endswith(".json")


# ---------------------------------------------------------------------------
# Deterministic archive naming, member order, and manifest content.
# ---------------------------------------------------------------------------


def test_content_fingerprint_is_deterministic_for_identical_members() -> None:
    members_a = {"a.json": b'{"x": 1}', "b.json": b'{"y": 2}'}
    members_b = {"b.json": b'{"y": 2}', "a.json": b'{"x": 1}'}

    assert content_fingerprint(members_a) == content_fingerprint(members_b)


def test_content_fingerprint_changes_when_content_changes() -> None:
    members_a = {"a.json": b'{"x": 1}'}
    members_b = {"a.json": b'{"x": 2}'}

    assert content_fingerprint(members_a) != content_fingerprint(members_b)


def test_write_support_bundle_is_deterministic_across_runs(tmp_path: Path) -> None:
    options = SupportBundleOptions(include_probes=False, include_ports=False)
    collection_a = collect_support_bundle(options)
    collection_b = collect_support_bundle(options)

    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    path_a = write_support_bundle(collection_a, out_a)
    path_b = write_support_bundle(collection_b, out_b)

    assert path_a.name == path_b.name

    with zipfile.ZipFile(path_a) as za, zipfile.ZipFile(path_b) as zb:
        names_a = za.namelist()
        names_b = zb.namelist()
        assert names_a == names_b
        for name in names_a:
            if name == "manifest.json":
                continue
            assert za.read(name) == zb.read(name)


def test_write_support_bundle_member_order_is_sorted_with_manifest_last(tmp_path: Path) -> None:
    options = SupportBundleOptions(include_probes=False, include_ports=False)
    collection = collect_support_bundle(options)

    path = write_support_bundle(collection, tmp_path)

    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()

    assert names[-1] == "manifest.json"
    assert names[:-1] == sorted(names[:-1])


def test_write_support_bundle_uses_fixed_zip_metadata_for_determinism(tmp_path: Path) -> None:
    options = SupportBundleOptions(include_probes=False, include_ports=False)
    collection = collect_support_bundle(options)

    path = write_support_bundle(collection, tmp_path)

    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.create_system == 0


def test_write_support_bundle_explicit_zip_path_is_used_verbatim(tmp_path: Path) -> None:
    options = SupportBundleOptions(include_probes=False, include_ports=False)
    collection = collect_support_bundle(options)

    target = tmp_path / "custom-name.zip"
    path = write_support_bundle(collection, target)

    assert path == target
    assert path.is_file()


def test_write_support_bundle_manifest_schema_and_version(tmp_path: Path) -> None:
    options = SupportBundleOptions(include_probes=False, include_ports=False)
    collection = collect_support_bundle(options)

    assert collection.manifest.schema == SUPPORT_BUNDLE_SCHEMA
    assert collection.manifest.schema_version == SUPPORT_BUNDLE_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Archive self-verification.
# ---------------------------------------------------------------------------


def test_verify_support_bundle_round_trips(tmp_path: Path) -> None:
    options = SupportBundleOptions(include_probes=False, include_ports=False)
    collection = collect_support_bundle(options)
    path = write_support_bundle(collection, tmp_path)

    manifest = verify_support_bundle(path)

    assert manifest.hpx_version == collection.manifest.hpx_version
    assert {a.path for a in manifest.artifacts} == {a.path for a in collection.manifest.artifacts}


def test_verify_support_bundle_detects_tampered_member(tmp_path: Path) -> None:
    options = SupportBundleOptions(include_probes=False, include_ports=False)
    collection = collect_support_bundle(options)
    path = write_support_bundle(collection, tmp_path)

    original = collection.members["checks.json"]
    # Same length as the original so this exercises the digest check
    # specifically, not the (equally valid) size-mismatch check.
    tampered = (b"0" * (len(original) - 1)) + b"\n"
    assert len(tampered) == len(original)
    _rewrite_zip_member(path, "checks.json", tampered)

    with pytest.raises(ReportError, match="digest mismatch"):
        verify_support_bundle(path)


def test_verify_support_bundle_detects_size_mismatch(tmp_path: Path) -> None:
    options = SupportBundleOptions(include_probes=False, include_ports=False)
    collection = collect_support_bundle(options)
    path = write_support_bundle(collection, tmp_path)

    _rewrite_zip_member(path, "checks.json", b'{"tampered": true}')

    with pytest.raises(ReportError, match="size mismatch"):
        verify_support_bundle(path)


def test_verify_support_bundle_detects_undeclared_extra_member(tmp_path: Path) -> None:
    options = SupportBundleOptions(include_probes=False, include_ports=False)
    collection = collect_support_bundle(options)
    path = write_support_bundle(collection, tmp_path)

    _add_zip_member(path, "extra.json", b"{}")

    with pytest.raises(ReportError, match="undeclared members"):
        verify_support_bundle(path)


def test_verify_support_bundle_detects_missing_declared_member(tmp_path: Path) -> None:
    options = SupportBundleOptions(include_probes=False, include_ports=False)
    collection = collect_support_bundle(options)
    path = write_support_bundle(collection, tmp_path)

    _remove_zip_member(path, "checks.json")

    with pytest.raises(ReportError, match="missing declared members"):
        verify_support_bundle(path)


def test_verify_support_bundle_requires_manifest(tmp_path: Path) -> None:
    path = tmp_path / "no-manifest.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("checks.json", "{}")

    with pytest.raises(ReportError, match="missing manifest.json"):
        verify_support_bundle(path)


# ---------------------------------------------------------------------------
# Malformed / hostile archive member paths.
# ---------------------------------------------------------------------------


def _minimal_manifest_json() -> str:
    return json.dumps(
        {
            "schema": SUPPORT_BUNDLE_SCHEMA,
            "schema_version": SUPPORT_BUNDLE_SCHEMA_VERSION,
            "hpx_version": "0.0.0",
            "generated_at": "2026-01-01T00:00:00+00:00",
            "host": {},
            "sections": [],
            "redaction": {},
            "artifacts": [],
        }
    )


@pytest.mark.parametrize(
    "hostile_name",
    [
        "../../etc/passwd",
        "/etc/passwd",
        "a/../../b.json",
        "a\\..\\b.json",
        "model.tflite",
        "profiler_app/main.elf",
        "firmware.bin",
    ],
)
def test_verify_support_bundle_rejects_hostile_member_paths(
    tmp_path: Path, hostile_name: str
) -> None:
    path = tmp_path / "hostile.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", _minimal_manifest_json())
        archive.writestr(hostile_name, "evil")

    with pytest.raises(ReportError):
        verify_support_bundle(path)


def test_verify_support_bundle_rejects_duplicate_members(tmp_path: Path) -> None:
    path = tmp_path / "dup.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", _minimal_manifest_json())
        archive.writestr("checks.json", "{}")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("checks.json", "{}")

    with pytest.raises(ReportError, match="duplicate member"):
        verify_support_bundle(path)


def test_verify_support_bundle_rejects_null_byte_in_member_name(tmp_path: Path) -> None:
    path = tmp_path / "nul.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", _minimal_manifest_json())
        # zipfile permits writing an embedded NUL directly to the central
        # directory entry name; verify_support_bundle must still reject it.
        info = zipfile.ZipInfo("checks.json\x00.txt")
        archive.writestr(info, "evil")

    with pytest.raises(ReportError):
        verify_support_bundle(path)


# ---------------------------------------------------------------------------
# SupportBundleManifest contract — schema/version rejection, directory verify.
# ---------------------------------------------------------------------------


def test_support_bundle_manifest_rejects_wrong_schema() -> None:
    with pytest.raises(ReportError, match="Unsupported support bundle schema"):
        SupportBundleManifest(
            schema="not-the-right-schema",
            schema_version=SUPPORT_BUNDLE_SCHEMA_VERSION,
            hpx_version="0.0.0",
            generated_at="2026-01-01T00:00:00+00:00",
            host={},
            sections=(),
            redaction={},
            artifacts=(),
        )


def test_support_bundle_manifest_rejects_wrong_schema_version() -> None:
    with pytest.raises(ReportError, match="Unsupported support bundle schema version"):
        SupportBundleManifest(
            schema=SUPPORT_BUNDLE_SCHEMA,
            schema_version=999,
            hpx_version="0.0.0",
            generated_at="2026-01-01T00:00:00+00:00",
            host={},
            sections=(),
            redaction={},
            artifacts=(),
        )


def test_support_bundle_manifest_directory_verify_detects_digest_mismatch(tmp_path: Path) -> None:
    artifact_path = tmp_path / "checks.json"
    artifact_path.write_text('{"ok": true}', encoding="utf-8")
    manifest = SupportBundleManifest(
        schema=SUPPORT_BUNDLE_SCHEMA,
        schema_version=SUPPORT_BUNDLE_SCHEMA_VERSION,
        hpx_version="0.0.0",
        generated_at="2026-01-01T00:00:00+00:00",
        host={},
        sections=(),
        redaction={},
        artifacts=(
            ResultArtifact(
                path="checks.json",
                media_type="application/json",
                size_bytes=artifact_path.stat().st_size,
                sha256="0" * 64,
            ),
        ),
    )

    with pytest.raises(ReportError, match="digest mismatch"):
        manifest.verify(tmp_path)


def test_support_bundle_manifest_round_trips_through_dict() -> None:
    manifest = SupportBundleManifest(
        schema=SUPPORT_BUNDLE_SCHEMA,
        schema_version=SUPPORT_BUNDLE_SCHEMA_VERSION,
        hpx_version="0.1.1",
        generated_at="2026-01-01T00:00:00+00:00",
        host={"system": "Darwin"},
        sections=(SupportBundleSection("checks", True),),
        redaction={"total": 0},
        artifacts=(
            ResultArtifact(
                path="checks.json",
                media_type="application/json",
                size_bytes=2,
                sha256="a" * 64,
            ),
        ),
    )

    reloaded = SupportBundleManifest.from_dict(manifest.to_dict())

    assert reloaded == manifest
    assert reloaded.section("checks") == SupportBundleSection("checks", True)
    assert reloaded.section("missing") is None


# ---------------------------------------------------------------------------
# Zip-editing test helpers.
# ---------------------------------------------------------------------------


def _rewrite_zip_member(path: Path, name: str, content: bytes) -> None:
    with zipfile.ZipFile(path) as archive:
        entries = {info.filename: archive.read(info.filename) for info in archive.infolist()}
    entries[name] = content
    _rewrite_zip(path, entries)


def _add_zip_member(path: Path, name: str, content: bytes) -> None:
    with zipfile.ZipFile(path) as archive:
        entries = {info.filename: archive.read(info.filename) for info in archive.infolist()}
    entries[name] = content
    _rewrite_zip(path, entries)


def _remove_zip_member(path: Path, name: str) -> None:
    with zipfile.ZipFile(path) as archive:
        entries = {info.filename: archive.read(info.filename) for info in archive.infolist()}
    del entries[name]
    _rewrite_zip(path, entries)


def _rewrite_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
