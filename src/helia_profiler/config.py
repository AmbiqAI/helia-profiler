"""ProfileConfig — immutable configuration resolved from CLI + YAML."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .engines import EngineType

DEFAULT_BOARD = "apollo510_evb"
DEFAULT_TOOLCHAIN = "arm-none-eabi-gcc"
DEFAULT_ITERATIONS = 100
DEFAULT_WARMUP = 5
DEFAULT_PMU_PRESETS = ("basic_cpu",)
DEFAULT_POWER_DURATION_S = 30
DEFAULT_IO_VOLTAGE = 1.8


@dataclass(frozen=True)
class ModelConfig:
    """Model file and arena sizing."""

    path: Path
    arena_size: int | None = None  # bytes; None = let engine/firmware report


@dataclass(frozen=True)
class EngineConfig:
    """Inference engine selection and passthrough config."""

    type: EngineType
    backend: str | None = None  # engine-specific (e.g. helia-rt backend)
    config: dict[str, Any] = field(default_factory=dict)
    config_path: Path | None = None  # path to engine-specific YAML


@dataclass(frozen=True)
class TargetConfig:
    """Hardware target."""

    board: str = DEFAULT_BOARD
    toolchain: str = DEFAULT_TOOLCHAIN


@dataclass(frozen=True)
class ProfilingConfig:
    """PMU capture settings."""

    pmu_presets: tuple[str, ...] = DEFAULT_PMU_PRESETS
    per_layer: bool = True
    iterations: int = DEFAULT_ITERATIONS
    warmup: int = DEFAULT_WARMUP


@dataclass(frozen=True)
class PowerConfig:
    """Power measurement settings."""

    enabled: bool = False
    backend: str = "joulescope"
    duration_s: int = DEFAULT_POWER_DURATION_S
    io_voltage: float = DEFAULT_IO_VOLTAGE


@dataclass(frozen=True)
class OutputConfig:
    """Report output settings."""

    format: str = "csv"  # csv | json | model-explorer
    dir: Path = Path("./results")
    model_explorer: bool = True  # always emit ME overlay alongside primary format


@dataclass(frozen=True)
class ProfileConfig:
    """Top-level immutable configuration for a profiling run."""

    model: ModelConfig
    engine: EngineConfig
    target: TargetConfig = field(default_factory=TargetConfig)
    profiling: ProfilingConfig = field(default_factory=ProfilingConfig)
    power: PowerConfig = field(default_factory=PowerConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    work_dir: Path | None = None  # None = use tempdir
    keep_work_dir: bool = False
    verbose: int = 0


def load_config(yaml_path: Path | None, cli_overrides: dict[str, Any]) -> ProfileConfig:
    """Merge YAML config file with CLI overrides into a frozen ProfileConfig.

    CLI values take precedence over YAML values. Missing values fall back to
    dataclass defaults.
    """
    import yaml

    base: dict[str, Any] = {}
    if yaml_path is not None:
        with open(yaml_path) as f:
            base = yaml.safe_load(f) or {}

    # Deep merge: CLI overrides win
    merged = _deep_merge(base, cli_overrides)

    return _build_config(merged)


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, returning a new dict."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _build_config(d: dict[str, Any]) -> ProfileConfig:
    """Construct a ProfileConfig from a merged dict."""
    model_d = d.get("model", {})
    engine_d = d.get("engine", {})
    target_d = d.get("target", {})
    profiling_d = d.get("profiling", {})
    power_d = d.get("power", {})
    output_d = d.get("output", {})

    model = ModelConfig(
        path=Path(model_d["path"]),
        arena_size=model_d.get("arena_size"),
    )

    engine_type_raw = engine_d.get("type", "tflm")
    engine = EngineConfig(
        type=EngineType(engine_type_raw),
        backend=engine_d.get("backend"),
        config=engine_d.get("config", {}),
        config_path=Path(engine_d["config_path"]) if engine_d.get("config_path") else None,
    )

    pmu_presets = profiling_d.get("pmu_presets", DEFAULT_PMU_PRESETS)
    if isinstance(pmu_presets, list):
        pmu_presets = tuple(pmu_presets)

    return ProfileConfig(
        model=model,
        engine=engine,
        target=TargetConfig(
            board=target_d.get("board", DEFAULT_BOARD),
            toolchain=target_d.get("toolchain", DEFAULT_TOOLCHAIN),
        ),
        profiling=ProfilingConfig(
            pmu_presets=pmu_presets,
            per_layer=profiling_d.get("per_layer", True),
            iterations=profiling_d.get("iterations", DEFAULT_ITERATIONS),
            warmup=profiling_d.get("warmup", DEFAULT_WARMUP),
        ),
        power=PowerConfig(
            enabled=power_d.get("enabled", False),
            backend=power_d.get("backend", "joulescope"),
            duration_s=power_d.get("duration_s", DEFAULT_POWER_DURATION_S),
            io_voltage=power_d.get("io_voltage", DEFAULT_IO_VOLTAGE),
        ),
        output=OutputConfig(
            format=output_d.get("format", "csv"),
            dir=Path(output_d.get("dir", "./results")),
            model_explorer=output_d.get("model_explorer", True),
        ),
        work_dir=Path(d["work_dir"]) if d.get("work_dir") else None,
        keep_work_dir=d.get("keep_work_dir", False),
        verbose=d.get("verbose", 0),
    )
