"""Frozen run plans: every resolved setting, saved so another host can rebuild it.

A :class:`FrozenRun` is :meth:`Session.resolve` captured as data: the settings
that determine the firmware and the measurement, the compatibility baseline
and its override classification, the model's hash, the toolchain's identity,
the engine identity a profile build uses, and every local file or directory
the build reads. Host-local settings (probe serials, ports, output and work
directories, timeouts) are not stored; :meth:`FrozenRun.config` takes them
for the replaying host. ``intent_hash`` identifies what the plan asks for and
excludes the HPX release.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import MappingProxyType, UnionType
from typing import TYPE_CHECKING, Any, Mapping, TypeVar, get_args, get_type_hints

from ._files import write_text_atomically
from .errors import ConfigError

if TYPE_CHECKING:
    from ._fixture_build import TypedFixture
    from .compiled_build import CompiledBuild
    from .config import ProfileConfig
    from .deps.compatibility import CompatibilityResolution

FROZEN_RUN_SCHEMA = "hpx.frozen-run"
FROZEN_RUN_SCHEMA_VERSION = 1

_T = TypeVar("_T")

#: Resolved fields HPX derives itself; ``load_config`` refuses them as input.
_DERIVED_SETTINGS = ("platform_registry", "compatibility", "compatibility_baseline")

#: Settings that name the host, device or session, or only shape host-side
#: reporting and waits. None of them changes the firmware or what it measures.
_HOST_LOCAL_SETTINGS = (
    ("work_dir",),
    ("verbose",),
    ("clean",),
    ("output",),
    ("timeouts",),
    ("target", "jlink_serial"),
    ("target", "usb_port"),
    ("power", "serial"),
    ("build", "compiler_launcher"),
    ("build", "offline"),
    ("frozen",),  # alias that forces build.offline
)

#: Environment variables that can point a build at local sources.
_SOURCE_VARIABLES = ("SEGGER_RTT_PATH", "CMSIS_NN_PATH")

#: Environment variables the compilers, CMake or NSX read that change what is
#: built. Compiler launchers are host-local and excluded. Values are recorded
#: as strings: files or directories they name (response files, include paths)
#: are not content-identified.
_BUILD_VARIABLES = (
    "ASM",
    "ASMFLAGS",
    "CC",
    "CCC_OVERRIDE_OPTIONS",
    "CFLAGS",
    "CMAKE_BUILD_TYPE",
    "CMAKE_TOOLCHAIN_FILE",
    "COMPILER_PATH",
    "CPATH",
    "CPLUS_INCLUDE_PATH",
    "CPPFLAGS",
    "CXX",
    "CXXFLAGS",
    "C_INCLUDE_PATH",
    "GCC_EXEC_PREFIX",
    "LDFLAGS",
    "LIBRARY_PATH",
    "NSX_ALLOW_VERSION_MISMATCH",
    "NSX_SKIP_COMPAT_CHECK",
)

#: Environment variables that change how a measurement is judged.
_MEASUREMENT_VARIABLES = ("HPX_POWER_ALLOW_NEGATIVE", "HPX_POWER_FULLRATE_XCHECK")

_ENVIRONMENT_VARIABLES = (*_BUILD_VARIABLES, *_MEASUREMENT_VARIABLES)

#: The ExecuTorch ``<model>.pte.json`` export sidecar, recorded beside the model.
_SIDECAR = "model.sidecar"

#: ``engine.config`` keys that point a build at local sources.
_SOURCE_ENGINE_KEYS = ("dist_path", "source_path", "cmsis_nn_path")

_VERSION_LINE = re.compile(r"\d+\.\d+")
_COMMIT = re.compile(r"[0-9a-f]{40}")
#: ``--version`` lines that carry install locations rather than the version.
_LOCATION_LINE = re.compile(r"^(InstalledDir|Configuration file):")


@dataclass(frozen=True)
class FrozenModel:
    """The model a plan was frozen against, identified by content."""

    path: str
    sha256: str
    size_bytes: int


_VERSION_SEPARATOR = " | "


@dataclass(frozen=True)
class ToolchainIdentity:
    """The compiler a plan expects: name and exact version, never a path.

    ``version`` holds the version-bearing lines of ``<compiler> --version``;
    it is ``None`` when the compiler could not be run, with the reason in
    ``unavailable_reason``.
    """

    name: str
    version: str | None
    unavailable_reason: str | None = None

    @property
    def lines(self) -> tuple[str, ...]:
        """The version-bearing lines ``version`` joins, or none when it is unknown."""
        return tuple(self.version.split(_VERSION_SEPARATOR)) if self.version else ()


@dataclass(frozen=True)
class FrozenRuntime:
    """The engine and kernel sources a profile build uses.

    Commits are NSX module or source revisions from the compatibility
    baseline or the installed package. ``kernels_commit`` is ``None`` when the
    engine links no kernel library or its provider is overridden.
    """

    name: str
    version: str | None
    source_commit: str | None
    kernels_module: str | None = None
    kernels_commit: str | None = None
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class LocalInput:
    """A local file or directory the build reads instead of a pinned source.

    Files are identified by content. A directory is recorded by path only, so
    a plan with one is not portable to another host.
    """

    setting: str
    path: str
    kind: str  # "file" or "directory"
    sha256: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("file", "directory") or (self.kind == "file") != (
            self.sha256 is not None
        ):
            raise ValueError(
                f"local input {self.setting}: kind {self.kind!r}, sha256 {self.sha256!r}"
            )


@dataclass(frozen=True)
class FrozenRun:
    """A resolved, replayable run plan. Build it with :meth:`Session.freeze`."""

    settings: Mapping[str, Any]
    model: FrozenModel
    baseline_id: str
    baseline_fingerprint: str
    qualification: str
    overrides: tuple[str, ...]
    local_inputs: tuple[LocalInput, ...]
    environment: Mapping[str, str]
    toolchain: ToolchainIdentity
    runtime: FrozenRuntime | None
    hpx_version: str
    _intent: str = field(default="", init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "settings", _freeze(self.settings))
        object.__setattr__(self, "overrides", tuple(self.overrides))
        object.__setattr__(self, "local_inputs", tuple(self.local_inputs))
        object.__setattr__(self, "environment", _freeze(self.environment))
        object.__setattr__(self, "_intent", _intent_hash(self))

    @property
    def intent_hash(self) -> str:
        """SHA-256 of what this plan asks for; the HPX release is not part of it."""
        return self._intent

    @property
    def portable(self) -> bool:
        """Whether every input is pinned or content-identified, so another host can rebuild it.

        A compiler environment variable makes a plan host-specific, since the
        files it may name are not tracked, and so do a dependency selector
        that names a branch, tag or version rather than a commit and a custom
        heliaRT release, whose archive is not content-identified.
        """
        return (
            all(item.kind == "file" for item in self.local_inputs)
            and not any(name in _BUILD_VARIABLES for name in self.environment)
            and not _floating_selectors(self.settings)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": FROZEN_RUN_SCHEMA,
            "schema_version": FROZEN_RUN_SCHEMA_VERSION,
            "intent_hash": self.intent_hash,
            "settings": _thaw(self.settings),
            "model": asdict(self.model),
            "baseline": {"id": self.baseline_id, "fingerprint": self.baseline_fingerprint},
            "qualification": self.qualification,
            "overrides": list(self.overrides),
            "local_inputs": [asdict(item) for item in self.local_inputs],
            "environment": _thaw(self.environment),
            "toolchain": asdict(self.toolchain),
            "runtime": asdict(self.runtime) if self.runtime is not None else None,
            "hpx_version": self.hpx_version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FrozenRun:
        if not isinstance(data, Mapping):
            raise ConfigError("A frozen run plan must be an object.")
        if data.get("schema") != FROZEN_RUN_SCHEMA:
            raise ConfigError(f"Not a frozen run plan: schema {data.get('schema')!r}")
        version = data.get("schema_version")
        if type(version) is not int or version != FROZEN_RUN_SCHEMA_VERSION:
            raise ConfigError(f"Unsupported frozen run plan version: {version!r}")
        try:
            settings = data["settings"]
            if not isinstance(settings, Mapping) or not isinstance(settings.get("model"), Mapping):
                raise TypeError("settings and settings.model must be objects")
            baseline = _typed(dict, data["baseline"], "baseline", id=str, fingerprint=str)
            environment = _typed(dict, data["environment"], "environment")
            plan = cls(
                settings=settings,
                model=_record(FrozenModel, data["model"], "model"),
                baseline_id=baseline["id"],
                baseline_fingerprint=baseline["fingerprint"],
                qualification=_typed(str, data["qualification"], "qualification"),
                overrides=tuple(
                    _typed(str, item, "overrides[]")
                    for item in _typed(list, data["overrides"], "overrides")
                ),
                local_inputs=tuple(
                    _record(LocalInput, item, "local_inputs[]")
                    for item in _typed(list, data["local_inputs"], "local_inputs")
                ),
                environment=_typed(
                    dict, environment, "environment", **dict.fromkeys(environment, str)
                ),
                toolchain=_record(ToolchainIdentity, data["toolchain"], "toolchain"),
                runtime=(
                    None
                    if data["runtime"] is None
                    else _record(FrozenRuntime, data["runtime"], "runtime")
                ),
                hpx_version=_typed(str, data["hpx_version"], "hpx_version"),
            )
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise ConfigError(f"Malformed frozen run plan: {exc}") from exc
        if data.get("intent_hash") != plan.intent_hash:
            raise ConfigError(
                "Frozen run plan intent hash does not match its contents",
                hint="The file was edited after it was saved; freeze the plan again.",
            )
        return plan

    def save(self, path: str | Path) -> Path:
        """Write the plan as JSON and return its path."""
        target = Path(path).expanduser()
        text = json.dumps(self.to_dict(), indent=2, sort_keys=True, allow_nan=False) + "\n"
        try:
            write_text_atomically(target, text)
        except OSError as exc:
            raise ConfigError(f"Cannot save frozen run plan to {target}: {exc}") from exc
        return target

    @classmethod
    def load(cls, path: str | Path) -> FrozenRun:
        """Read a plan written by :meth:`save`, verifying its intent hash."""
        source = Path(path).expanduser()
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigError(f"Frozen run plan not found: {source}") from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigError(f"Cannot read frozen run plan {source}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError("A frozen run plan must be a JSON object.")
        return cls.from_dict(data)

    def config(
        self,
        *,
        model: str | Path | None = None,
        host: Mapping[str, Any] | None = None,
    ) -> ProfileConfig:
        """The :class:`ProfileConfig` this plan describes, on this host.

        *host* supplies host-local settings for the replaying host, for
        example ``{"target": {"jlink_serial": "..."}, "output": {"dir": "..."}}``;
        any other key is refused. Refuses when this HPX carries a different
        baseline, when the model (the recorded path, or *model* when it moved)
        has a different hash, when a local input differs or is missing, or
        when this host would build something else: a source-path environment
        variable set, unset or changed, another compiler version, or another
        engine runtime. Only the model can move; other local files must be
        at their recorded paths.
        """
        from .config import deep_merge, load_config
        from .deps.compatibility import load_compatibility_baseline

        baseline = load_compatibility_baseline()
        if baseline.fingerprint != self.baseline_fingerprint:
            raise ConfigError(
                f"Plan was frozen against baseline {self.baseline_id} "
                f"({self.baseline_fingerprint[:12]}…); this HPX carries "
                f"{baseline.baseline_id} ({baseline.fingerprint[:12]}…)",
                hint="Freeze the plan again with this HPX, or use the HPX release it was frozen with.",
            )
        model_path = Path(model).expanduser() if model is not None else Path(self.model.path)
        if _file_sha256(model_path) != self.model.sha256:
            raise ConfigError(
                f"Model {model_path} does not match the frozen model "
                f"(sha256 {self.model.sha256[:12]}…)",
                hint="Pass model= with the file the plan was frozen against.",
            )
        for item in self.local_inputs:
            if item.setting != _SIDECAR:
                _check_local_input(item)
        settings = _thaw(self.settings)
        _check_sidecar(self, settings, model_path)
        settings["model"] = {**settings["model"], "path": str(model_path)}
        if host:
            _check_host_overlay(host)
            settings = deep_merge(settings, _thaw(host))
        replayed = load_config(None, settings)
        _check_replay(self, freeze_config(replayed))
        return replayed

    def build(self, fixture: TypedFixture, **options: Any) -> CompiledBuild:
        """Compile this plan for *fixture*'s pinned inputs; see :func:`~helia_profiler.build`."""
        from .compiled_build import build

        return build(self, fixture, **options)


