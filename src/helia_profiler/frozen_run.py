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
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping

from .errors import ConfigError

if TYPE_CHECKING:
    from .config import ProfileConfig

FROZEN_RUN_SCHEMA = "hpx.frozen-run"
FROZEN_RUN_SCHEMA_VERSION = 1

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
)

#: Environment variables that point a build at local sources, and the engines that read them.
_SOURCE_ENVIRONMENT = {
    "SEGGER_RTT_PATH": ("tflm", "helia-rt", "helia-aot", "executorch"),
    "CMSIS_NN_PATH": ("helia-rt", "helia-aot", "executorch"),
    "HELIART_DIST_PATH": ("helia-rt",),
    "HELIART_SOURCE_PATH": ("helia-rt",),
}

#: ``engine.config`` keys that point a build at local sources.
_SOURCE_ENGINE_KEYS = ("dist_path", "source_path", "cmsis_nn_path")

_VERSION_LINE = re.compile(r"\d+\.\d+")
#: ``--version`` lines that carry install locations rather than the version.
_LOCATION_LINE = re.compile(r"^(InstalledDir|Configuration file):")


@dataclass(frozen=True)
class FrozenModel:
    """The model a plan was frozen against, identified by content."""

    path: str
    sha256: str
    size_bytes: int


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
    kind: str  # "file", "directory" or "missing"
    sha256: str | None = None


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
    toolchain: ToolchainIdentity
    runtime: FrozenRuntime | None
    hpx_version: str
    _intent: str = field(default="", init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "settings", _freeze(self.settings))
        object.__setattr__(self, "overrides", tuple(self.overrides))
        object.__setattr__(self, "local_inputs", tuple(self.local_inputs))
        object.__setattr__(self, "_intent", _intent_hash(self))

    @property
    def intent_hash(self) -> str:
        """SHA-256 of what this plan asks for; the HPX release is not part of it."""
        return self._intent

    @property
    def portable(self) -> bool:
        """Whether every input is pinned or content-identified, so another host can rebuild it."""
        return all(item.kind == "file" for item in self.local_inputs)

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
            "toolchain": asdict(self.toolchain),
            "runtime": asdict(self.runtime) if self.runtime is not None else None,
            "hpx_version": self.hpx_version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FrozenRun:
        if data.get("schema") != FROZEN_RUN_SCHEMA:
            raise ConfigError(f"Not a frozen run plan: schema {data.get('schema')!r}")
        version = data.get("schema_version")
        if type(version) is not int or version != FROZEN_RUN_SCHEMA_VERSION:
            raise ConfigError(f"Unsupported frozen run plan version: {version!r}")
        try:
            if not isinstance(data["settings"], Mapping):
                raise TypeError("settings must be an object")
            plan = cls(
                settings=data["settings"],
                model=FrozenModel(**data["model"]),
                baseline_id=data["baseline"]["id"],
                baseline_fingerprint=data["baseline"]["fingerprint"],
                qualification=data["qualification"],
                overrides=tuple(data["overrides"]),
                local_inputs=tuple(LocalInput(**item) for item in data["local_inputs"]),
                toolchain=ToolchainIdentity(**data["toolchain"]),
                runtime=FrozenRuntime(**data["runtime"]) if data["runtime"] is not None else None,
                hpx_version=data["hpx_version"],
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
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.to_dict(), indent=2, sort_keys=True, allow_nan=False) + "\n"
        with target.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
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
        has a different hash, or when a local input differs or is missing.
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
            _check_local_input(item)
        settings = _thaw(self.settings)
        settings["model"] = {**settings["model"], "path": str(model_path)}
        if host:
            _check_host_overlay(host)
            settings = deep_merge(settings, _thaw(host))
        return load_config(None, settings)


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
    resolution = config.compatibility
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
        toolchain=_toolchain_identity(config),
        runtime=_frozen_runtime(config),
        hpx_version=__version__,
    )


def _local_inputs(config: ProfileConfig) -> tuple[LocalInput, ...]:
    candidates: list[tuple[str, str]] = []
    if config.target.segger_rtt_path is not None:
        candidates.append(("target.segger_rtt_path", str(config.target.segger_rtt_path)))
    if config.engine.config_path is not None:
        candidates.append(("engine.config_path", str(config.engine.config_path)))
    for key in _SOURCE_ENGINE_KEYS:
        value = config.engine.config.get(key)
        if value:
            candidates.append((f"engine.config.{key}", str(value)))
    for name, override in sorted(config.build.nsx_modules.items()):
        local = getattr(override, "path", None)
        if local:
            candidates.append((f"build.nsx_modules.{name}.path", str(local)))
    engine = config.engine.type.value
    for variable, readers in _SOURCE_ENVIRONMENT.items():
        value = os.environ.get(variable)
        if value and engine in readers:
            candidates.append((f"env.{variable}", value))
    return tuple(_describe_local(setting, value) for setting, value in candidates)


def _describe_local(setting: str, value: str) -> LocalInput:
    path = Path(value).expanduser()
    if path.is_file():
        return LocalInput(setting, value, "file", _file_sha256(path))
    if path.is_dir():
        return LocalInput(setting, value, "directory")
    return LocalInput(setting, value, "missing")


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
    return ToolchainIdentity(name, " | ".join(lines))


def _frozen_runtime(config: ProfileConfig) -> FrozenRuntime | None:
    from .engines import EngineType
    from .engines.cmsis_nn import ARM_CMSIS_NN_MODULE, CMSIS_NN_MODULE
    from .engines.helia_rt.artifacts import HELIART_MODULE
    from .engines.tflm import TFLITE_MICRO_MODULE
    from .runtime_records import runtime

    baseline = config.compatibility_baseline
    overridden = set(config.compatibility.module_overrides) if config.compatibility else set()

    def kernels(module: str | None) -> tuple[str | None, str | None]:
        if module is None or module in overridden:
            return module, None
        return module, baseline.module(module).ref

    def pinned(module: str) -> str | None:
        return None if module in overridden else baseline.module(module).ref

    engine = config.engine.type
    backend = config.engine.backend
    if engine is EngineType.TFLM:
        module, commit = kernels(
            None if (backend or "reference") == "reference" else ARM_CMSIS_NN_MODULE
        )
        return FrozenRuntime("tflm", None, pinned(TFLITE_MICRO_MODULE), module, commit)
    if engine is EngineType.HELIA_RT:
        selected = [
            key for key in ("source", *_SOURCE_ENGINE_KEYS) if config.engine.config.get(key)
        ]
        if selected:
            return FrozenRuntime(
                "helia-rt",
                None,
                None,
                unavailable_reason=f"heliaRT selected by engine.config: {', '.join(selected)}",
            )
        record = runtime("helia-rt")
        module, commit = kernels(CMSIS_NN_MODULE)
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
        module, commit = kernels(CMSIS_NN_MODULE if backend == "ns" else ARM_CMSIS_NN_MODULE)
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
    if isinstance(settings.get("model"), dict):
        settings["model"].pop("path", None)
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
        "runtime": asdict(plan.runtime) if plan.runtime is not None else None,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


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
