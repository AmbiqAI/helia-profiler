"""Tests for the ``hpx doctor --bundle`` field-diagnostics support archive.

Covers: collector partial failure (missing workspace/config/tools), offline
operation, opt-in raw probe identifiers, exact Stage 5 dependency lock
provenance inclusion, deterministic archive naming/manifest/member order,
archive self-verification, and rejection of malformed/hostile archives.
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path
from unittest import mock

import pytest
from neuralspotx.nsx_lock import LockKind, NsxLock, ResolvedModule, hash_manifest, write_lock

from helia_profiler.config import load_config
from helia_profiler.deps.dependencies import create_workspace, prepare_locked_dependencies
from helia_profiler.engines import TFLM_ENGINE_HEADER
from helia_profiler.engines.base import TflmArtifacts
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
from helia_profiler.diagnostics.support_bundle import (
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


def _prepared_workspace(
    tmp_path: Path, *, credentialed_url: bool = False, url_override: str | None = None
) -> Path:
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
    ctx.engine_artifacts = TflmArtifacts(engine_header=TFLM_ENGINE_HEADER)
    ctx.dependency_workspace = create_workspace(ctx)
    ctx.firmware_dir = ctx.dependency_workspace.root / "profiler_app"
    ctx.firmware_dir.mkdir(parents=True, exist_ok=True)
    (ctx.firmware_dir / "nsx.yml").write_text(
        "target:\n  board: apollo510_evb\nmodules:\n  - name: demo\n", encoding="utf-8"
    )
    module_dir = ctx.firmware_dir / "modules" / "demo"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "nsx-module.yaml").write_text("name: demo\n", encoding="utf-8")

    if url_override is not None:
        url = url_override
    else:
        url = (
            "https://" + "user:" + "hunter2" + "@example.invalid/demo.git"
            if credentialed_url
            else "https://example.invalid/demo.git"
        )
    lock = NsxLock(
        generated_at="2026-08-03T00:00:00+00:00",
        nsx_tool_version="0.7.17",
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

    with mock.patch("helia_profiler.deps.dependencies.nsx_cli.sync", lambda *a, **kw: None):
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
    assert dependencies.reason is not None
    assert "no --workspace given" in dependencies.reason
    assert lock_section is not None and not lock_section.available
    assert "dependencies.json" not in collection.members
    assert "nsx.lock" not in collection.members


def test_collect_support_bundle_marks_unprepared_workspace_unavailable(tmp_path: Path) -> None:
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
    assert section.reason is not None
    assert "no --config given" in section.reason


def test_collect_support_bundle_marks_unresolvable_config_unavailable(tmp_path: Path) -> None:
    bad_config = tmp_path / "bad.yml"
    bad_config.write_text("target:\n  board: apollo510_evb\n", encoding="utf-8")
    options = SupportBundleOptions(
        config_path=bad_config, include_probes=False, include_ports=False
    )

    section = _section(options, "config")

    assert not section.available
    assert section.reason is not None
    assert "model.path" in section.reason


def test_collect_support_bundle_non_utf8_config_degrades_section_not_traceback(
    tmp_path: Path,
) -> None:
    """A ``--config`` YAML with a byte that isn't valid UTF-8 must degrade
    the ``config`` section, not raise a raw ``UnicodeDecodeError`` --
    ``load_config()`` opens the file as text with no explicit encoding, so
    nothing guarantees a hand-authored config actually is UTF-8 (or that
    the process locale encoding is UTF-8 at all).
    """
    bad_config = tmp_path / "bad.yml"
    bad_config.write_bytes(b"target:\n  board: apollo510_evb  # not utf-8: \xe9\n")
    options = SupportBundleOptions(
        config_path=bad_config, include_probes=False, include_ports=False
    )

    collection = collect_support_bundle(options)

    section = collection.manifest.section("config")
    assert section is not None and not section.available
    assert section.reason


def test_collect_support_bundle_redacts_section_reason_paths(tmp_path: Path) -> None:
    """A skip *reason* is a formatted exception message, which routinely
    embeds the exact path that caused it -- it must be redacted like every
    other piece of free-form text in the bundle, not written verbatim into
    manifest.json.
    """
    sensitive_workspace = tmp_path / "Users" / "very-secret-account" / "private-workspace"
    options = SupportBundleOptions(
        workspace=sensitive_workspace, include_probes=False, include_ports=False
    )

    collection = collect_support_bundle(options)

    dependencies = collection.manifest.section("dependencies")
    lock_section = collection.manifest.section("nsx.lock")
    assert dependencies is not None and not dependencies.available
    assert lock_section is not None and not lock_section.available
    assert dependencies.reason and "very-secret-account" not in dependencies.reason
    assert lock_section.reason and "very-secret-account" not in lock_section.reason
    # The whole serialized manifest -- not just the section fields directly
    # asserted above -- must never contain the raw sensitive path segment.
    assert "very-secret-account" not in json.dumps(collection.manifest.to_dict())
    assert collection.manifest.redaction["paths"] >= 1


def test_collect_support_bundle_redacts_nested_secret_in_free_form_engine_config(
    tmp_path: Path,
) -> None:
    """``engine.config`` is a free-form, user-supplied dict — a nested
    ``credentials``/``secrets`` block under a secret-shaped key must be
    fully redacted in the archived config.json, not only a bare string
    value directly under that key, and not only when the value is itself a
    string (an unquoted YAML PIN/password parses as an ``int``)."""
    model = tmp_path / "model.tflite"
    model.write_bytes(b"TFL3")
    config_path = tmp_path / "hpx.yml"
    config_path.write_text(
        "model:\n"
        f"  path: {model}\n"
        "target:\n"
        "  board: apollo510_evb\n"
        "engine:\n"
        "  type: tflm\n"
        "  config:\n"
        "    credentials:\n"
        "      user: alice\n"
        "      pass: LEAKVALUE_NESTED\n"
        "    password: 55512345678\n",
        encoding="utf-8",
    )
    options = SupportBundleOptions(
        config_path=config_path, include_probes=False, include_ports=False
    )

    collection = collect_support_bundle(options)
    path = write_support_bundle(collection, tmp_path / "out")

    config_text = collection.members["config.json"].decode("utf-8")
    assert "LEAKVALUE_NESTED" not in config_text
    assert "55512345678" not in config_text
    assert collection.manifest.redaction["env_values"] >= 2

    # Verify against the *decompressed* archived member, not raw zip bytes
    # (DEFLATE-compressed content can't be reliably substring-searched).
    with zipfile.ZipFile(path) as archive:
        archived_config_text = archive.read("config.json").decode("utf-8")
    assert "LEAKVALUE_NESTED" not in archived_config_text
    assert "55512345678" not in archived_config_text


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


def test_collect_support_bundle_redacts_serial_everywhere_it_recurs_in_a_port_record(
    monkeypatch,
) -> None:
    """A serial number recurring in ``hwid``'s ``SER=`` marker and in the
    device path's basename (both realistic pyserial/macOS shapes) must be
    scrubbed everywhere, not only in the field literally named ``serial_
    number`` -- the structural-by-name defense alone is not enough."""
    from helia_profiler.transport.ports import SerialPortInfo

    serial = "000440123456"
    port = SerialPortInfo(
        device=f"/dev/tty.usbmodem{serial}1",
        kind="hpx-usb-cdc",
        description="HPX debug probe",
        manufacturer="Ambiq",
        product="HPX Probe",
        serial_number=serial,
        interface="00",
        hwid=f"USB VID:PID=1366:0105 SER={serial} LOCATION=20-2",
    )
    monkeypatch.setattr(
        "helia_profiler.transport.ports.list_serial_ports", lambda **_kw: (port,)
    )
    options = SupportBundleOptions(include_probes=False, include_ports=True)

    collection = collect_support_bundle(options)

    payload = json.loads(collection.members["ports.json"])
    record = payload[0]
    assert serial not in record["hwid"]
    assert serial not in record["device"]
    assert serial not in json.dumps(record)
    assert record["serial_number"].startswith("<redacted-serial:")
    # The same placeholder everywhere the same real value occurred, so a
    # reviewer can still tell "these three fields refer to one probe".
    assert record["serial_number"] in record["hwid"]
    assert collection.manifest.redaction["serials"] >= 1


def test_collect_support_bundle_ports_raw_probe_ids_opt_in_keeps_serial_everywhere(
    monkeypatch,
) -> None:
    from helia_profiler.transport.ports import SerialPortInfo

    serial = "000440123456"
    port = SerialPortInfo(
        device=f"/dev/tty.usbmodem{serial}1",
        kind="hpx-usb-cdc",
        serial_number=serial,
        hwid=f"USB VID:PID=1366:0105 SER={serial} LOCATION=20-2",
    )
    monkeypatch.setattr(
        "helia_profiler.transport.ports.list_serial_ports", lambda **_kw: (port,)
    )
    options = SupportBundleOptions(include_probes=False, include_ports=True, raw_probe_ids=True)

    collection = collect_support_bundle(options)

    payload = json.loads(collection.members["ports.json"])
    record = payload[0]
    assert record["serial_number"] == serial
    assert serial in record["hwid"]


def test_collect_support_bundle_config_directory_path_degrades_section_not_traceback(
    tmp_path: Path,
) -> None:
    """A ``--config`` path that is a directory (or otherwise unreadable) must
    raise ``IsADirectoryError``/``PermissionError`` -- plain ``OSError``
    subclasses, not ``HpxError`` -- inside ``load_config``'s bare ``open()``.
    The collector must still degrade just this section, never propagate."""
    config_dir = tmp_path / "a-directory-not-a-file"
    config_dir.mkdir()
    options = SupportBundleOptions(
        config_path=config_dir, include_probes=False, include_ports=False
    )

    collection = collect_support_bundle(options)

    section = collection.manifest.section("config")
    assert section is not None
    assert not section.available
    assert section.reason


@pytest.mark.skipif(
    not hasattr(os, "chmod") or os.name == "nt",
    reason="POSIX permission bits only",
)
def test_collect_support_bundle_unreadable_lock_degrades_section_not_traceback(
    tmp_path: Path,
) -> None:
    """An ``nsx.lock`` that becomes unreadable (permission denied) between
    workspace preparation and bundle collection must degrade the
    dependencies/nsx.lock sections, not raise a raw ``PermissionError``."""
    if os.geteuid() == 0:
        pytest.skip("root ignores POSIX permission bits")
    app_dir = _prepared_workspace(tmp_path)
    lock_path = app_dir / "nsx.lock"
    original_mode = lock_path.stat().st_mode
    lock_path.chmod(0o000)
    try:
        options = SupportBundleOptions(
            workspace=app_dir, include_probes=False, include_ports=False
        )
        collection = collect_support_bundle(options)
    finally:
        lock_path.chmod(original_mode)

    section = collection.manifest.section("dependencies")
    assert section is not None
    assert not section.available
    assert section.reason


def test_collect_support_bundle_non_utf8_lock_degrades_nsx_lock_section_not_traceback(
    tmp_path: Path,
) -> None:
    """An ``nsx.lock`` with bytes that aren't valid UTF-8 must degrade just
    the ``nsx.lock`` section (``dependencies.json`` provenance has already
    been recorded from the matching digest and stays available), not raise
    a raw ``UnicodeDecodeError`` -- ``read_text(encoding="utf-8")`` doesn't
    know or care that this collector only ever wrote valid-UTF-8 lock files
    itself; nothing guarantees an on-disk ``nsx.lock`` from an older/other
    tool actually is one.
    """
    app_dir = _prepared_workspace(tmp_path)
    lock_path = app_dir / "nsx.lock"
    state_path = app_dir / "hpx-dependencies.json"

    corrupted = lock_path.read_bytes() + b"\n# \xff\xfe not valid utf-8\n"
    lock_path.write_bytes(corrupted)
    new_sha256 = hashlib.sha256(corrupted).hexdigest()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["lock"]["sha256"]["value"] = new_sha256
    state_path.write_text(json.dumps(state), encoding="utf-8")

    options = SupportBundleOptions(workspace=app_dir, include_probes=False, include_ports=False)
    collection = collect_support_bundle(options)

    dependencies = collection.manifest.section("dependencies")
    lock_section = collection.manifest.section("nsx.lock")
    assert dependencies is not None and dependencies.available
    assert lock_section is not None and not lock_section.available
    assert lock_section.reason and "codec" in lock_section.reason.lower()
    assert "nsx.lock" not in collection.members


def test_collect_support_bundle_non_utf8_dependency_state_degrades_section_not_traceback(
    tmp_path: Path,
) -> None:
    """A corrupted/truncated ``hpx-dependencies.json`` provenance state file
    that isn't valid UTF-8 must degrade the ``dependencies``/``nsx.lock``
    sections, not raise a raw ``UnicodeDecodeError`` --
    ``read_dependency_lock_provenance()`` reads that state file as UTF-8
    text internally, and this collector's job is to catch every one of its
    typed and untyped failure modes, matching the ``nsx.lock`` text-read
    case just above.
    """
    app_dir = _prepared_workspace(tmp_path)
    state_path = app_dir / "hpx-dependencies.json"

    original = state_path.read_bytes()
    state_path.write_bytes(original[:-1] + b"\xff" + original[-1:])

    options = SupportBundleOptions(workspace=app_dir, include_probes=False, include_ports=False)
    collection = collect_support_bundle(options)

    dependencies = collection.manifest.section("dependencies")
    lock_section = collection.manifest.section("nsx.lock")
    assert dependencies is not None and not dependencies.available
    assert lock_section is not None and not lock_section.available
    assert dependencies.reason and "codec" in dependencies.reason.lower()
    assert "dependencies.json" not in collection.members
    assert "nsx.lock" not in collection.members


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
    assert compatibility_payload["baseline_id"] == "hpx-neuralspotx-0.7.17-2026-09"
    assert compatibility_payload["neuralspotx"]["version"] == "0.7.17"
    assert (
        compatibility_payload["neuralspotx"]["sha256"]
        == "1289cd67eb27475159a4f9083338ee81648fcc115783db4f467ec96c9ca0fbdb"
    )
    assert (
        compatibility_payload["projects"]["neuralspotx"]["ref"]
        == "8b5a7fa99f044cfd4ba3c0668fb2419eceabb44f"
    )
    assert (
        compatibility_payload["projects"]["nsx-ambiq-sdk"]["ref"]
        == "a9f4ec25a162f6f3700623feb691423bb5a51132"
    )


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

    # nsx.lock text is embedded (redacted, when it contains anything
    # secret-shaped), not just a dependencies.json summary. This fixture's
    # lock has nothing redaction-worthy in it, so the embedded bytes equal
    # the on-disk file exactly here -- see
    # test_collect_support_bundle_redacts_credentials_in_embedded_lock for
    # the case where they differ.
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


def test_collect_support_bundle_redacts_single_token_userinfo_in_embedded_lock(
    tmp_path: Path,
) -> None:
    """A single-token (no-colon) credential in a git remote URL -- the most
    common real-world shape (`https://<PAT>@host/...`) -- must not survive
    into the archived nsx.lock, and the manifest's counters must say so."""
    token = "ghp_" + "A" * 36
    app_dir = _prepared_workspace(
        tmp_path, url_override=f"https://{token}@example.invalid/demo.git"
    )
    options = SupportBundleOptions(workspace=app_dir, include_probes=False, include_ports=False)

    collection = collect_support_bundle(options)
    path = write_support_bundle(collection, tmp_path / "out")

    lock_text = collection.members["nsx.lock"].decode("utf-8")
    assert token not in lock_text
    assert "<redacted>@example.invalid" in lock_text
    assert collection.manifest.redaction["urls"] >= 1
    assert collection.manifest.redaction["total"] >= 1

    # And the same guarantee holds for the actual bytes written to disk, not
    # just the in-memory collection.
    with zipfile.ZipFile(path) as archive:
        archived_lock = archive.read("nsx.lock").decode("utf-8")
    assert token not in archived_lock
    with open(path, "rb") as handle:
        assert token.encode("utf-8") not in handle.read()