def freeze_config(config: ProfileConfig) -> FrozenRun:
    """Capture a resolved :class:`ProfileConfig` as a :class:`FrozenRun`."""
    from ._version import __version__
    from .pipeline import serialize_config

    settings = {
        key: value
        for key, value in serialize_config(config).items()
        if key not in _DERIVED_SETTINGS
    }
    for path in _HOST_LOCAL_SETTINGS:
        _drop(settings, path)
    model_path = Path(config.model.path)
    baseline = config.compatibility_baseline
    resolution = _compatibility(config)
    return FrozenRun(
        settings=settings,
        model=FrozenModel(
            path=str(config.model.path),
            sha256=_file_sha256(model_path),
            size_bytes=model_path.stat().st_size,
        ),
        baseline_id=baseline.baseline_id,
        baseline_fingerprint=baseline.fingerprint,
        qualification=resolution.qualification.value if resolution else "unknown",
        overrides=tuple(
            sorted({*resolution.module_overrides, *resolution.engine_overrides})
            if resolution
            else ()
        ),
        local_inputs=_local_inputs(config),
        environment=_environment(),
        toolchain=_toolchain_identity(config),
        runtime=_frozen_runtime(config),
        hpx_version=__version__,
    )


def _local_inputs(config: ProfileConfig) -> tuple[LocalInput, ...]:
    from .engines import EngineType

    candidates: list[tuple[str, str]] = []
    if config.engine.type is EngineType.EXECUTORCH:
        sidecar = Path(f"{config.model.path}.json")
        if sidecar.is_file():
            candidates.append((_SIDECAR, str(sidecar)))
    if config.target.segger_rtt_path is not None and _uses_rtt(config):
        candidates.append(("target.segger_rtt_path", str(config.target.segger_rtt_path)))
    if config.engine.config_path is not None:
        candidates.append(("engine.config_path", str(config.engine.config_path)))
    for key in _SOURCE_ENGINE_KEYS:
        value = config.engine.config.get(key)
        if value and not (key == "dist_path" and _heliart_source_build(config)):
            candidates.append((f"engine.config.{key}", str(value)))
    for name, override in sorted(config.build.nsx_modules.items()):
        local = getattr(override, "path", None)
        if local:
            candidates.append((f"build.nsx_modules.{name}.path", str(local)))
    for variable, value in _source_environment(config).items():
        candidates.append((f"env.{variable}", value))
    return tuple(_describe_local(setting, value) for setting, value in candidates)


