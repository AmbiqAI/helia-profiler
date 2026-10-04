"""Prepared runtime archives built from the runtime records.

A fixed-fixture build for an engine that links a prepared archive uses the one
``hpx runtimes prepare`` built from the engine's runtime record, kept in the hpx
cache under ``runtimes/<name>/<version>/``.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import zlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from .config import DEFAULT_DOWNLOAD_API_S, DEFAULT_DOWNLOAD_ASSET_S
from .errors import ConfigError
from .fixture_runtime import FixtureFile, PreparedUpstreamRuntime
from .runtime_records import RuntimeRecord, RuntimeSource, runtime

#: The heliaRT build a fixture links: its ATfE release-with-logs library for the fixture core.
_HELIA_RT_VARIANT = "release-with-logs"
#: Release directories whose headers form the pinned header closure.
_HELIA_RT_HEADER_DIRS = ("tensorflow", "third_party", "signal")
_HELIA_RT_INCLUDE_DIRS = (
    ".",
    "third_party/flatbuffers/include",
    "third_party/gemmlowp",
    "third_party/ruy",
    "third_party/kissfft",
    "third_party/cmsis/CMSIS/Core/Include",
    "third_party/ns_cmsis_nn",
    "third_party/ns_cmsis_nn/Include",
)
#: The Cortex-M55 library is built with FP32 and FP16 kernels enabled.
_HELIA_RT_DEFINES = (
    "TF_LITE_STATIC_MEMORY",
    "CMSIS_NN",
    "ARM_NN_ENABLE_F32=1",
    "ARM_NN_ENABLE_F16=1",
)

_TFLM_MAKEFILE = "tensorflow/lite/micro/tools/make/Makefile"
#: TFLM's own source list for the fixture core with CMSIS-NN kernels.
_TFLM_MAKE_SELECTION = (
    "TARGET=cortex_m_generic",
    "TARGET_ARCH=cortex-m55",
    "OPTIMIZED_KERNEL_DIR=cmsis_nn",
    "TOOLCHAIN=armclang",
    "CC_TOOL=clang",
    "CXX_TOOL=clang++",
)
#: The CMSIS-NN commit TFLM's own download script pins.
_TFLM_CMSIS_NN_SCRIPT = "tensorflow/lite/micro/tools/make/ext_libs/cmsis_nn_download.sh"
_TFLM_CMSIS_NN_PIN = re.compile(r'^ZIP_PREFIX_NN="([0-9a-f]{40})"', re.M)
#: Upstream optimization flags dropped from every translation unit, and the ones added.
_TFLM_DROPPED_FLAGS = ("-O", "-ffp-mode=")
_TFLM_ADDED_FLAGS = ("--config=newlib.cfg", "-O3", "-ffast-math", "-fshort-enums", "-DNDEBUG")
#: The only environment build steps see: TFLM's make takes its variables from the
#: environment and clang its include paths, so anything else could change the archive.
#: The download scripts keep the proxy and CA settings.
_TFLM_ENV = (
    "PATH",
    "HOME",
    "TMPDIR",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "CURL_CA_BUNDLE",
    "all_proxy",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
)
#: Host tools TFLM's make and its download scripts need.
_TFLM_HOST_TOOLS = ("make", "bash")
_TFLM_HEADER_DIRS = ("tensorflow", "signal", "third_party")
_TFLM_DOWNLOADS = "tensorflow/lite/micro/tools/make/downloads"
_TFLM_INCLUDE_DIRS = (
    ".",
    _TFLM_DOWNLOADS,
    f"{_TFLM_DOWNLOADS}/gemmlowp",
    f"{_TFLM_DOWNLOADS}/flatbuffers/include",
    f"{_TFLM_DOWNLOADS}/kissfft",
    f"{_TFLM_DOWNLOADS}/ruy",
    f"{_TFLM_DOWNLOADS}/cmsis",
    f"{_TFLM_DOWNLOADS}/cmsis/CMSIS/Core/Include",
    f"{_TFLM_DOWNLOADS}/cmsis_nn",
    f"{_TFLM_DOWNLOADS}/cmsis_nn/Include",
)
_ABI = {"toolchain": "atfe", "cpu": "cortex-m55", "float_abi": "hard", "short_enums": True}


@dataclass(frozen=True)
class PreparedArchive:
    """A prepared archive in the hpx cache and how it compares with its record."""

    record: RuntimeRecord
    directory: Path
    runtime: PreparedUpstreamRuntime

    @property
    def matches_record(self) -> bool:
        """Whether the archive bytes equal the record's; a difference is reported, not refused."""
        return self.runtime.archive.sha256 == self.record.archive_sha256


def prepared_directory(record: RuntimeRecord) -> Path:
    from .hostenv.cache_dirs import hpx_cache_root

    return hpx_cache_root() / "runtimes" / record.name / record.version


