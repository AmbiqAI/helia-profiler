"""Hash-verified prepared-runtime declarations, host-only NSX staging and the proof a built app links exactly that stage."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePath

from .errors import ConfigError


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
#: Each stack's kernel library by baseline project name, and its runtime record name.
STACK_KERNEL_LIBRARY = {"upstream": "arm-cmsis-nn", "helia-rt": "ns-cmsis-nn"}
STACK_RUNTIME_RECORD = {"upstream": "tflm", "helia-rt": "helia-rt"}
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
class PreparedRuntimeFacts:
    """What a prepared runtime's manifest declares: its stack, archive and providers."""

    stack: str
    archive_sha256: str
    providers: tuple[RuntimeProvider, ...]


def read_prepared_runtime_manifest(manifest: FixtureFile) -> PreparedRuntimeFacts:
    """Read a prepared runtime's manifest without verifying its archive or headers.

    The manifest's own hash and declared fields are checked;
    :meth:`PreparedUpstreamRuntime.verify` also checks the archive, ABI and
    header closure.
    """
    return _manifest_facts(manifest)[2]


def _manifest_facts(manifest: FixtureFile) -> tuple[int, dict, PreparedRuntimeFacts]:
    raw = json.loads(manifest.read(), object_pairs_hook=_unique_object)
    schema = raw.get("schema_version") if isinstance(raw, dict) else None
    if type(schema) is not int or schema not in (1, 2):
        raise ValueError("Unsupported runtime manifest schema version")
    fields = {"schema_version", "archive_sha256", "providers", "abi", "headers", "include_dirs"}
    data = _object(raw, fields | ({"stack", "build"} if schema == 2 else set()), "manifest")
    stack = data["stack"] if schema == 2 else "upstream"
    if schema == 2 and (not isinstance(stack, str) or stack not in _STACK_KERNEL_DIR):
        raise ValueError("Unsupported prepared runtime stack")
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
    facts = PreparedRuntimeFacts(stack, _digest(data["archive_sha256"]), tuple(sources))
    return schema, data, facts


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
        schema, data, facts = _manifest_facts(self.manifest)
        stack, sources = facts.stack, list(facts.providers)
        if facts.archive_sha256 != self.archive.sha256:
            raise ValueError("Runtime manifest/archive identity mismatch")
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


#: Runtime providers a prepared-runtime build must not also resolve or link:
#: any module whose name carries one of these, and the other prepared stacks.
_RUNTIME_PROVIDER = re.compile(r"helia-rt|tflite-micro|cmsis-nn")
_PREPARED_MODULES = frozenset(name for name, _, _ in PREPARED_RUNTIME_MODULES.values())
#: Archives and objects a link map lists as inputs, e.g. ``.../libx.a(y.o):`` or ``.../y.o:``.
_MAP_INPUT = re.compile(r"((?:[A-Za-z]:)?[^\s():]+)\.(?:a|o|obj)(?=[(:])")
#: An LLD map input line: address, load address, size and alignment columns, then the
#: input path, which may contain spaces.
_LLD_INPUT = re.compile(r"\s*(?:[0-9A-Fa-f]+\s+){4}((?:[A-Za-z]:)?[^():]+?)\.(?:a|o|obj)(?=[(:])")


def _linked_components(map_text: str, app: PurePath) -> set[str]:
    """Path components naming each linked input, not the directories above the app.

    Inputs under ``app`` and relative inputs contribute every component; other
    absolute inputs (the toolchain's libraries) contribute the components after
    their first ``modules`` or ``_nsx`` directory, or else their parent directory
    and file stem. Underscores are read as hyphens, the NSX build-directory spelling.
    """
    # Windows maps may spell separators either way, whatever the app path uses.
    root = str(app).replace("\\", "/").rstrip("/")
    names = set()
    for line in map_text.splitlines():
        lld = _LLD_INPUT.match(line)
        for found in [lld.group(1)] if lld else _MAP_INPUT.findall(line):
            names.update(_input_components(found.replace("\\", "/"), root))
    return names


def _input_components(path: str, root: str) -> set[str]:
    """Components of one ``/``-separated input path, per :func:`_linked_components`."""
    prefix = path[: len(root) + 1]
    # A drive-letter root is a Windows path, whose case does not matter.
    windows = re.match(r"[A-Za-z]:", root) is not None
    if prefix == root + "/" or (windows and prefix.lower() == (root + "/").lower()):
        parts = path[len(root) + 1 :].split("/")
    else:
        parts = path.split("/")
        if path.startswith("/") or re.match(r"[A-Za-z]:", path):
            marks = [i for i, part in enumerate(parts) if part in ("modules", "_nsx")]
            parts = parts[marks[0] + 1 :] if marks else parts[-2:]
    return {part.replace("_", "-") for part in parts if part}


def _prove_link_map(map_text: str, app: PurePath, stack: str) -> None:
    """Refuse a link map that lacks the stack's archive or names another runtime provider."""
    name = PREPARED_RUNTIME_MODULES[stack][0]
    others = _PREPARED_MODULES - {name}
    if (
        f"{name}/runtime.a(" not in map_text.replace("\\", "/")
        or any(
            part in others or _RUNTIME_PROVIDER.search(part)
            for part in _linked_components(map_text, app)
        )
        or (stack == "upstream" and ("helia_rt" in map_text or "helia-rt" in map_text))
    ):
        raise ConfigError(f"Link map does not prove the explicit {stack} provider")


def prove_prepared_runtime_build(
    runtime: PreparedUpstreamRuntime,
    verified: VerifiedPreparedRuntime,
    app: Path,
    board: str,
    lock: bytes,
    link_map: bytes,
) -> None:
    """Refuse a built app unless its staged module, resolved lock and link map prove the archive.

    The staged module must hold the verified archive and manifest, the lock
    must resolve that module and no other runtime provider, and the link map
    must name the stack's archive and no other provider.
    """
    import yaml

    stack = verified.record.stack
    name = PREPARED_RUNTIME_MODULES[stack][0]
    others = _PREPARED_MODULES - {name}
    module = app / "modules" / name
    FixtureFile(module / "runtime.a", runtime.archive.sha256).read()
    FixtureFile(module / "provider-manifest.json", runtime.manifest.sha256).read()
    modules = yaml.safe_load(lock)["targets"][board]["modules"]
    if name not in modules or any(m in others or _RUNTIME_PROVIDER.search(m) for m in modules):
        raise ConfigError("Unexpected runtime provider in resolved dependency lock")
    _prove_link_map(link_map.decode(), app, stack)


def prepare_runtime_module(runtime: VerifiedPreparedRuntime, work_dir: Path):
    """Stage exactly the selected verified archive; the engine adapter owns its artifacts."""
    from .results import NsxModuleRef

    data = runtime.record
    module_name, library, alias = PREPARED_RUNTIME_MODULES[data.stack]
    module = work_dir / ("prepared-" + module_name.removeprefix("hpx-")) / runtime.manifest.sha256
    module.mkdir(parents=True, exist_ok=True)
    (module / "runtime.a").write_bytes(runtime.archive.read())
    for header in data.headers:
        name, digest = header.name, header.sha256
        target = module / "include" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(FixtureFile(runtime.header_path(name), digest).read())
    (module / "provider-manifest.json").write_bytes(runtime.manifest.read())
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
    return NsxModuleRef(name=module_name, path=module, local=True)