def _source_environment(config: ProfileConfig) -> dict[str, str]:
    """The source-path environment variables this build reads, with their values.

    Follows each reader's precedence: an explicit setting beats its variable,
    and a variable counts only for the engine and kernel provider that read it.
    """
    from .deps.compatibility import (
        cmsis_nn_provider_module,
        heliart_environment,
        select_cmsis_nn_override,
    )
    from .engines import EngineType

    environment = {variable: os.environ.get(variable) or "" for variable in _SOURCE_VARIABLES}
    engine = config.engine.type
    engine_config = config.engine.config
    effective: dict[str, str] = {}
    if config.target.segger_rtt_path is None and _uses_rtt(config):
        effective["SEGGER_RTT_PATH"] = environment["SEGGER_RTT_PATH"]
    if engine is not EngineType.LITERT:
        provider = cmsis_nn_provider_module(engine.value, _effective_backend(config))
        selected = select_cmsis_nn_override(engine_config, provider_module=provider)
        if selected is not None and selected.selector == "env.CMSIS_NN_PATH":
            effective["CMSIS_NN_PATH"] = environment["CMSIS_NN_PATH"]
    effective.update(heliart_environment(engine.value, engine_config))
    return {variable: value for variable, value in effective.items() if value}


def _environment() -> dict[str, str]:
    environment = {variable: os.environ.get(variable) for variable in _ENVIRONMENT_VARIABLES}
    return {variable: value for variable, value in environment.items() if value}


