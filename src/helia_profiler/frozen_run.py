"""Frozen run plans: every resolved setting, saved so another host can rebuild it.

A :class:`FrozenRun` is :meth:`Session.resolve` captured as data. It records
the resolved settings, the compatibility baseline, the model's hash, the
toolchain's identity and the runtime record the engine resolves to. It names
no device and no install path, so a saved plan replays on another host, and
:meth:`FrozenRun.config` refuses a replay whose baseline or model differs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
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

#: Settings that name this host or session rather than what is built or measured.
_HOST_LOCAL_SETTINGS = (
    ("work_dir",),
    ("verbose",),
    ("clean",),
    ("output", "dir"),
    ("target", "jlink_serial"),
    ("target", "usb_port"),
    ("target", "segger_rtt_path"),
    ("build", "compiler_launcher"),
)


@dataclass(frozen=True)
class FrozenModel:
    """The model a plan was frozen against, identified by content."""

    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ToolchainIdentity:
    """The compiler a plan expects: name and exact version, never a path.

    ``version`` is ``None`` when the compiler could not be probed at freeze
    time; ``unavailable_reason`` then says why.
    """

    name: str
    version: str | None
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class FrozenRuntime:
    """The runtime record the engine resolves to, or why there is none."""

    name: str
    version: str | None
    source_commit: str | None
    kernels_commit: str | None = None
    archive_sha256: str | None = None
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class FrozenRun:
    """A resolved, replayable run plan. Build it with :meth:`Session.freeze`."""

    settings: Mapping[str, Any]
    model: FrozenModel
    baseline_id: str
    baseline_fingerprint: str
    toolchain: ToolchainIdentity
    runtime: FrozenRuntime | None
    hpx_version: str
    _intent: str = field(default="", init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "settings", _freeze(self.settings))
        object.__setattr__(self, "_intent", _intent_hash(self))

    @property
    def intent_hash(self) -> str:
        """SHA-256 of what this plan asks for, excluding host-local settings and the HPX release."""
        return self._intent

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": FROZEN_RUN_SCHEMA,
            "schema_version": FROZEN_RUN_SCHEMA_VERSION,
            "intent_hash": self.intent_hash,
            "settings": _thaw(self.settings),
            "model": vars(self.model),
            "baseline": {"id": self.baseline_id, "fingerprint": self.baseline_fingerprint},
            "toolchain": vars(self.toolchain),
            "runtime": vars(self.runtime) if self.runtime is not None else None,
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
            plan = cls(
                settings=data["settings"],
                model=FrozenModel(**data["model"]),
                baseline_id=data["baseline"]["id"],
                baseline_fingerprint=data["baseline"]["fingerprint"],
                toolchain=ToolchainIdentity(**data["toolchain"]),
                runtime=FrozenRuntime(**data["runtime"]) if data["runtime"] is not None else None,
                hpx_version=data["hpx_version"],
            )
        except (KeyError, TypeError) as exc:
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
        target.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return target

    @classmethod
    def load(cls, path: str | Path) -> FrozenRun:
        """Read a plan written by :meth:`save`, verifying its intent hash."""
        source = Path(path).expanduser()
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigError(f"Frozen run plan not found: {source}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"Cannot read frozen run plan {source}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError("A frozen run plan must be a JSON object.")
        return cls.from_dict(data)

    def config(self, *, model: str | Path | None = None) -> ProfileConfig:
        """The :class:`ProfileConfig` this plan describes, on this host.

        Refuses when this HPX carries a different compatibility baseline, or
        when the model file (the recorded path, or *model* when the file has
        moved) no longer has the recorded hash.
        """
        from .config import load_config
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
        if _sha256(model_path) != self.model.sha256:
            raise ConfigError(
                f"Model {model_path} does not match the frozen model "
                f"(sha256 {self.model.sha256[:12]}…)",
                hint="Pass model= with the file the plan was frozen against.",
            )
        settings = _thaw(self.settings)
        settings["model"] = {**settings["model"], "path": str(model_path)}
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
    model_path = Path(config.model.path)
    baseline = config.compatibility_baseline
    return FrozenRun(
        settings=settings,
        model=FrozenModel(
            path=str(config.model.path),
            sha256=_sha256(model_path),
            size_bytes=model_path.stat().st_size,
        ),
        baseline_id=baseline.baseline_id,
        baseline_fingerprint=baseline.fingerprint,
        toolchain=_toolchain_identity(config),
        runtime=_frozen_runtime(config),
        hpx_version=__version__,
    )


def _toolchain_identity(config: ProfileConfig) -> ToolchainIdentity:
    from .hostenv.toolchain_probe import compiler_version

    name = str(config.target.toolchain)
    try:
        version = compiler_version(name, timeout_s=config.timeouts.binary_probe_s)
    except Exception as exc:  # the probe shells out; any failure is an unknown, not a crash
        return ToolchainIdentity(name, None, f"compiler probe failed: {exc}")
    if not version:
        return ToolchainIdentity(name, None, "compiler reported no version")
    return ToolchainIdentity(name, version)


def _frozen_runtime(config: ProfileConfig) -> FrozenRuntime | None:
    from .engines import EngineType
    from .runtime_records import runtime

    names = {
        EngineType.TFLM: "tflm",
        EngineType.HELIA_RT: "helia-rt",
        EngineType.HELIA_AOT: "helia-aot",
        EngineType.EXECUTORCH: "executorch",
    }
    name = names.get(config.engine.type)
    if name is None:
        return None
    overrides = config.compatibility.engine_overrides if config.compatibility else ()
    if overrides:
        return FrozenRuntime(
            name, None, None, unavailable_reason=f"engine overrides: {', '.join(overrides)}"
        )
    version = None
    if config.engine.type is EngineType.HELIA_AOT:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as installed

        try:
            version = installed("helia-aot")
        except PackageNotFoundError:
            return FrozenRuntime(name, None, None, unavailable_reason="helia-aot is not installed")
    record = runtime(name, version)
    if record is None:
        return FrozenRuntime(
            name, version, None, unavailable_reason=f"no runtime record for {name} {version}"
        )
    return FrozenRuntime(
        name=record.name,
        version=record.version,
        source_commit=record.source.commit,
        kernels_commit=record.kernels.commit if record.kernels is not None else None,
        archive_sha256=record.archive_sha256,
    )


def _intent_hash(plan: FrozenRun) -> str:
    settings = _thaw(plan.settings)
    for path in _HOST_LOCAL_SETTINGS:
        parent = settings
        for key in path[:-1]:
            parent = parent.get(key, {}) if isinstance(parent, dict) else {}
        if isinstance(parent, dict):
            parent.pop(path[-1], None)
    if isinstance(settings.get("model"), dict):
        settings["model"].pop("path", None)
    payload = {
        "settings": settings,
        "model_sha256": plan.model.sha256,
        "baseline_fingerprint": plan.baseline_fingerprint,
        "toolchain": [plan.toolchain.name, plan.toolchain.version],
        "runtime": vars(plan.runtime) if plan.runtime is not None else None,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ConfigError(f"Cannot read model {path}: {exc}") from exc


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value