def _prepared_record(name: str, version: str | None) -> tuple[RuntimeRecord, RuntimeSource]:
    record = runtime(name, version)
    if record is None:
        label = f"{name} {version}" if version is not None else name
        raise ConfigError(f"No runtime record for {label}")
    if record.name not in _PREPARERS:
        raise ConfigError(f"hpx prepares no archive for {record.name}")
    if record.archive_sha256 is None or record.kernels is None:
        raise ConfigError(f"The {record.name} {record.version} record pins no prepared archive")
    return record, record.kernels


def prepare_runtime(
    name: str,
    version: str | None = None,
    *,
    api_s: float = DEFAULT_DOWNLOAD_API_S,
    asset_s: float = DEFAULT_DOWNLOAD_ASSET_S,
) -> PreparedArchive:
    """Build the prepared archive for a runtime record into the hpx cache."""
    record, kernels = _prepared_record(name, version)
    fetch, stage = _PREPARERS[record.name]
    # A download process that detached from its stopped group may still be writing here.
    with tempfile.TemporaryDirectory(
        prefix=f"hpx-{record.name}-", ignore_cleanup_errors=True
    ) as work:
        try:
            source = fetch(record, kernels, Path(work), api_s, asset_s)
        except (OSError, ValueError, IndexError) as exc:
            raise ConfigError(f"Cannot prepare {record.name} {record.version}: {exc}") from exc
        directory = prepared_directory(record)
        directory.parent.mkdir(parents=True, exist_ok=True)
        for leftover in directory.parent.glob(f".{directory.name}-old-*"):
            shutil.rmtree(leftover, ignore_errors=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{directory.name}-", dir=directory.parent))
        staging.chmod(0o755)
        try:
            stage(record, kernels, source, staging)
            _load(record, staging).runtime.verify()
            _install(staging, directory)
        except (OSError, ValueError) as exc:
            raise ConfigError(f"Cannot prepare {record.name} {record.version}: {exc}") from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return _load(record, directory)


def _fetch_helia_rt(
    record: RuntimeRecord, kernels: RuntimeSource, work: Path, api_s: float, asset_s: float
) -> Path:
    from .engines.helia_rt.download import _fetch_github_release

    dist, _ = _fetch_github_release(
        record.source.repo, f"helia-rt-v{record.version}", api_s=api_s, asset_s=asset_s
    )
    commit = re.search(r"^Commit:\s*([0-9a-f]{7,40})\s*$", _read(dist / "MANIFEST.txt"), re.M)
    if commit is None or not record.source.commit.startswith(commit.group(1)):
        raise ConfigError(
            f"The helia-rt v{record.version} release was not built from {record.source.commit}"
        )
    missing = [top for top in _HELIA_RT_HEADER_DIRS if not (dist / top).is_dir()]
    if missing:
        raise ConfigError(f"The helia-rt v{record.version} release has no {', '.join(missing)}/")
    return dist


def _stage_helia_rt(
    record: RuntimeRecord, kernels: RuntimeSource, dist: Path, staging: Path
) -> None:
    from .engines.helia_rt.artifacts import _core_tag, _library_name, _toolchain_tag
    from .fixture_target import FIXTURE_BOARD

    include = staging / "include"
    headers = {}
    for top in _HELIA_RT_HEADER_DIRS:
        for path in sorted((dist / top).rglob("*.h")):
            name = path.relative_to(dist).as_posix()
            target = include / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            headers[name] = _sha256(target)
    library = _library_name(_core_tag(FIXTURE_BOARD), _toolchain_tag("atfe"), _HELIA_RT_VARIANT)
    archive = staging / "runtime.a"
    shutil.copyfile(dist / "lib" / library, archive)
    manifest = {
        "schema_version": 2,
        "stack": "helia-rt",
        "archive_sha256": _sha256(archive),
        "providers": {
            "helia-rt": {
                "url": f"https://github.com/{record.source.repo}",
                "revision": record.source.commit,
            },
            "ns-cmsis-nn": {
                "url": f"https://github.com/{kernels.repo}",
                "revision": kernels.commit,
            },
        },
        "abi": _ABI,
        "build": {"consumer_defines": list(_HELIA_RT_DEFINES), "kernel_dir": "helia"},
        "headers": headers,
        "include_dirs": list(_HELIA_RT_INCLUDE_DIRS),
    }
    (staging / "provider-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _fetch_tflm(
    record: RuntimeRecord, kernels: RuntimeSource, work: Path, api_s: float, asset_s: float
) -> Path:
    """Build TFLM's archive from the record's commit with ATfE; returns the source tree.

    TFLM's make dry run fetches its pinned third-party sources and lists the
    translation units; each is compiled from the tree with relative paths, so
    the archive embeds no host path.
    """
    from .hostenv.toolchains import resolve_toolchain_executable
    from .vocab import Toolchain

    if sys.platform == "win32":
        raise ConfigError(
            "Preparing tflm is not supported on Windows; prepare it on Linux or macOS"
        )
    missing = [tool for tool in _TFLM_HOST_TOOLS if shutil.which(tool) is None]
    if missing:
        raise ConfigError(f"Preparing tflm needs {' and '.join(missing)} on PATH")
    if not os.environ.get("ATFE_ROOT"):
        raise ConfigError(
            "Preparing tflm needs ATFE_ROOT",
            hint="Set ATFE_ROOT to an Arm Toolchain for Embedded installation (see hpx doctor).",
        )
    cc, cxx, ar = (
        os.path.abspath(resolve_toolchain_executable(Toolchain.ATFE, tool))
        for tool in ("clang", "clang++", "llvm-ar")
    )
    missing = [tool for tool in (cc, cxx, ar) if not Path(tool).is_file()]
    if missing:
        raise ConfigError(f"ATFE_ROOT has no {', '.join(missing)}")
    tree = _download_tree(record.source, work / "tflite-micro", asset_s)
    pin = _TFLM_CMSIS_NN_PIN.search(_read(tree / _TFLM_CMSIS_NN_SCRIPT))
    if pin is None or pin.group(1) != kernels.commit:
        pinned = pin.group(1) if pin else "no commit"
        raise ConfigError(
            f"tflite-micro {record.source.commit[:8]} pins CMSIS-NN {pinned}, "
            f"not the record's {kernels.commit}"
        )
    make = ["make", "-n", "-f", _TFLM_MAKEFILE, *_TFLM_MAKE_SELECTION, "microlite"]
    listing = _run(make, tree, asset_s, group=True)
    objects = work / "objects"
    objects.mkdir()
    commands = []
    for line in listing.splitlines():
        if not line.startswith(("clang ", "clang++ ")):
            continue
        try:
            args = shlex.split(line)
            if "-c" not in args:
                continue
            source = Path(args[args.index("-c") + 1])
            args[args.index("-o") + 1] = str(objects / f"{len(commands):03}-{source.stem}.o")
        except (ValueError, IndexError) as exc:
            raise ConfigError(
                f"Cannot read TFLM's make dry-run line ({exc}): {line[:200]}"
            ) from exc
        flags = [
            "--target=arm-none-eabi" if arg.startswith("--target=") else arg
            for arg in args[1:]
            if not arg.startswith(_TFLM_DROPPED_FLAGS)
        ]
        tool = cxx if args[0] == "clang++" else cc
        commands.append([tool, *_TFLM_ADDED_FLAGS, f"-ffile-prefix-map={tree}=.", *flags])
    if not commands:
        raise ConfigError("TFLM's make dry run listed no translation units")
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:
        for _ in pool.map(lambda argv: _run(argv, tree, asset_s), commands):
            pass
    archive = [command[command.index("-o") + 1] for command in commands]
    _run([ar, "rcsD", str(work / "runtime.a"), *archive], tree, asset_s)
    return tree


def _download_tree(source: RuntimeSource, tree: Path, timeout_s: float) -> Path:
    if not hasattr(tarfile, "data_filter"):
        raise ConfigError("Preparing tflm needs a Python whose tarfile has extraction filters")
    url = f"https://github.com/{source.repo}/archive/{source.commit}.tar.gz"
    try:
        with urlopen(url, timeout=timeout_s) as response:
            data = response.read()
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            roots = {Path(name).parts[0] for name in tar.getnames()}
            if len(roots) != 1:
                raise ValueError(f"{url} does not unpack to one directory")
            tar.extractall(tree.parent, filter="data")
    except (
        OSError,
        URLError,
        HTTPException,
        EOFError,
        zlib.error,
        ValueError,
        tarfile.TarError,
    ) as exc:
        raise ConfigError(f"Cannot download {url}: {exc}") from exc
    (tree.parent / roots.pop()).rename(tree)
    return tree


def _run(argv: list[str], cwd: Path, timeout_s: float, *, group: bool = False) -> str:
    """Run a build step in ``cwd`` and return its stdout, or raise naming the step.

    A ``group`` step (make, whose download scripts start their own processes) runs in
    its own session and is stopped with everything it started; other steps stay in
    hpx's process group, so a terminal interrupt reaches them directly.
    """
    step = argv[argv.index("-c") + 1] if "-c" in argv[:-1] else " ".join(argv[:2])
    env = {name: os.environ[name] for name in _TFLM_ENV if name in os.environ}
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=group,
        )
    except OSError as exc:
        raise ConfigError(f"Cannot run {argv[0]}: {exc}") from exc
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except BaseException as exc:
        _stop(process, group=group)
        if isinstance(exc, subprocess.TimeoutExpired):
            raise ConfigError(f"Preparing tflm timed out at {step} after {timeout_s:g} s") from exc
        raise
    if process.returncode != 0:
        tail = "\n".join(stderr.strip().splitlines()[-20:])
        raise ConfigError(f"Preparing tflm failed at {step} (exit {process.returncode}):\n{tail}")
    return stdout


