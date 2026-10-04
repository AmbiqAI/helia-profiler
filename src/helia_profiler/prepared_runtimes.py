"""Prepared runtime archives built from the runtime records.

A fixed-fixture build for an engine that links a prepared archive uses the one
``hpx runtimes prepare`` built from the engine's runtime record, kept in the hpx
cache under ``runtimes/<name>/<version>/``.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

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
    if record.name != "helia-rt":
        raise ConfigError(f"hpx prepares archives for helia-rt only, not {record.name}")
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
    from .engines.helia_rt.download import _fetch_github_release

    record, kernels = _prepared_record(name, version)
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
    directory = prepared_directory(record)
    directory.parent.mkdir(parents=True, exist_ok=True)
    for leftover in directory.parent.glob(f".{directory.name}-old-*"):
        shutil.rmtree(leftover, ignore_errors=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{directory.name}-", dir=directory.parent))
    staging.chmod(0o755)
    try:
        _stage(record, kernels, dist, staging)
        _load(record, staging).runtime.verify()
        _install(staging, directory)
    except (OSError, ValueError) as exc:
        raise ConfigError(f"Cannot prepare {record.name} {record.version}: {exc}") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return _load(record, directory)


def _stage(record: RuntimeRecord, kernels: RuntimeSource, dist: Path, staging: Path) -> None:
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
        "abi": {"toolchain": "atfe", "cpu": "cortex-m55", "float_abi": "hard", "short_enums": True},
        "build": {"consumer_defines": list(_HELIA_RT_DEFINES), "kernel_dir": "helia"},
        "headers": headers,
        "include_dirs": list(_HELIA_RT_INCLUDE_DIRS),
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
