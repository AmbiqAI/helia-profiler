"""Immutable interactive session for notebooks, IPython, and scripts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Self

from .config import ProfileConfig, load_config
from .errors import ConfigError
from .results import ProfileResult

if TYPE_CHECKING:
    from .compare import CompareResult
    from .evaluation import ComparisonProfile
    from .counters import PmuCounter
    from .doctor import DoctorResult
    from .engines import EngineType
    from .model_analysis import ModelAnalysis
    from .platform import BoardDef
    from .target.probe.jlink import JLinkProbe, JLinkProbeMatch
    from .transport.ports import SerialPortInfo
    from rich.console import Console


def _merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge mappings; scalar and sequence values replace."""
    merged = {key: _thaw(value) for key, value in base.items()}
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = _merge(current, value)
        else:
            merged[key] = _thaw(value)
    return merged


def _copy_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _copy_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_copy_value(item) for item in value)
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, list):
        return [_thaw(item) for item in value]
    return value


def _without_none(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


@dataclass(frozen=True)
class Session:
    """Immutable, branchable configuration for interactive HPX operations.

    Sessions retain unresolved configuration overrides so YAML values and
    board-derived defaults are resolved by the same validation path as the
    CLI. Every ``with_*`` method returns an independent session.
    """

    yaml_path: Path | None = None
    _base: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), repr=False, hash=False
    )
    _overrides: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), repr=False, hash=False
    )

    def __post_init__(self) -> None:
        if self.yaml_path is not None:
            object.__setattr__(self, "yaml_path", Path(self.yaml_path))
        object.__setattr__(self, "_base", _freeze(_copy_value(self._base)))
        object.__setattr__(self, "_overrides", _freeze(_copy_value(self._overrides)))

    @classmethod
    def from_yaml(cls, path: str | Path) -> Self:
        """Create a session from an immutable snapshot of an HPX YAML config."""
        import yaml

        yaml_path = Path(path).expanduser().resolve()
        try:
            data = yaml.safe_load(yaml_path.read_text()) or {}
        except FileNotFoundError as exc:
            raise ConfigError(
                f"Config file not found: {yaml_path}",
                hint="Check the config path before creating the session.",
            ) from exc
        except yaml.YAMLError as exc:
            raise ConfigError(
                f"Malformed YAML in config file {yaml_path}: {exc}",
                hint="Check the file for YAML syntax errors.",
            ) from exc
        if not isinstance(data, Mapping):
            raise ConfigError(
                f"Config file {yaml_path} must contain a YAML mapping, got {type(data).__name__}.",
            )
        return cls(_base=data)

    def with_overrides(self, overrides: Mapping[str, Any]) -> Self:
        """Return a session with advanced raw configuration overrides merged in."""
        return replace(self, _overrides=_merge(self._overrides, overrides))

    def with_model(self, path: str | Path, **options: Any) -> Self:
        return self._with_section("model", {"path": path, **options})

    def with_engine(self, engine: Any, **options: Any) -> Self:
        return self._with_section("engine", {"type": engine, **options})

    def with_target(self, **options: Any) -> Self:
        return self._with_section("target", options)

    def with_profiling(self, **options: Any) -> Self:
        return self._with_section("profiling", options)

    def with_power(self, **options: Any) -> Self:
        return self._with_section("power", options)

    def with_output(self, **options: Any) -> Self:
        return self._with_section("output", options)

    def with_build(self, **options: Any) -> Self:
        return self._with_section("build", options)

    def with_timeouts(self, **options: Any) -> Self:
        return self._with_section("timeouts", options)

    def with_options(
        self,
        *,
        verbose: int | None = None,
        frozen: bool | None = None,
        work_dir: str | Path | None = None,
        clean: bool | None = None,
    ) -> Self:
        """Return a session with top-level run options."""
        return self.with_overrides(
            _without_none(
                {
                    "verbose": verbose,
                    "frozen": frozen,
                    "work_dir": work_dir,
                    "clean": clean,
                }
            )
        )

    def resolve(self, model: str | Path | None = None) -> ProfileConfig:
        """Resolve and validate this session as a complete profile config."""
        overrides = _merge(self._base, self._overrides)
        if model is not None:
            overrides = _merge(overrides, {"model": {"path": model}})
        return load_config(None, overrides)

    def profile(self, model: str | Path | None = None) -> ProfileResult:
        """Run a profile using this session's resolved configuration."""
        from .api import profile

        return profile(self.resolve(model))

    def analyze(self, model: str | Path | None = None) -> ModelAnalysis:
        """Analyze the configured model without building or flashing firmware."""
        from .model_analysis import analyze_for_engine

        config = self.resolve(model)
        return analyze_for_engine(
            config.model.path,
            engine=config.engine.type,
            board=config.target.board,
        )

    def compare(
        self,
        baseline: str | Path | ProfileResult,
        candidate: str | Path | ProfileResult,
        *,
        output_dir: str | Path | None = None,
        profile: str | Path | ComparisonProfile | None = None,
    ) -> CompareResult:
        """Compare two completed profiles and optionally write diff artifacts."""
        from .compare import compare_runs, write_compare_artifacts

        resolved_profile = profile
        if isinstance(profile, (str, Path)):
            from .evaluation import ComparisonProfile

            resolved_profile = ComparisonProfile.load(profile)
        baseline_dir = _result_directory(baseline)
        candidate_dir = _result_directory(candidate)
        if resolved_profile is None:
            result = compare_runs(baseline_dir, candidate_dir)
        else:
            result = compare_runs(baseline_dir, candidate_dir, profile=resolved_profile)
        if output_dir is not None:
            write_compare_artifacts(result, Path(output_dir))
        return result

    def doctor(self) -> DoctorResult:
        """Return structured host dependency checks."""
        from .config import Transport
        from .doctor import inspect_environment

        config = self._utility_config()
        return inspect_environment(
            toolchain=config.target.toolchain,
            transport=config.target.transport,
            engine=config.engine.type,
            require_segger_rtt=config.target.transport is Transport.RTT,
            segger_rtt_path=config.target.segger_rtt_path,
        )

    def show(self, value: Any, *, console: Console | None = None) -> Any:
        """Pretty-print a typed interactive value and return it unchanged."""
        from .presentation import show

        return show(value, console=console)

    def boards(self) -> tuple[BoardDef, ...]:
        """Return boards visible to this session's platform registry."""
        from .platform import list_boards

        config = self._utility_config()
        return tuple(list_boards(registry=config.platform_registry))

    def engines(self) -> tuple[EngineType, ...]:
        """Return supported inference engine identifiers."""
        from .engines import EngineType

        return tuple(EngineType)

    def counter_groups(self) -> tuple[str, ...]:
        """Return registered PMU counter group names."""
        from .counters import list_groups

        return tuple(list_groups())

    def counters(self, group: str | None = None) -> tuple[PmuCounter, ...]:
        """Return registered PMU counters, optionally filtered by group."""
        from .counters import list_counters

        return tuple(list_counters(group))

    def probes(self) -> tuple[JLinkProbe, ...]:
        """Return connected J-Link probes."""
        from .target.probe.jlink import list_connected_probes

        return tuple(list_connected_probes())

    def inspect_probes(self, board: str | None = None) -> tuple[JLinkProbeMatch, ...]:
        """Inspect the target core visible through each connected probe."""
        from .platform import get_soc_for_board
        from .target.probe.jlink import inspect_probe_target

        config = self._utility_config(board=board)
        soc = get_soc_for_board(config.target.board, registry=config.platform_registry)
        return tuple(
            inspect_probe_target(probe, device=soc.jlink_device)
            for probe in self.probes()
        )

    def match_probe(
        self,
        board: str | None = None,
        *,
        serial: str | None = None,
    ) -> str:
        """Resolve the J-Link serial matching a board target."""
        from .platform import get_soc_for_board
        from .target.probe.jlink import resolve_probe_serial

        config = self._utility_config(board=board, serial=serial)
        soc = get_soc_for_board(config.target.board, registry=config.platform_registry)
        return resolve_probe_serial(
            device=soc.jlink_device,
            expected_core=soc.core,
            requested_serial=config.target.jlink_serial,
        )

    def ports(self, *, include_all: bool = False) -> tuple[SerialPortInfo, ...]:
        """Return host serial ports relevant to HPX transports."""
        from .transport.ports import list_serial_ports

        return list_serial_ports(include_all=include_all)

    def reset(
        self,
        board: str | None = None,
        *,
        serial: str | None = None,
        kind: Literal["debug", "swpoi"] = "debug",
    ) -> None:
        """Reset the configured target through its J-Link probe."""
        from .platform import get_soc_for_board
        from .target.probe.jlink import reset_target, reset_target_poi

        if kind not in ("debug", "swpoi"):
            raise ConfigError("reset kind must be 'debug' or 'swpoi'")
        config = self._utility_config(board=board, serial=serial)
        soc = get_soc_for_board(config.target.board, registry=config.platform_registry)
        reset = reset_target_poi if kind == "swpoi" else reset_target
        reset(device=soc.jlink_device, jlink_serial=config.target.jlink_serial)

    def _utility_config(
        self,
        *,
        board: str | None = None,
        serial: str | None = None,
    ) -> ProfileConfig:
        overrides: dict[str, Any] = {"model": {"path": "__hpx_session__.tflite"}}
        target = _without_none({"board": board, "jlink_serial": serial})
        if target:
            overrides["target"] = target
        return load_config(None, _merge(_merge(self._base, self._overrides), overrides))

    def _with_section(self, name: str, values: Mapping[str, Any]) -> Self:
        return self.with_overrides({name: values})


def _result_directory(value: str | Path | ProfileResult) -> Path:
    if not isinstance(value, ProfileResult):
        return Path(value)
    for path in value.report_paths:
        if path.name == "summary.json":
            return path.parent
    if value.report_paths:
        parent = value.report_paths[0].parent
        return parent.parent if parent.name in {"detailed", "model_explorer"} else parent

    from .errors import ReportError

    raise ReportError(
        "ProfileResult has no report paths to compare.",
        hint="Run the profile with report generation enabled or pass its result directory.",
    )
