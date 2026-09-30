"""Hash-verified prepared-runtime declarations and host-only NSX staging."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import re

from .engines.base import HeliaRtArtifacts
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
    """Manifest-declared ABI; archive members are not independently inspected."""

    toolchain: str
    cpu: str
    float_abi: str
    short_enums: bool


@dataclass(frozen=True)
class RuntimeHeader:
    name: str
    sha256: str


#: Runtime stacks a prepared archive may provide, with their source repositories.
#: ``upstream`` is manifest schema 1; ``helia-rt`` is schema 2.
_STACK_PROVIDERS = {
    "upstream": (
        ("tflite-micro", "https://github.com/tensorflow/tflite-micro"),
        ("cmsis-nn", "https://github.com/ARM-software/CMSIS-NN"),
    ),
    "helia-rt": (
        ("helia-rt", "https://github.com/AmbiqAI/helia-rt"),
        ("ns-cmsis-nn", "https://github.com/AmbiqAI/ns-cmsis-nn"),
    ),
}
#: Kernel directory each schema-2 stack declares.
_STACK_KERNEL_DIR = {"helia-rt": "helia"}
#: Consumer defines of schema 1, which the manifest does not declare.
_UPSTREAM_DEFINES = (
    "TF_LITE_STATIC_MEMORY",
    "CMSIS_NN",
    "ARM_NN_ENABLE_F16=0",
    "ARM_NN_ENABLE_F32=0",
)


@dataclass(frozen=True)
class RuntimeManifest:
    schema_version: int
    archive_sha256: str
    providers: tuple[RuntimeProvider, ...]
    abi: RuntimeABI
    headers: tuple[RuntimeHeader, ...]
    include_dirs: tuple[str, ...]
    stack: str = "upstream"
    #: Preprocessor definitions every translation unit using the headers needs.
    consumer_defines: tuple[str, ...] = _UPSTREAM_DEFINES
    kernel_dir: str | None = None


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
    """Explicit prebuilt provider with a pinned, vendored header closure.

    Manifest schema 1 declares the upstream TFLM + CMSIS-NN stack; schema 2
    declares its ``stack`` (``helia-rt``) and the consumer defines and kernel
    directory it was built with.
    """

    archive: FixtureFile
    header_root: Path
    manifest: FixtureFile

    def verify(self) -> VerifiedPreparedRuntime:
        archive = self.archive.read()
        if len(archive) <= 8 or not archive.startswith(b"!<arch>\n"):
            raise ValueError("Prepared runtime requires a nonempty regular archive")
        raw = json.loads(self.manifest.read(), object_pairs_hook=_unique_object)
        schema = raw.get("schema_version") if isinstance(raw, dict) else None
        if type(schema) is not int or schema not in (1, 2):
            raise ValueError("Unsupported runtime manifest schema version")
        fields = {"schema_version", "archive_sha256", "providers", "abi", "headers", "include_dirs"}
        data = _object(raw, fields | ({"stack", "build"} if schema == 2 else set()), "manifest")
        stack = data["stack"] if schema == 2 else "upstream"
        if schema == 2 and (not isinstance(stack, str) or stack not in _STACK_KERNEL_DIR):
            raise ValueError("Unsupported prepared runtime stack")
        if _digest(data["archive_sha256"]) != self.archive.sha256:
            raise ValueError("Runtime manifest/archive identity mismatch")
        declared = _STACK_PROVIDERS[stack]
        providers = _object(data["providers"], {name for name, _ in declared}, "providers")
        sources = []
        for name, url in declared:
            provider = _object(providers[name], {"url", "revision"}, "provider")
            revision = provider["revision"]
            if (
                provider["url"] != url
                or not isinstance(revision, str)
                or not re.fullmatch(r"[0-9a-f]{40}", revision)
            ):
                raise ValueError("Invalid runtime provider source identity")
            sources.append(RuntimeProvider(name, url, revision))
        defines, kernel_dir = _UPSTREAM_DEFINES, None
        if schema == 2:
            build = _object(data["build"], {"consumer_defines", "kernel_dir"}, "build")
            defines, kernel_dir = build["consumer_defines"], build["kernel_dir"]
            if (
                not isinstance(defines, list)
                or not all(
                    isinstance(d, str) and re.fullmatch(r"[A-Z_][A-Z0-9_]*(=[A-Za-z0-9_]+)?", d)
                    for d in defines
                )
                or len({d.split("=")[0] for d in defines}) != len(defines)
                or "TF_LITE_STATIC_MEMORY" not in defines
            ):
                raise ValueError("Invalid runtime consumer defines")
            if kernel_dir != _STACK_KERNEL_DIR[stack]:
                raise ValueError("Unexpected runtime kernel directory")
            defines = tuple(defines)
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
                schema,
                self.archive.sha256,
                tuple(sources),
                RuntimeABI(**abi),
                tuple(pinned),
                tuple(includes),
                stack,
                defines,
                kernel_dir,
            ),
        )


#: NSX module, CMake target and alias each prepared stack is staged as.
PREPARED_RUNTIME_MODULES = {
    "upstream": ("hpx-upstream-runtime", "hpx_upstream_runtime", "nsx::tflite_micro"),
    "helia-rt": ("hpx-heliart-runtime", "hpx_heliart_runtime", "nsx::helia_rt"),
}


class _PreparedRuntimeStage:
    def __init__(self, runtime: VerifiedPreparedRuntime):
        self.runtime = runtime
        self.name = (
            "prepare_upstream_runtime"
            if runtime.record.stack == "upstream"
            else "prepare_heliart_runtime"
        )

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        from .results import NsxModuleRef

        data = self.runtime.record
        module_name, library, alias = PREPARED_RUNTIME_MODULES[data.stack]
        module = (
            ctx.work_dir
            / ("prepared-" + module_name.removeprefix("hpx-"))
            / self.runtime.manifest.sha256
        )
        module.mkdir(parents=True, exist_ok=True)
        (module / "runtime.a").write_bytes(self.runtime.archive.read())
        for header in data.headers:
            name, digest = header.name, header.sha256
            target = module / "include" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(FixtureFile(self.runtime.header_path(name), digest).read())
        (module / "provider-manifest.json").write_bytes(self.runtime.manifest.read())
        (module / "nsx-module.yaml").write_text(
            f"schema_version: 1\nmodule:\n  name: {module_name}\n  type: runtime\n  version: '0.1.0'\n"
            "support:\n  ambiqsuite: true\n  zephyr: false\n"
            f"build:\n  cmake:\n    targets: [{alias}]\n"
            "depends:\n  required: [nsx-core, nsx-soc-hal]\n",
            encoding="utf-8",
        )
        includes = "\n".join(
            '  "${CMAKE_CURRENT_LIST_DIR}/include/' + name + '"' for name in data.include_dirs
        )
        (module / "CMakeLists.txt").write_text(
            f"add_library({library} STATIC IMPORTED GLOBAL)\n"
            f'set_target_properties({library} PROPERTIES IMPORTED_LOCATION "${{CMAKE_CURRENT_LIST_DIR}}/runtime.a")\n'
            f"target_include_directories({library} INTERFACE\n" + includes + "\n)\n"
            f"target_compile_definitions({library} INTERFACE {' '.join(data.consumer_defines)})\n"
            f"add_library({alias} ALIAS {library})\n",
            encoding="utf-8",
        )
        if ctx.engine_artifacts is None:
            raise ConfigError("Engine preparation did not produce artifacts")
        ctx.engine_artifacts = replace(
            ctx.engine_artifacts,
            extra_modules=[NsxModuleRef(name=module_name, path=module, local=True)],
            cmake_vars={},
        )
        artifacts = ctx.engine_artifacts
        if data.stack == "helia-rt" and isinstance(artifacts, HeliaRtArtifacts):
            # The adapter named its own release; record the archive actually linked.
            revision = next(p.revision for p in data.providers if p.name == "helia-rt")
            ctx.engine_artifacts = replace(
                artifacts, heliart_version=f"prepared:{revision}", heliart_variant="prepared"
            )
            if ctx.run_metadata.engine is not None:
                ctx.run_metadata.engine = replace(
                    ctx.run_metadata.engine, version=f"prepared:{revision}"
                )