def _heliart_source_build(config: ProfileConfig) -> bool:
    """Whether heliaRT builds from a source tree, which wins over any distribution."""
    from .engines import EngineType

    return config.engine.type is EngineType.HELIA_RT and bool(
        config.engine.config.get("source_path")
        or "HELIART_SOURCE_PATH" in _source_environment(config)
    )


def _uses_rtt(config: ProfileConfig) -> bool:
    from .vocab import Transport

    return config.target.transport == Transport.RTT


def _compatibility(config: ProfileConfig) -> CompatibilityResolution | None:
    """The compatibility resolution against the kernel provider the build uses.

    An ExecuTorch sidecar can select the provider after ``ProfileConfig``
    resolved compatibility from ``engine.backend`` alone.
    """
    backend = _effective_backend(config)
    if config.compatibility is None or backend == config.engine.backend:
        return config.compatibility
    from .deps.compatibility import resolve_compatibility

    return resolve_compatibility(
        config.compatibility_baseline,
        module_overrides=config.build.nsx_modules,
        engine_config=config.engine.config,
        engine_config_path=config.engine.config_path,
        engine_type=config.engine.type.value,
        engine_backend=backend,
        allow_unsupported_version=config.engine.allow_unsupported_version,
    )


def _effective_backend(config: ProfileConfig) -> str | None:
    """``engine.backend``, or for ExecuTorch the provider its PTE sidecar selects."""
    from .engines import EngineType

    if config.engine.type is not EngineType.EXECUTORCH or config.engine.backend:
        return config.engine.backend
    from .engines.executorch import _load_pte_sidecar

    sidecar = _load_pte_sidecar(Path(config.model.path)) or {}
    return sidecar.get("kernel_provider") or "arm"