def _stop(process: subprocess.Popen[str], *, group: bool) -> None:
    """Stop a build step; a group step gets TERM first, so download scripts can clean up."""
    posix_group = group and os.name == "posix"
    try:
        if posix_group and _signal_group(process, signal.SIGTERM):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and _signal_group(process, 0):
                process.poll()
                time.sleep(0.05)
    finally:
        if posix_group:
            _signal_group(process, signal.SIGKILL)
        else:
            process.kill()
        process.wait()
        # Not read: a grandchild that left the group may still hold them open.
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()


def _signal_group(process: subprocess.Popen[str], sig: int) -> bool:
    """Signal the step's process group; False once it is gone (or, on macOS, holds only zombies)."""
    try:
        os.killpg(process.pid, sig)
    except OSError:  # ESRCH, or EPERM where only zombies remain
        return False
    return True


def _stage_tflm(record: RuntimeRecord, kernels: RuntimeSource, tree: Path, staging: Path) -> None:
    headers = {}
    for top in _TFLM_HEADER_DIRS:
        for path in sorted((tree / top).rglob("*.h")):
            name = path.relative_to(tree).as_posix()
            target = staging / "include" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            headers[name] = _sha256(target)
    archive = staging / "runtime.a"
    shutil.copyfile(tree.parent / "runtime.a", archive)
    manifest = {
        "schema_version": 1,
        "archive_sha256": _sha256(archive),
        "providers": {
            "tflite-micro": {
                "url": f"https://github.com/{record.source.repo}",
                "revision": record.source.commit,
            },
            "cmsis-nn": {"url": f"https://github.com/{kernels.repo}", "revision": kernels.commit},
        },
        "abi": _ABI,
        "headers": headers,
        "include_dirs": list(_TFLM_INCLUDE_DIRS),
    }
    (staging / "provider-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _install(staging: Path, directory: Path) -> None:
    """Replace ``directory`` with ``staging``, restoring the previous install if the move fails.

    Two renames, not one atomic swap: a build that reads the cache between them
    finds no prepared runtime and is told to prepare again.
    """
    retired = None
    if directory.exists():
        retired = Path(tempfile.mkdtemp(prefix=f".{directory.name}-old-", dir=directory.parent))
        directory.rename(retired / "install")
    try:
        staging.rename(directory)
    except BaseException:
        if retired is not None:
            (retired / "install").rename(directory)
            shutil.rmtree(retired, ignore_errors=True)
        raise
    if retired is not None:
        shutil.rmtree(retired, ignore_errors=True)


def prepared_runtime(name: str, version: str | None = None) -> PreparedArchive:
    """The prepared archive for a runtime record, or a ConfigError naming the prepare command."""
    record, _ = _prepared_record(name, version)
    directory = prepared_directory(record)
    if not (directory / "provider-manifest.json").is_file():
        raise ConfigError(
            f"No prepared {record.name} {record.version} runtime in {directory}",
            hint=f"Run: hpx runtimes prepare {record.name} {record.version}",
        )
    prepared = _load(record, directory)
    try:
        prepared.runtime.verify()
    except (OSError, ValueError) as exc:
        raise ConfigError(
            f"Prepared {record.name} {record.version} runtime in {directory} is damaged: {exc}",
            hint=f"Run: hpx runtimes prepare {record.name} {record.version}",
        ) from exc
    return prepared


def _load(record: RuntimeRecord, directory: Path) -> PreparedArchive:
    manifest = directory / "provider-manifest.json"
    try:
        archive_sha256 = json.loads(manifest.read_text(encoding="utf-8"))["archive_sha256"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ConfigError(
            f"Prepared runtime manifest {manifest} is unreadable: {exc}",
            hint=f"Run: hpx runtimes prepare {record.name} {record.version}",
        ) from exc
    return PreparedArchive(
        record,
        directory,
        PreparedUpstreamRuntime(
            archive=FixtureFile(directory / "runtime.a", archive_sha256),
            header_root=directory / "include",
            manifest=FixtureFile(manifest, _sha256(manifest)),
        ),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from exc


_Fetch = Callable[[RuntimeRecord, RuntimeSource, Path, float, float], Path]
_Stage = Callable[[RuntimeRecord, RuntimeSource, Path, Path], None]
#: How each runtime's archive is fetched or built, then staged with its manifest.
_PREPARERS: dict[str, tuple[_Fetch, _Stage]] = {
    "helia-rt": (_fetch_helia_rt, _stage_helia_rt),
    "tflm": (_fetch_tflm, _stage_tflm),
}