def test_collect_support_bundle_redacts_token_shape_embedded_in_lock_url_path(
    tmp_path: Path,
) -> None:
    """A credential/token shape can appear in a URL's path or query, not
    only its userinfo component (e.g. a signed download link) -- that must
    be caught too, both in-memory and in the written archive bytes."""
    token = "ghp_" + "B" * 36
    app_dir = _prepared_workspace(
        tmp_path, url_override=f"https://example.invalid/download/{token}/demo.git"
    )
    options = SupportBundleOptions(workspace=app_dir, include_probes=False, include_ports=False)

    collection = collect_support_bundle(options)
    path = write_support_bundle(collection, tmp_path / "out")

    lock_text = collection.members["nsx.lock"].decode("utf-8")
    assert token not in lock_text
    assert collection.manifest.redaction["tokens"] >= 1
    with zipfile.ZipFile(path) as archive:
        archived_lock = archive.read("nsx.lock").decode("utf-8")
    assert token not in archived_lock
    with open(path, "rb") as handle:
        assert token.encode("utf-8") not in handle.read()


def test_collect_support_bundle_redacts_oauth_fragment_credential_in_lock_url(
    tmp_path: Path,
) -> None:
    """An OAuth implicit-grant-style `#access_token=...` fragment credential
    embedded in a git remote URL must not survive into the archive, and the
    manifest's counters must say so (not read a false-clean 0)."""
    secret = "SUPERSECRETOAUTHVALUE"
    app_dir = _prepared_workspace(
        tmp_path,
        url_override=f"https://example.invalid/demo.git#access_token={secret}",
    )
    options = SupportBundleOptions(workspace=app_dir, include_probes=False, include_ports=False)

    collection = collect_support_bundle(options)
    path = write_support_bundle(collection, tmp_path / "out")

    lock_text = collection.members["nsx.lock"].decode("utf-8")
    deps_text = collection.members["dependencies.json"].decode("utf-8")
    assert secret not in lock_text
    assert secret not in deps_text
    assert collection.manifest.redaction["urls"] >= 1
    with zipfile.ZipFile(path) as archive:
        assert secret not in archive.read("nsx.lock").decode("utf-8")
        assert secret not in archive.read("dependencies.json").decode("utf-8")
    with open(path, "rb") as handle:
        assert secret.encode("utf-8") not in handle.read()


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
            # ZIP_STORED (not ZIP_DEFLATED): DEFLATE's exact output bytes
            # depend on the zlib version/build doing the compressing, so a
            # "byte-identical across hosts" guarantee can only actually
            # hold for uncompressed members.
            assert info.compress_type == zipfile.ZIP_STORED