def _describe_local(setting: str, value: str) -> LocalInput:
    path = Path(value).expanduser().resolve()
    if path.is_file():
        return LocalInput(setting, str(path), "file", _file_sha256(path))
    if path.is_dir():
        return LocalInput(setting, str(path), "directory")
    raise ConfigError(
        f"{setting} {value} does not exist",
        hint="Create it or remove the setting, then freeze the plan again.",
    )


def _check_local_input(item: LocalInput) -> None:
    path = Path(item.path).expanduser()
    if item.kind == "file":
        if not path.is_file() or _file_sha256(path) != item.sha256:
            raise ConfigError(
                f"{item.setting} {item.path} is missing or differs from the frozen file",
                hint="Restore the file the plan was frozen with, or freeze the plan again.",
            )
    elif item.kind == "directory" and not path.is_dir():
        raise ConfigError(f"{item.setting} directory {item.path} does not exist on this host")


def _check_sidecar(plan: FrozenRun, settings: Mapping[str, Any], model_path: Path) -> None:
    """Check the ExecuTorch sidecar beside the replayed model before anything reads it."""
    sidecar = Path(f"{model_path}.json")
    recorded = next((item for item in plan.local_inputs if item.setting == _SIDECAR), None)
    if recorded is not None:
        _check_local_input(LocalInput(_SIDECAR, str(sidecar), "file", recorded.sha256))
    elif settings["engine"].get("type") == "executorch" and sidecar.exists():
        raise ConfigError(
            f"{_SIDECAR} {sidecar} was not there when the plan was frozen",
            hint="Remove it, or freeze the plan again with it.",
        )


def _check_replay(plan: FrozenRun, replayed: FrozenRun) -> None:
    """Refuse a replay whose resolved run differs from the plan."""
    if replayed.intent_hash == plan.intent_hash:
        return
    drift = [
        f"{name}: frozen {frozen!r}, this host {now!r}"
        for name, frozen, now in (
            ("local inputs", _input_keys(plan), _input_keys(replayed)),
            ("toolchain", _toolchain_fact(plan.toolchain), _toolchain_fact(replayed.toolchain)),
            ("engine runtime", plan.runtime, replayed.runtime),
            ("qualification", plan.qualification, replayed.qualification),
            ("overrides", plan.overrides, replayed.overrides),
            ("environment", dict(plan.environment), dict(replayed.environment)),
        )
        if frozen != now
    ] or ["resolved settings differ"]
    raise ConfigError(
        "This host would build a different run than the frozen plan: " + "; ".join(drift),
        hint="Restore the frozen environment and toolchain, or freeze the plan again on this host.",
    )


