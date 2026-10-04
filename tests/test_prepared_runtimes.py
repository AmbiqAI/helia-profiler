"""Prepared runtime archives built from the runtime records."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import time
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
        ("helia-aot", None, "no archive for helia-aot"),
        ("executorch", None, "no archive for executorch"),
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


def test_runtimes_prepare_cli_reports_the_archive(cache: Path) -> None:
    result = CliRunner().invoke(app, ["runtimes", "prepare", "helia-rt"])
    assert result.exit_code == 0, result.output
    assert f"Prepared helia-rt 1.21.3 in {prepared_directory(RECORD)}" in result.output
    assert "differs from the record's 7df6c2f7" in result.output

    refused = CliRunner().invoke(app, ["runtimes", "prepare", "helia-aot"])
    assert refused.exit_code == 1
    assert "no archive for helia-aot" in refused.output


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
    if os.name == "posix":  # Windows reports no POSIX permission bits
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


_TFLM = runtime("tflm")
assert _TFLM is not None and _TFLM.kernels is not None
TFLM, TFLM_KERNELS = _TFLM, _TFLM.kernels
# A make dry run: download notes and a non-compile line shlex cannot split, which must be
# skipped unparsed, then one C++ and one C unit.
TFLM_LISTING = """\
tensorflow/lite/micro/tools/make/downloads/cmsis_nn already exists, skipping the download.
echo "ends with a backslash \\
mkdir -p gen/obj/core/tensorflow/lite/micro/
clang++ -fno-rtti -Oz -ffp-mode=full --target=arm-arm-none-eabi -mcpu=cortex-m55 -I. \
-c tensorflow/lite/micro/micro_log.cc -o gen/obj/core/micro_log.o
clang -O2 -std=c17 --target=arm-arm-none-eabi -mcpu=cortex-m55 \
-c tensorflow/lite/micro/tools/make/downloads/cmsis_nn/Source/arm_nn.c -o gen/obj/arm_nn.o
"""


def _tflm_tree(tree: Path, *, pin: str = TFLM_KERNELS.commit) -> Path:
    for include in prepared_runtimes._TFLM_INCLUDE_DIRS:
        (tree / include).mkdir(parents=True, exist_ok=True)
        (tree / include / "api.h").write_text(f"// {include}\n")
    (tree / "signal/src").mkdir(parents=True)
    (tree / "signal/src/fft.h").write_text("// fft\n")
    (tree / "tensorflow/lite/micro/micro_log.cc").write_text("// a source, not a header\n")
    script = tree / prepared_runtimes._TFLM_CMSIS_NN_SCRIPT
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(f'ZIP_PREFIX_NN="{pin}"\n')
    return tree


@pytest.fixture
def tflm_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Prepare tflm with the download and every build step faked; returns the build argvs."""
    monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ATFE_ROOT", str(tmp_path / "atfe"))
    (tmp_path / "atfe" / "bin").mkdir(parents=True)
    (tmp_path / "atfe" / "bin" / "clang").write_text("")
    monkeypatch.setattr(prepared_runtimes.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(
        prepared_runtimes, "_download_tree", lambda source, tree, timeout_s: _tflm_tree(tree)
    )
    calls: list[list[str]] = []

    def run(argv: list[str], cwd: Path, timeout_s: float) -> str:
        calls.append(argv)
        if argv[0] == "make":
            return TFLM_LISTING
        if "-c" in argv:
            source = argv[argv.index("-c") + 1]
            Path(argv[argv.index("-o") + 1]).write_bytes(f"object of {source};".encode())
            return ""
        members = b"".join(Path(member).read_bytes() for member in argv[3:])
        Path(argv[2]).write_bytes(b"!<arch>\n" + members)
        return ""

    monkeypatch.setattr(prepared_runtimes, "_run", run)
    return calls


def test_prepare_tflm_compiles_tflm_s_source_list_with_the_recorded_flags(
    tflm_build: list[list[str]], tmp_path: Path
) -> None:
    prepared = prepare_runtime("tflm")
    assert prepared.directory == tmp_path / "cache" / "runtimes" / "tflm" / "85638f0"
    make, *compiles, archive = tflm_build
    selection = prepared_runtimes._TFLM_MAKE_SELECTION
    assert make == ["make", "-n", "-f", prepared_runtimes._TFLM_MAKEFILE, *selection, "microlite"]
    atfe = tmp_path / "atfe" / "bin"
    assert [argv[0] for argv in compiles] == [str(atfe / "clang++"), str(atfe / "clang")]
    for argv in compiles:
        added = len(prepared_runtimes._TFLM_ADDED_FLAGS)
        assert tuple(argv[1 : 1 + added]) == prepared_runtimes._TFLM_ADDED_FLAGS
        assert argv[1 + added].startswith("-ffile-prefix-map=") and argv[1 + added].endswith("=.")
        assert [a for a in argv if a.startswith(("-O", "-ffp-mode="))] == ["-O3"]
        assert "--target=arm-none-eabi" in argv and "--target=arm-arm-none-eabi" not in argv
    assert [argv[argv.index("-c") + 1] for argv in compiles] == [
        "tensorflow/lite/micro/micro_log.cc",
        "tensorflow/lite/micro/tools/make/downloads/cmsis_nn/Source/arm_nn.c",
    ]
    assert archive[:2] == [str(atfe / "llvm-ar"), "rcsD"]
    assert [Path(member).name for member in archive[3:]] == ["000-micro_log.o", "001-arm_nn.o"]
    assert prepared.runtime.archive.read() == (
        b"!<arch>\nobject of tensorflow/lite/micro/micro_log.cc;"
        b"object of tensorflow/lite/micro/tools/make/downloads/cmsis_nn/Source/arm_nn.c;"
    )
    verified = prepared.runtime.verify()
    assert (verified.record.schema_version, verified.record.stack) == (1, "upstream")
    providers = {p.name: p.revision for p in verified.record.providers}
    assert providers == {"tflite-micro": TFLM.source.commit, "cmsis-nn": TFLM_KERNELS.commit}
    assert verified.record.include_dirs == prepared_runtimes._TFLM_INCLUDE_DIRS
    headers = {h.name for h in verified.record.headers}
    assert "signal/src/fft.h" in headers and "api.h" not in headers
    assert "tensorflow/lite/micro/tools/make/downloads/cmsis_nn/Include/api.h" in headers
    assert all(name.endswith(".h") for name in headers)


def test_a_tflm_tree_pinning_other_kernels_is_refused(tflm_build, tmp_path, monkeypatch) -> None:
    other = "0123456789abcdef0123456789abcdef01234567"
    monkeypatch.setattr(
        prepared_runtimes, "_download_tree", lambda s, tree, t: _tflm_tree(tree, pin=other)
    )
    with pytest.raises(ConfigError, match=f"pins CMSIS-NN {other}, not the record's 4ab83cc3"):
        prepare_runtime("tflm")
    assert tflm_build == []
    assert not (tmp_path / "cache" / "runtimes").exists()


def test_a_relative_atfe_root_names_absolute_tools(tflm_build, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ATFE_ROOT", "atfe")  # the fixture's tmp_path/atfe
    prepare_runtime("tflm")
    assert tflm_build[1][0] == str(tmp_path / "atfe" / "bin" / "clang++")
    assert tflm_build[-1][0] == str(tmp_path / "atfe" / "bin" / "llvm-ar")


@pytest.mark.parametrize("line", ["clang++ -c", "clang++ -c x.cc", 'clang "unterminated -c x.cc'])
def test_an_unparseable_compile_line_is_refused_cleanly(tflm_build, monkeypatch, line) -> None:
    monkeypatch.setattr(prepared_runtimes, "_run", lambda argv, cwd, timeout_s: line + "\n")
    result = CliRunner().invoke(app, ["runtimes", "prepare", "tflm"])
    assert result.exit_code == 1
    assert "Cannot read TFLM's make dry-run line" in result.output
    assert "".join(line.split()) in "".join(result.output.split())
    assert "Traceback" not in result.output


def test_preparing_tflm_needs_atfe_root(tflm_build, monkeypatch) -> None:
    monkeypatch.delenv("ATFE_ROOT")
    with pytest.raises(ConfigError, match="needs ATFE_ROOT") as exc:
        prepare_runtime("tflm")
    assert "ATFE_ROOT" in (exc.value.hint or "")
    assert tflm_build == []


def test_preparing_tflm_names_missing_host_tools(tflm_build, monkeypatch) -> None:
    monkeypatch.setattr(prepared_runtimes.shutil, "which", lambda tool: None)
    with pytest.raises(ConfigError, match="needs make and bash on PATH"):
        prepare_runtime("tflm")


def test_an_empty_tflm_source_list_is_refused(tflm_build, monkeypatch) -> None:
    monkeypatch.setattr(prepared_runtimes, "_run", lambda argv, cwd, timeout_s: "make: Nothing\n")
    with pytest.raises(ConfigError, match="listed no translation units"):
        prepare_runtime("tflm")


def test_a_failed_tflm_compile_leaves_nothing_behind_and_no_traceback(
    tflm_build, tmp_path, monkeypatch
) -> None:
    def run(argv: list[str], cwd: Path, timeout_s: float) -> str:
        if argv[0] == "make":
            return TFLM_LISTING
        raise ConfigError("Preparing tflm failed at tensorflow/lite/micro/micro_log.cc (exit 1)")

    monkeypatch.setattr(prepared_runtimes, "_run", run)
    result = CliRunner().invoke(app, ["runtimes", "prepare", "tflm"])
    assert result.exit_code == 1
    assert "failed at tensorflow/lite/micro/micro_log.cc" in result.output
    assert "Traceback" not in result.output
    assert not (tmp_path / "cache" / "runtimes").exists()


def test_a_failed_build_step_names_the_source_exit_and_stderr(tmp_path: Path) -> None:
    script = tmp_path / "fail.py"
    script.write_text("import sys\nsys.stderr.write('first\\nerror: boom\\n')\nsys.exit(3)\n")
    argv = [sys.executable, str(script), "-c", "tensorflow/lite/micro/x.cc", "-o", "x.o"]
    with pytest.raises(ConfigError, match=r"at tensorflow/lite/micro/x\.cc \(exit 3\)") as exc:
        prepared_runtimes._run(argv, tmp_path, 60)
    assert str(exc.value).endswith("first\nerror: boom")


def test_build_steps_see_only_the_allowed_environment(tmp_path, monkeypatch) -> None:
    for name, value in {"ARM_NN_ENABLE_F16": "1", "CPATH": "/x", "MAKEFLAGS": "-j9"}.items():
        monkeypatch.setenv(name, value)
    script = tmp_path / "env.py"
    script.write_text("import json, os\nprint(json.dumps(sorted(os.environ)))\n")
    seen = json.loads(prepared_runtimes._run([sys.executable, str(script)], tmp_path, 60))
    assert "PATH" in seen
    assert set(seen) <= set(prepared_runtimes._TFLM_ENV) | {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"}


def test_build_steps_keep_the_download_proxy_and_ca_settings(tmp_path, monkeypatch) -> None:
    for name in ("SSL_CERT_FILE", "CURL_CA_BUNDLE", "https_proxy"):
        monkeypatch.setenv(name, "/etc/site")
    script = tmp_path / "env.py"
    script.write_text("import json, os\nprint(json.dumps(sorted(os.environ)))\n")
    seen = json.loads(prepared_runtimes._run([sys.executable, str(script)], tmp_path, 60))
    assert {"SSL_CERT_FILE", "CURL_CA_BUNDLE", "https_proxy"} <= set(seen)


def test_undecodable_build_output_still_reports_the_failure(tmp_path: Path) -> None:
    script = tmp_path / "bytes.py"
    script.write_text("import sys\nsys.stderr.buffer.write(b'bad \\xff byte')\nsys.exit(2)\n")
    with pytest.raises(ConfigError, match="exit 2"):
        prepared_runtimes._run([sys.executable, str(script)], tmp_path, 60)


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
def test_a_timed_out_build_step_stops_its_children(tmp_path: Path) -> None:
    script = tmp_path / "spawn.py"
    pid_file = tmp_path / "child.pid"
    script.write_text(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen(['sleep', '30'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(30)\n"
    )
    with pytest.raises(ConfigError, match="timed out"):
        prepared_runtimes._run([sys.executable, str(script)], tmp_path, 2)
    child = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail(f"child {child} outlived the timeout")


def test_every_tflm_build_step_takes_the_download_timeout(tflm_build, monkeypatch) -> None:
    timeouts: list[float] = []
    fake = prepared_runtimes._run

    def run(argv: list[str], cwd: Path, timeout_s: float) -> str:
        timeouts.append(timeout_s)
        return fake(argv, cwd, timeout_s)

    monkeypatch.setattr(prepared_runtimes, "_run", run)
    prepare_runtime("tflm", asset_s=7)
    assert timeouts == [7, 7, 7, 7]


def test_a_python_without_tar_filters_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.delattr(tarfile, "data_filter")
    with pytest.raises(ConfigError, match="extraction filters"):
        prepared_runtimes._download_tree(TFLM.source, tmp_path / "tflite-micro", 5)


def test_an_atfe_root_without_clang_is_refused_before_downloading(
    tflm_build, tmp_path, monkeypatch
):
    monkeypatch.setenv("ATFE_ROOT", str(tmp_path / "empty"))
    monkeypatch.setattr(
        prepared_runtimes, "_download_tree", lambda *_: pytest.fail("downloaded first")
    )
    with pytest.raises(ConfigError, match="ATFE_ROOT has no"):
        prepare_runtime("tflm")


def test_a_build_step_that_hangs_is_stopped(tmp_path: Path) -> None:
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]
    with pytest.raises(ConfigError, match="timed out at .* after 0.5 s"):
        prepared_runtimes._run(argv, tmp_path, 0.5)


def _tarball(entries: dict[str, tuple[bytes, int]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, (data, mode) in entries.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_the_tflm_tree_is_the_tarball_at_the_record_commit(tmp_path, monkeypatch) -> None:
    root = f"tflite-micro-{TFLM.source.commit}"
    data = _tarball({f"{root}/x.h": (b"// x\n", 0o644), f"{root}/get.sh": (b"#!/bin/sh\n", 0o755)})
    urls: list[str] = []
    monkeypatch.setattr(
        prepared_runtimes, "urlopen", lambda url, timeout: urls.append(url) or io.BytesIO(data)
    )
    tree = prepared_runtimes._download_tree(TFLM.source, tmp_path / "tflite-micro", 5)
    assert urls == [
        f"https://github.com/tensorflow/tflite-micro/archive/{TFLM.source.commit}.tar.gz"
    ]
    assert tree == tmp_path / "tflite-micro" and (tree / "x.h").read_text() == "// x\n"
    if os.name == "posix":  # TFLM's make runs its download scripts directly
        assert (tree / "get.sh").stat().st_mode & 0o100


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ({"a/x.h": (b"", 0o644), "b/y.h": (b"", 0o644)}, "does not unpack to one directory"),
        ({"../escape.h": (b"", 0o644)}, "Cannot download"),
    ],
)
def test_a_tflm_tarball_that_is_not_one_tree_is_refused(tmp_path, monkeypatch, entries, message):
    data = _tarball(entries)
    monkeypatch.setattr(prepared_runtimes, "urlopen", lambda url, timeout: io.BytesIO(data))
    with pytest.raises(ConfigError, match=message):
        prepared_runtimes._download_tree(TFLM.source, tmp_path / "tflite-micro", 5)


def test_a_tflm_request_uses_the_prepared_runtime(tflm_build, tmp_path: Path) -> None:
    prepared = prepare_runtime("tflm")
    assert _request(tmp_path, EngineType.TFLM).runtime == prepared.runtime


def test_a_tflm_request_without_a_prepared_runtime_names_the_prepare_command(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path / "cache"))
    with pytest.raises(ConfigError, match="No prepared tflm 85638f0 runtime") as exc:
        _request(tmp_path, EngineType.TFLM)
    assert "hpx runtimes prepare tflm 85638f0" in (exc.value.hint or "")