def test_write_support_bundle_raises_report_error_on_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A destination write failure (permission denied, disk full, ...) must
    surface as a typed ReportError, not a raw OSError -- so a CLI caller
    that only catches HpxError still gets a clean message instead of a
    traceback.
    """
    from helia_profiler.diagnostics import support_bundle as support_bundle_module

    options = SupportBundleOptions(include_probes=False, include_ports=False)
    collection = collect_support_bundle(options)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr(support_bundle_module, "_write_deterministic_zip", _boom)

    with pytest.raises(ReportError, match="Cannot write support bundle") as excinfo:
        write_support_bundle(collection, tmp_path)
    assert not isinstance(excinfo.value, OSError)
    assert "No space left on device" in str(excinfo.value)


def test_write_support_bundle_raises_report_error_when_output_dir_is_a_file(
    tmp_path: Path,
) -> None:
    """A directory-mode ``--bundle`` destination that collides with an
    existing plain file is a real, easy-to-hit OSError (``mkdir`` on a path
    component that already exists as a file) -- confirm it is wrapped too,
    without mocking anything.
    """
    options = SupportBundleOptions(include_probes=False, include_ports=False)
    collection = collect_support_bundle(options)

    blocking_file = tmp_path / "not-a-directory"
    blocking_file.write_text("occupied", encoding="utf-8")

    with pytest.raises(ReportError, match="Cannot write support bundle"):
        write_support_bundle(collection, blocking_file / "bundle-dir")


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


def test_verify_support_bundle_rejects_corrupt_manifest_json_as_report_error(
    tmp_path: Path,
) -> None:
    """A truncated/corrupted manifest.json must raise ReportError.

    Not a raw json.JSONDecodeError -- verify_support_bundle()'s contract is
    that every failure is a typed ReportError, matching every other
    structural check in this function (missing manifest, undeclared
    members, tampered digests, ...).
    """
    path = tmp_path / "corrupt-manifest.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", "{not valid json")

    with pytest.raises(ReportError, match="not valid JSON") as excinfo:
        verify_support_bundle(path)
    assert not isinstance(excinfo.value, json.JSONDecodeError)


def test_verify_support_bundle_rejects_non_utf8_manifest_json_as_report_error(
    tmp_path: Path,
) -> None:
    """A manifest.json that isn't valid UTF-8 must also raise ReportError.

    ``json.loads`` on bytes decodes as UTF-8 internally and raises
    ``UnicodeDecodeError`` (a ``ValueError``, not caught by a bare
    ``except json.JSONDecodeError``) for invalid byte sequences. This uses
    a payload with no BOM: a leading ``\\xff\\xfe`` is itself a valid
    UTF-16-LE byte-order mark, which ``json.detect_encoding()`` would pick
    up and decode successfully (as garbage), raising ``JSONDecodeError``
    instead and defeating the point of this test.
    """
    path = tmp_path / "non-utf8-manifest.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", b'{"schema": "\xff"}')

    with pytest.raises(ReportError, match="not valid JSON") as excinfo:
        verify_support_bundle(path)
    assert not isinstance(excinfo.value, UnicodeDecodeError)
    assert isinstance(excinfo.value.__cause__, UnicodeDecodeError)


def test_verify_support_bundle_still_raises_manifest_from_dict_report_error(
    tmp_path: Path,
) -> None:
    """A structurally-valid-JSON but schema-invalid manifest.json still
    surfaces SupportBundleManifest.from_dict()'s own ReportError message,
    not the generic "not valid JSON" one -- the two failure modes must
    stay distinguishable.
    """
    path = tmp_path / "wrong-schema-manifest.zip"
    bad_manifest = json.loads(_minimal_manifest_json())
    bad_manifest["schema"] = "nope"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(bad_manifest))

    with pytest.raises(ReportError, match="Unsupported support bundle schema"):
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
    ("hostile_name", "expected_message"),
    [
        ("../../etc/passwd", "unsafe path"),
        ("/etc/passwd", "unsafe path"),
        ("a/../../b.json", "unsafe path"),
        ("a\\..\\b.json", "unsafe path"),
        ("model.tflite", "disallowed type"),
        ("profiler_app/main.elf", "disallowed type"),
        ("firmware.bin", "disallowed type"),
        # Windows drive-absolute paths are absolute on Windows even though
        # they contain no leading "/" -- PurePosixPath doesn't recognize a
        # drive letter as a root, so these must be caught by a dedicated
        # check rather than falling through as "relative".
        ("C:/Windows/System32/x.json", "must be relative"),
        ("c:/x.json", "must be relative"),
        # zipfile.ZipInfo normalizes a backslash to "/" only when the
        # writing host's os.sep is "\\" (Windows) -- so this same literal
        # Python string ends up stored as "C:\\Windows\\..." on POSIX CI
        # runners (caught by the plain "\\" in name check) but as
        # "C:/Windows/..." on Windows CI runners (caught by
        # _WINDOWS_DRIVE_ABS_RE instead). Both are safely rejected; only
        # the specific message differs by host, so accept either.
        ("C:\\Windows\\System32\\x.json", "unsafe path|must be relative"),
    ],
)
def test_verify_support_bundle_rejects_hostile_member_paths(
    tmp_path: Path, hostile_name: str, expected_message: str
) -> None:
    path = tmp_path / "hostile.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", _minimal_manifest_json())
        archive.writestr(hostile_name, "evil")

    # Pin the *specific* rejection reason, not just "some ReportError" --
    # a path-traversal/absolute-path member must be caught as an unsafe
    # path, and a disguised model/firmware payload must be caught as a
    # disallowed member type, even if the other check were ever loosened.
    with pytest.raises(ReportError, match=expected_message):
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