def _floating_selectors(settings: Mapping[str, Any]) -> list[str]:
    """Dependency selectors that name something other than a commit."""
    engine = settings.get("engine", {}).get("config") or {}
    floating = []
    if engine.get("cmsis_nn_ref") and not _COMMIT.fullmatch(str(engine["cmsis_nn_ref"])):
        floating.append("engine.config.cmsis_nn_ref")
    # A heliaRT release mapping resolves to an archive that is not content-identified.
    if engine.get("source"):
        floating.append("engine.config.source")
    for name, module in sorted((settings.get("build", {}).get("nsx_modules") or {}).items()):
        selector = module.get("ref") or module.get("version")
        if selector and not _COMMIT.fullmatch(str(selector)):
            floating.append(f"build.nsx_modules.{name}")
    return floating


def _toolchain_fact(toolchain: ToolchainIdentity) -> str | None:
    if toolchain.version is None and toolchain.unavailable_reason:
        return f"unavailable ({toolchain.unavailable_reason})"
    return toolchain.version


def _input_keys(plan: FrozenRun) -> list[tuple[str, str, str]]:
    return [(item.setting, item.kind, item.sha256 or item.path) for item in plan.local_inputs]


def _check_host_overlay(host: Mapping[str, Any], prefix: tuple[str, ...] = ()) -> None:
    for key, value in host.items():
        path = (*prefix, key)
        if path in _HOST_LOCAL_SETTINGS:
            continue
        if isinstance(value, Mapping) and any(
            local[: len(path)] == path for local in _HOST_LOCAL_SETTINGS
        ):
            _check_host_overlay(value, path)
            continue
        raise ConfigError(
            f"{'.'.join(path)} is not a host-local setting",
            hint="Only host-local settings can be supplied when replaying a frozen plan.",
        )


def _toolchain_identity(config: ProfileConfig) -> ToolchainIdentity:
    from .hostenv._proc import tool_output
    from .hostenv.toolchain_probe import _compiler_command

    name = str(config.target.toolchain)
    try:
        command = _compiler_command(name)
    except Exception as exc:  # an unknown toolchain is a recorded unknown, not a crash
        return ToolchainIdentity(name, None, f"no compiler for {name}: {exc}")
    output = tool_output([command, "--version"], timeout_s=config.timeouts.toolchain_probe_s)
    if output is None:
        return ToolchainIdentity(name, None, f"{command} --version could not run")
    lines = [
        line.strip()
        for line in output.splitlines()
        if _VERSION_LINE.search(line) and not _LOCATION_LINE.match(line.strip())
    ]
    if not lines:
        return ToolchainIdentity(name, None, f"{command} --version reported no version")
    return ToolchainIdentity(name, _VERSION_SEPARATOR.join(lines))


