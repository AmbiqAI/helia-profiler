"""Prepared runtime archives built from the runtime records."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from helia_profiler import prepared_runtimes
from helia_profiler.cli.app import app
from helia_profiler.errors import ConfigError
from helia_profiler.fixture import (
    EngineType,
    FixtureBackend,
    FixtureBuildRequest,
    FixtureFile,
    FixtureIO,
    FixtureMethod,
    FixtureTensor,
    FixtureTimingScope,
    PerTensorQuantization,
    TypedFixture,
)
from helia_profiler.prepared_runtimes import prepare_runtime, prepared_directory, prepared_runtime
from helia_profiler.runtime_records import runtime

_RECORD = runtime("helia-rt")
assert _RECORD is not None
RECORD = _RECORD
ARCHIVE = b"!<arch>\nhelia-rt-member"


def _dist(root: Path, *, commit: str = "dc8533a") -> Path:
    (root / "lib").mkdir(parents=True)
    (root / "lib" / "libhelia-rt-cm55-atfe-release-with-logs.a").write_bytes(ARCHIVE)
    (root / "lib" / "libhelia-rt-cm55-atfe-release.a").write_bytes(b"!<arch>\nother")
    (root / "MANIFEST.txt").write_text(f"helia-rt helia-rt-v1.21.3\nCommit: {commit}\n")
    for include in prepared_runtimes._HELIA_RT_INCLUDE_DIRS[1:]:
        (root / include).mkdir(parents=True, exist_ok=True)
        (root / include / "api.h").write_text(f"// {include}\n")
    (root / "tensorflow/lite").mkdir(parents=True)
    (root / "tensorflow/lite/micro.h").write_text("// micro\n")
    (root / "signal").mkdir()
    (root / "signal/fft.h").write_text("// fft\n")
    (root / "tensorflow/lite/notes.txt").write_text("not a header\n")
    return root


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path / "cache"))
    dist = _dist(tmp_path / "dist")
    monkeypatch.setattr(
        "helia_profiler.engines.helia_rt.download._fetch_github_release",
        lambda repo, ref, **_: (dist, "1.21.3") if ref == "helia-rt-v1.21.3" else pytest.fail(ref),
    )
    return tmp_path / "cache"


def test_prepare_builds_a_verified_archive_from_the_record(cache: Path) -> None:
    assert RECORD.kernels is not None
    prepared = prepare_runtime("helia-rt")
    assert prepared.directory == cache / "runtimes" / "helia-rt" / "1.21.3"
    verified = prepared.runtime.verify()
    assert prepared.runtime.archive.read() == ARCHIVE
    providers = {p.name: p.revision for p in verified.record.providers}
    assert providers == {"helia-rt": RECORD.source.commit, "ns-cmsis-nn": RECORD.kernels.commit}
    headers = {h.name for h in verified.record.headers}
    assert "tensorflow/lite/micro.h" in headers and "signal/fft.h" in headers
    assert all(name.endswith(".h") for name in headers)
    assert verified.record.consumer_defines == prepared_runtimes._HELIA_RT_DEFINES
    assert not (prepared.directory.parent / "1.21.3.partial").exists()


def test_a_differing_archive_is_reported_not_refused(cache: Path, monkeypatch) -> None:
    assert not prepare_runtime("helia-rt").matches_record
    sha256 = hashlib.sha256(ARCHIVE).hexdigest()
    monkeypatch.setattr(
        prepared_runtimes, "runtime", lambda *_: replace(RECORD, archive_sha256=sha256)
    )
    assert prepare_runtime("helia-rt").matches_record


def test_prepare_refuses_a_release_built_from_another_commit(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path / "cache"))
    dist = _dist(tmp_path / "dist", commit="0123456")
    monkeypatch.setattr(
        "helia_profiler.engines.helia_rt.download._fetch_github_release",
        lambda *_a, **_k: (dist, "1.21.3"),
    )
    with pytest.raises(ConfigError, match="not built from dc8533ab"):
        prepare_runtime("helia-rt")
    assert not (tmp_path / "cache" / "runtimes").exists()


@pytest.mark.parametrize(
    ("name", "version", "message"),
    [
        ("tflm", None, "helia-rt only, not tflm"),
        ("helia-aot", None, "helia-rt only, not helia-aot"),
        ("helia-rt", "9.9.9", "No runtime record for helia-rt 9.9.9"),
    ],
)
def test_prepare_refuses_runtimes_without_an_archive(name, version, message) -> None:
    with pytest.raises(ConfigError, match=message):
        prepare_runtime(name, version)


def test_a_missing_prepared_runtime_names_the_prepare_command(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path))
    with pytest.raises(ConfigError, match="No prepared helia-rt 1.21.3 runtime") as exc:
        prepared_runtime("helia-rt")
    assert "hpx runtimes prepare helia-rt 1.21.3" in (exc.value.hint or "")


def _pin(path: Path, data: bytes) -> FixtureFile:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return FixtureFile(path, hashlib.sha256(data).hexdigest())


def _request(root: Path, engine: EngineType) -> FixtureBuildRequest:
    tensor = FixtureTensor("x", 0, "int8", (1, 4), PerTensorQuantization(0.5, 0))
    fixture = TypedFixture(
        _pin(root / "model.tflite", b"model"),
        (FixtureIO(tensor, _pin(root / "in.bin", bytes(4))),),
        (FixtureIO(tensor, _pin(root / "out.bin", bytes(4))),),
    )
    backend = FixtureBackend.HELIA if engine is EngineType.HELIA_RT else FixtureBackend.CMSIS_NN
    return FixtureBuildRequest(
        fixture=fixture,
        method=FixtureMethod(FixtureTimingScope.INVOKE_ONLY),
        work_dir=root / "work",
        engine=engine,
        arena_size=65536,
        iterations=3,
        warmup=1,
        backend=backend,
    )


def test_a_helia_rt_request_uses_the_prepared_runtime(cache: Path, tmp_path: Path) -> None:
    prepared = prepare_runtime("helia-rt")
    request = _request(tmp_path, EngineType.HELIA_RT)
    assert request.runtime == prepared.runtime
    manifest = json.loads(prepared.runtime.manifest.read())
    assert manifest["archive_sha256"] == hashlib.sha256(ARCHIVE).hexdigest()


def test_a_helia_rt_request_without_a_prepared_runtime_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path / "cache"))
    with pytest.raises(ConfigError, match="No prepared helia-rt"):
        _request(tmp_path, EngineType.HELIA_RT)


def test_a_tflm_request_still_takes_an_explicit_runtime(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path / "cache"))
    assert _request(tmp_path, EngineType.TFLM).runtime is None


def test_runtimes_prepare_cli_reports_the_archive(cache: Path) -> None:
    result = CliRunner().invoke(app, ["runtimes", "prepare", "helia-rt"])
    assert result.exit_code == 0, result.output
    assert f"Prepared helia-rt 1.21.3 in {prepared_directory(RECORD)}" in result.output
    assert "differs from the record's 7df6c2f7" in result.output

    refused = CliRunner().invoke(app, ["runtimes", "prepare", "tflm"])
    assert refused.exit_code == 1
    assert "helia-rt only, not tflm" in refused.output


def _fetch(monkeypatch, dist: Path) -> None:
    monkeypatch.setattr(
        "helia_profiler.engines.helia_rt.download._fetch_github_release",
        lambda *_a, **_k: (dist, "1.21.3"),
    )


def test_a_failed_prepare_leaves_nothing_behind_and_no_traceback(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    monkeypatch.setenv("HPX_CACHE_DIR", str(cache))
    dist = _dist(tmp_path / "dist")
    (dist / "lib" / "libhelia-rt-cm55-atfe-release-with-logs.a").unlink()
    _fetch(monkeypatch, dist)
    result = CliRunner().invoke(app, ["runtimes", "prepare", "helia-rt"])
    assert result.exit_code == 1
    assert "Cannot prepare helia-rt 1.21.3" in result.output and "Traceback" not in result.output
    assert list((cache / "runtimes" / "helia-rt").iterdir()) == []


def test_a_release_without_a_header_directory_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path / "cache"))
    dist = _dist(tmp_path / "dist")
    shutil.rmtree(dist / "signal")
    _fetch(monkeypatch, dist)
    with pytest.raises(ConfigError, match="release has no signal/"):
        prepare_runtime("helia-rt")


def test_re_preparing_replaces_the_install_without_leftovers(cache: Path) -> None:
    first = prepare_runtime("helia-rt")
    (first.directory / "stale.txt").write_text("from the first install")
    second = prepare_runtime("helia-rt")
    assert second.directory == first.directory and not (second.directory / "stale.txt").exists()
    assert sorted(p.name for p in second.directory.parent.iterdir()) == ["1.21.3"]
    assert second.runtime.manifest.sha256 == first.runtime.manifest.sha256


def test_a_malformed_prepared_manifest_names_the_prepare_command(cache: Path) -> None:
    prepared = prepare_runtime("helia-rt")
    (prepared.directory / "provider-manifest.json").write_text("{}")
    with pytest.raises(ConfigError, match="unreadable") as exc:
        prepared_runtime("helia-rt")
    assert "hpx runtimes prepare helia-rt 1.21.3" in (exc.value.hint or "")


def test_an_explicit_runtime_is_kept_and_identity_covers_the_runtime(cache, tmp_path) -> None:
    prepared = prepare_runtime("helia-rt")
    resolved = _request(tmp_path / "a", EngineType.HELIA_RT)
    explicit = replace(resolved, runtime=prepared.runtime)
    assert explicit.runtime is prepared.runtime
    assert explicit.intent_identity == resolved.intent_identity
    (prepared.directory / "provider-manifest.json").write_text(
        (prepared.directory / "provider-manifest.json").read_text() + " "
    )
    changed = _request(tmp_path / "b", EngineType.HELIA_RT)
    assert changed.intent_identity != resolved.intent_identity


def test_a_failed_swap_restores_the_previous_install(cache: Path, monkeypatch) -> None:
    first = prepare_runtime("helia-rt")
    marker = first.directory / "marker.txt"
    marker.write_text("previous install")
    real_rename = Path.rename

    def failing_rename(self: Path, target):
        if self.name.startswith(".1.21.3-") and "-old-" not in self.name:
            raise OSError("injected rename failure")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", failing_rename)
    with pytest.raises(ConfigError, match="injected rename failure"):
        prepare_runtime("helia-rt")
    monkeypatch.setattr(Path, "rename", real_rename)
    assert marker.read_text() == "previous install"
    assert sorted(p.name for p in first.directory.parent.iterdir()) == ["1.21.3"]
    assert oct(first.directory.stat().st_mode & 0o777) in ("0o755", "0o775")


def test_a_damaged_prepared_install_names_the_prepare_command(cache: Path) -> None:
    prepared = prepare_runtime("helia-rt")
    (prepared.directory / "include" / "signal" / "fft.h").unlink()
    with pytest.raises(ConfigError, match="is damaged") as exc:
        prepared_runtime("helia-rt")
    assert "hpx runtimes prepare helia-rt 1.21.3" in (exc.value.hint or "")


def test_a_helia_rt_record_without_an_archive_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(
        prepared_runtimes, "runtime", lambda *_: replace(RECORD, archive_sha256=None, kernels=None)
    )
    with pytest.raises(ConfigError, match="record pins no prepared archive"):
        prepare_runtime("helia-rt")


def test_a_prepare_removes_leftovers_of_an_interrupted_replace(cache: Path) -> None:
    first = prepare_runtime("helia-rt")
    leftover = first.directory.parent / ".1.21.3-old-abcd1234" / "install"
    leftover.mkdir(parents=True)
    prepare_runtime("helia-rt")
    assert sorted(p.name for p in first.directory.parent.iterdir()) == ["1.21.3"]
