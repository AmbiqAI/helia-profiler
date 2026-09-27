"""Verified prepared-runtime records and host-only NSX staging."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import re

from .errors import ConfigError
from .pipeline import PipelineContext


@dataclass(frozen=True)
class FixtureFile:
    path: Path
    sha256: str

    def read(self) -> bytes:
        _digest(self.sha256)
        data = self.path.read_bytes()
        if hashlib.sha256(data).hexdigest() != self.sha256:
            raise ValueError(f"Fixture hash mismatch: {self.path}")
        return data


def _digest(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Expected a SHA256 fixture identity")
    return value


def _object(value: object, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict) or value.keys() != keys:
        raise ValueError(f"Invalid runtime {label} fields")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate runtime manifest field")
        result[key] = value
    return result


def _path(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_./+-]+", name):
        raise ValueError("Unsafe runtime relative path")
    relative = Path(name)
    path = (root / relative).resolve()
    if relative.is_absolute() or ".." in relative.parts or not path.is_relative_to(root.resolve()):
        raise ValueError("Runtime path escapes header root")
    return path


@dataclass(frozen=True)
class RuntimeProvider:
    name: str
    url: str
    revision: str


@dataclass(frozen=True)
class RuntimeABI:
    toolchain: str
    cpu: str
    float_abi: str
    short_enums: bool


@dataclass(frozen=True)
class RuntimeHeader:
    name: str
    sha256: str


@dataclass(frozen=True)
class RuntimeManifest:
    schema_version: int
    archive_sha256: str
    providers: tuple[RuntimeProvider, ...]
    abi: RuntimeABI
    headers: tuple[RuntimeHeader, ...]
    include_dirs: tuple[str, ...]


@dataclass(frozen=True)
class VerifiedPreparedRuntime:
    archive: FixtureFile
    header_root: Path
    manifest: FixtureFile
    record: RuntimeManifest

    def header_path(self, name: str) -> Path:
        return _path(self.header_root, name)


@dataclass(frozen=True)
class PreparedUpstreamRuntime:
    """Explicit prebuilt provider with a pinned, vendored header closure."""

    archive: FixtureFile
    header_root: Path
    manifest: FixtureFile

    def verify(self) -> VerifiedPreparedRuntime:
        self.archive.read()
        data = _object(
            json.loads(self.manifest.read(), object_pairs_hook=_unique_object),
            {"schema_version", "archive_sha256", "providers", "abi", "headers", "include_dirs"},
            "manifest",
        )
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError("Unsupported runtime manifest schema version")
        if _digest(data["archive_sha256"]) != self.archive.sha256:
            raise ValueError("Runtime manifest/archive identity mismatch")
        providers = _object(data["providers"], {"tflite-micro", "cmsis-nn"}, "providers")
        sources = []
        for name, url in (
            ("tflite-micro", "https://github.com/tensorflow/tflite-micro"),
            ("cmsis-nn", "https://github.com/ARM-software/CMSIS-NN"),
        ):
            provider = _object(providers[name], {"url", "revision"}, "provider")
            revision = provider["revision"]
            if (
                provider["url"] != url
                or not isinstance(revision, str)
                or not re.fullmatch(r"[0-9a-f]{40}", revision)
            ):
                raise ValueError("Invalid upstream provider source identity")
            sources.append(RuntimeProvider(name, url, revision))
        abi = _object(data["abi"], {"toolchain", "cpu", "float_abi", "short_enums"}, "ABI")
        if (
            abi
            != {"toolchain": "atfe", "cpu": "cortex-m55", "float_abi": "hard", "short_enums": True}
            or type(abi["short_enums"]) is not bool
        ):
            raise ValueError("Unsupported prepared runtime ABI")
        headers = data["headers"]
        includes = data["include_dirs"]
        if (
            not isinstance(headers, dict)
            or not headers
            or not isinstance(includes, list)
            or not includes
        ):
            raise ValueError("Pinned header closure required")
        root = self.header_root.resolve()
        pinned = []
        for name, digest in headers.items():
            path = _path(root, name)
            FixtureFile(path, _digest(digest)).read()
            pinned.append(RuntimeHeader(name, digest))
        for name in includes:
            if not _path(root, name).is_dir():
                raise ValueError("Missing runtime include directory")
        if len(set(includes)) != len(includes):
            raise ValueError("Duplicate runtime include directory")
        return VerifiedPreparedRuntime(
            self.archive,
            root,
            self.manifest,
            RuntimeManifest(
                1,
                self.archive.sha256,
                tuple(sources),
                RuntimeABI(**abi),
                tuple(pinned),
                tuple(includes),
            ),
        )


class _PreparedRuntimeStage:
    name = "prepare_upstream_runtime"

    def __init__(self, runtime: VerifiedPreparedRuntime):
        self.runtime = runtime

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        from .results import NsxModuleRef

        data = self.runtime.record
        module = ctx.work_dir / "prepared-upstream-runtime" / self.runtime.manifest.sha256
        module.mkdir(parents=True, exist_ok=True)
        (module / "runtime.a").write_bytes(self.runtime.archive.read())
        for header in data.headers:
            name, digest = header.name, header.sha256
            target = module / "include" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(FixtureFile(self.runtime.header_path(name), digest).read())
        (module / "provider-manifest.json").write_bytes(self.runtime.manifest.read())
        (module / "nsx-module.yaml").write_text(
            "schema_version: 1\nmodule:\n  name: hpx-upstream-runtime\n  type: runtime\n  version: '0.1.0'\n"
            "support:\n  ambiqsuite: true\n  zephyr: false\n"
            "build:\n  cmake:\n    targets: [nsx::tflite_micro]\n"
            "depends:\n  required: [nsx-core, nsx-soc-hal]\n"
        )
        includes = "\n".join(
            '  "${CMAKE_CURRENT_LIST_DIR}/include/' + name + '"' for name in data.include_dirs
        )
        (module / "CMakeLists.txt").write_text(
            "add_library(hpx_upstream_runtime STATIC IMPORTED GLOBAL)\n"
            'set_target_properties(hpx_upstream_runtime PROPERTIES IMPORTED_LOCATION "${CMAKE_CURRENT_LIST_DIR}/runtime.a")\n'
            "target_include_directories(hpx_upstream_runtime INTERFACE\n" + includes + "\n)\n"
            "target_compile_definitions(hpx_upstream_runtime INTERFACE TF_LITE_STATIC_MEMORY CMSIS_NN ARM_NN_ENABLE_F16=0 ARM_NN_ENABLE_F32=0)\n"
            "add_library(nsx::tflite_micro ALIAS hpx_upstream_runtime)\n"
        )
        if ctx.engine_artifacts is None:
            raise ConfigError("Engine preparation did not produce artifacts")
        ctx.engine_artifacts = replace(
            ctx.engine_artifacts,
            extra_modules=[NsxModuleRef(name="hpx-upstream-runtime", path=module, local=True)],
            cmake_vars={},
        )