def _frozen_runtime(config: ProfileConfig) -> FrozenRuntime | None:
    from .engines import EngineType
    from .engines.cmsis_nn import ARM_CMSIS_NN_MODULE, CMSIS_NN_MODULE
    from .engines.helia_rt.artifacts import HELIART_MODULE
    from .engines.litert import TFLITE_MICRO_MODULE
    from .runtime_records import runtime

    baseline = config.compatibility_baseline
    resolution = _compatibility(config)
    overridden = set(resolution.module_overrides) if resolution else set()

    def kernels(module: str | None) -> tuple[str | None, str | None]:
        if module is None or module in overridden:
            return module, None
        return module, baseline.module(module).ref

    def pinned(module: str) -> str | None:
        return None if module in overridden else baseline.module(module).ref

    engine = config.engine.type
    backend = config.engine.backend
    if engine is EngineType.LITERT:
        # LiteRT's modules take no build.nsx_modules override or CMSIS-NN selector.
        module = None if (backend or "reference") == "reference" else ARM_CMSIS_NN_MODULE
        return FrozenRuntime(
            "litert",
            None,
            baseline.module(TFLITE_MICRO_MODULE).ref,
            module,
            baseline.module(module).ref if module else None,
        )
    if engine is EngineType.HELIA_RT:
        # A prebuilt heliaRT archive carries its own CMSIS-NN; only a source build links the module.
        prebuilt = bool(
            config.engine.config.get("dist_path")
            or config.engine.config.get("source")
            or "HELIART_DIST_PATH" in _source_environment(config)
        )
        source_build = _heliart_source_build(config)
        module, commit = (None, None) if prebuilt and not source_build else kernels(CMSIS_NN_MODULE)
        selected = [
            f"engine.config.{key}"
            for key in (("source_path",) if source_build else ("source", "dist_path"))
            if config.engine.config.get(key)
        ] + [
            f"env.{variable}"
            for variable in _source_environment(config)
            if variable.startswith("HELIART_")
        ]
        if selected:
            return FrozenRuntime(
                "helia-rt",
                None,
                None,
                module,
                commit,
                f"heliaRT selected by {', '.join(selected)}",
            )
        record = runtime("helia-rt")
        return FrozenRuntime(
            "helia-rt",
            record.version if record else None,
            pinned(HELIART_MODULE),
            module,
            commit,
        )
    if engine is EngineType.HELIA_AOT:
        from .results.dependencies import installed_aot_source

        source = installed_aot_source()
        module, commit = kernels(CMSIS_NN_MODULE)
        if source is None:
            return FrozenRuntime(
                "helia-aot", None, None, module, commit, "helia-aot is not installed"
            )
        return FrozenRuntime("helia-aot", source["version"], source["commit"], module, commit)
    if engine is EngineType.EXECUTORCH:
        record = runtime("executorch")
        module, commit = kernels(
            CMSIS_NN_MODULE if _effective_backend(config) == "ns" else ARM_CMSIS_NN_MODULE
        )
        return FrozenRuntime(
            "executorch",
            record.version if record else None,
            record.source.commit if record else None,
            module,
            commit,
        )
    return None


def _intent_hash(plan: FrozenRun) -> str:
    settings = _thaw(plan.settings)
    _drop(settings, ("model", "path"))
    # Local inputs are identified by content (files) or by their recorded path.
    for item in plan.local_inputs:
        if not item.setting.startswith("env.") and item.setting != _SIDECAR:
            _drop(settings, _setting_path(item.setting))
    payload = {
        "settings": settings,
        "model_sha256": plan.model.sha256,
        "baseline_fingerprint": plan.baseline_fingerprint,
        "qualification": plan.qualification,
        "overrides": list(plan.overrides),
        "local_inputs": [
            [item.setting, item.kind, item.sha256 or item.path] for item in plan.local_inputs
        ],
        "toolchain": [plan.toolchain.name, plan.toolchain.version],
        "environment": _thaw(plan.environment),
        "runtime": asdict(plan.runtime) if plan.runtime is not None else None,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _setting_path(setting: str) -> tuple[str, ...]:
    module_prefix = "build.nsx_modules."
    if setting.startswith(module_prefix):
        return ("build", "nsx_modules", setting[len(module_prefix) : -len(".path")], "path")
    return tuple(setting.split("."))


def _record(cls: type[_T], data: Any, where: str) -> _T:
    """Build a plan record from JSON, refusing fields of the wrong type."""
    hints = get_type_hints(cls)
    return cls(**_typed(dict, data, where, **{name: hints[name] for name in data if name in hints}))


def _typed(expected: Any, value: Any, where: str, **fields: Any) -> Any:
    """Return *value* when its JSON type is exactly *expected*; check *fields* of an object."""
    options = get_args(expected) if isinstance(expected, UnionType) else (expected,)
    if not any(type(value) is option for option in options):
        raise TypeError(f"{where} must be {getattr(expected, '__name__', expected)}")
    for name, field_type in fields.items():
        if name in value:
            _typed(field_type, value[name], f"{where}.{name}")
    return value


def _drop(settings: dict[str, Any], path: tuple[str, ...]) -> None:
    parent: Any = settings
    for key in path[:-1]:
        parent = parent.get(key) if isinstance(parent, dict) else None
    if isinstance(parent, dict):
        parent.pop(path[-1], None)


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from exc


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_thaw(item) for item in value]
    return value
