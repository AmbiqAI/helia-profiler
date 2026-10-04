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
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError
from .fixture_runtime import FixtureFile, PreparedUpstreamRuntime
from .runtime_records import RuntimeRecord, RuntimeSource, runtime

#: The heliaRT release library a fixture links: Cortex-M55, ATfE, release with logs.
_HELIA_RT_LIBRARY = "lib/libhelia-rt-cm55-atfe-release-with-logs.a"
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
    if record.name != "helia-rt" or record.archive_sha256 is None or record.kernels is None:
        raise ConfigError(f"hpx prepares archives for helia-rt only, not {record.name}")
    return record, record.kernels


def prepare_runtime(
    name: str, version: str | None = None, *, api_s: float = 30, asset_s: float = 300
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
    directory = prepared_directory(record)
    staging = directory.with_name(directory.name + ".partial")
    shutil.rmtree(staging, ignore_errors=True)
    include = staging / "include"
    headers = {}
    for top in _HELIA_RT_HEADER_DIRS:
        for path in sorted((dist / top).rglob("*.h")):
            name_ = path.relative_to(dist).as_posix()
            target = include / name_
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            headers[name_] = _sha256(target)
    archive = staging / "runtime.a"
    shutil.copyfile(dist / _HELIA_RT_LIBRARY, archive)
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
    prepared = _load(record, staging)
    prepared.runtime.verify()
    shutil.rmtree(directory, ignore_errors=True)
    staging.rename(directory)
    return _load(record, directory)


def prepared_runtime(name: str, version: str | None = None) -> PreparedArchive:
    """The prepared archive for a runtime record, or a ConfigError naming the prepare command."""
    record, _ = _prepared_record(name, version)
    directory = prepared_directory(record)
    if not (directory / "provider-manifest.json").is_file():
        raise ConfigError(
            f"No prepared {record.name} {record.version} runtime in {directory}",
            hint=f"Run: hpx runtimes prepare {record.name} {record.version}",
        )
    return _load(record, directory)


def _load(record: RuntimeRecord, directory: Path) -> PreparedArchive:
    manifest = directory / "provider-manifest.json"
    archive_sha256 = json.loads(manifest.read_text(encoding="utf-8"))["archive_sha256"]
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
