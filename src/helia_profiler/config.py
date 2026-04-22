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
DEFAULT_POWER_DRIVER = "joulescope"
DEFAULT_POWER_MODE = "external"
DEFAULT_SYNC_GPIO_PIN = 10  # EVB-friendly default


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
    jlink_serial: str | None = None  # select J-Link by S/N (None = auto)


@dataclass(frozen=True)
class ProfilingConfig:
    """PMU capture settings.

    Counter selection is specified via *pmu_counters* — a mapping of
    compute-unit group (``cpu``, ``mve``, ``memory``) to a selection:

    * ``"default"`` — curated set of the most useful counters.
    * ``"all"``     — every counter in the group (multi-pass).
    * ``["NAME", …]`` — explicit counter names.

    The legacy *pmu_presets* field is still accepted for backward
    compatibility and is converted internally.
    """

    pmu_presets: tuple[str, ...] = DEFAULT_PMU_PRESETS
    pmu_counters: dict[str, str | list[str]] | None = None
    per_layer: bool = True
    iterations: int = DEFAULT_ITERATIONS
    warmup: int = DEFAULT_WARMUP


@dataclass(frozen=True)
class PowerConfig:
    """Power measurement settings."""

    enabled: bool = False
    driver: str = DEFAULT_POWER_DRIVER
    mode: str = DEFAULT_POWER_MODE  # "external" | "internal"
    duration_s: int = DEFAULT_POWER_DURATION_S
    io_voltage: float = DEFAULT_IO_VOLTAGE
    sync_gpio_pin: int = DEFAULT_SYNC_GPIO_PIN  # GPIO for external sync


@dataclass(frozen=True)
class OutputConfig:
    """Report output settings."""

    format: str = "csv"  # csv | json | model-explorer
    dir: Path = Path("./results")
    model_explorer: bool = True  # always emit ME overlay alongside primary format
    detailed: bool = False  # emit per-preset/group CSVs and memory breakdown


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

    pmu_counters_raw = profiling_d.get("pmu_counters")
    pmu_counters: dict[str, str | list[str]] | None = None
    if isinstance(pmu_counters_raw, dict):
        pmu_counters = {}
        for grp, sel in pmu_counters_raw.items():
            if isinstance(sel, list):
                pmu_counters[grp] = sel
            else:
                pmu_counters[grp] = str(sel)

    return ProfileConfig(
        model=model,
        engine=engine,
        target=TargetConfig(
            board=target_d.get("board", DEFAULT_BOARD),
            toolchain=target_d.get("toolchain", DEFAULT_TOOLCHAIN),
            jlink_serial=target_d.get("jlink_serial"),
        ),
        profiling=ProfilingConfig(
            pmu_presets=pmu_presets,
            pmu_counters=pmu_counters,
            per_layer=profiling_d.get("per_layer", True),
            iterations=profiling_d.get("iterations", DEFAULT_ITERATIONS),
            warmup=profiling_d.get("warmup", DEFAULT_WARMUP),
        ),
        power=PowerConfig(
            enabled=power_d.get("enabled", False),
            driver=power_d.get("driver", DEFAULT_POWER_DRIVER),
            mode=power_d.get("mode", DEFAULT_POWER_MODE),
            duration_s=power_d.get("duration_s", DEFAULT_POWER_DURATION_S),
            io_voltage=power_d.get("io_voltage", DEFAULT_IO_VOLTAGE),
            sync_gpio_pin=power_d.get("sync_gpio_pin", DEFAULT_SYNC_GPIO_PIN),
        ),
        output=OutputConfig(
            format=output_d.get("format", "csv"),
            dir=Path(output_d.get("dir", "./results")),
            model_explorer=output_d.get("model_explorer", True),
            detailed=output_d.get("detailed", False),
        ),
        work_dir=Path(d["work_dir"]) if d.get("work_dir") else None,
        keep_work_dir=d.get("keep_work_dir", False),
        verbose=d.get("verbose", 0),
    )
