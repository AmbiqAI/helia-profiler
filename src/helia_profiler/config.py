"""ProfileConfig — immutable configuration resolved from CLI + YAML."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .engines import EngineType
from .placement import ModelLocation
from .power.base import PowerMode


class Toolchain(StrEnum):
    """Supported cross-compiler toolchains for the profiler firmware.

    ``GCC`` and ``ARM_NONE_EABI_GCC`` are aliases — both resolve to the
    GNU Arm Embedded toolchain.  ``ARMCLANG`` is Arm Compiler 6 (Keil),
    ``ATFE`` is the Arm Toolchain for Embedded (LLVM).
    """

    ARM_NONE_EABI_GCC = "arm-none-eabi-gcc"
    GCC = "gcc"
    ARMCLANG = "armclang"
    ATFE = "atfe"


class Transport(StrEnum):
    """Host↔target transport for capture and heartbeat traffic."""

    RTT = "rtt"
    USB_CDC = "usb_cdc"
    SWO = "swo"


class OutputFormat(StrEnum):
    """Top-level report format emitted by the report stage."""

    CSV = "csv"
    JSON = "json"
    MODEL_EXPLORER = "model-explorer"


DEFAULT_BOARD = "apollo510_evb"
DEFAULT_TOOLCHAIN = Toolchain.ARM_NONE_EABI_GCC
DEFAULT_ITERATIONS = 100
DEFAULT_WARMUP = 5
DEFAULT_PMU_PRESETS = ("basic_cpu",)
DEFAULT_POWER_DURATION_S = 30
DEFAULT_IO_VOLTAGE = 1.8
DEFAULT_POWER_DRIVER = "joulescope"
DEFAULT_POWER_MODE = PowerMode.EXTERNAL
DEFAULT_SYNC_GPIO_PIN = 10  # EVB-friendly default
DEFAULT_TRANSPORT = Transport.RTT

# Heartbeat defaults — firmware emits progress lines so the host can detect
# a truly hung run without needing large wall-clock timeouts.  Setting either
# *every_n_ops* or *every_ms* to 0 disables that trigger; setting both to 0
# disables intra-inference heartbeats entirely (phase heartbeats still fire).
DEFAULT_HB_EVERY_N_OPS = 8
DEFAULT_HB_EVERY_MS = 2000
DEFAULT_HB_HOST_TIMEOUT_S = 30
DEFAULT_OVERALL_TIMEOUT_S: int | None = None  # None = unbounded when heartbeats on

# Subprocess / network timeouts — consolidated so users can override any one
# of them from config without touching source.  Values match the legacy
# module-level constants they replaced.
DEFAULT_CONFIGURE_TIMEOUT_S = 120
DEFAULT_BUILD_TIMEOUT_S = 300
DEFAULT_FLASH_TIMEOUT_S = 120
DEFAULT_TOOLCHAIN_PROBE_S = 5
DEFAULT_BINARY_PROBE_S = 10
DEFAULT_DOWNLOAD_API_S = 30
DEFAULT_DOWNLOAD_ASSET_S = 300


@dataclass(frozen=True)
class ModelConfig:
    """Model file and arena sizing.

    ``model_location`` is the high-level policy for where weights and the
    tensor arena live.

    Runtime-specific split overrides live under ``engine.config`` as
    ``runtime_arena_location`` / ``runtime_weights_location`` so they are
    scoped to interpreters that share the runtime path (currently ``tflm``
    and ``helia-rt``). ``helia-aot`` uses its own placement controls.

    Policy values:

    * ``auto`` *(default)* — plan-memory stage picks the fastest region(s)
      that fit. Greedy fastest-fit with arena prioritized over weights when
      the two compete for the same region. Order: TCM → SRAM → MRAM.
    * ``tcm`` — force both arena and weights into DTCM (highest performance,
      smallest capacity). Fails preflight if the SoC has no TCM or it
      doesn't fit.
    * ``sram`` — force both into shared SRAM.
    * ``mram`` — weights stay in MRAM/Flash (rodata); arena goes to TCM
      when available, else SRAM. Matches pre-auto-placement behavior.
    * ``psram`` — weights uploaded to external PSRAM at runtime via J-Link;
      arena in SRAM. Requires a PSRAM-capable board.
    """

    path: Path
    arena_size: int | None = None  # bytes; None = let engine/firmware report
    model_location: ModelLocation = ModelLocation.AUTO

    def __post_init__(self) -> None:
        # Tolerate invalid raw strings here — :class:`PreflightStage`
        # produces a friendlier ``ConfigError`` for unknown values.
        if not isinstance(self.model_location, ModelLocation):
            try:
                object.__setattr__(
                    self, "model_location", ModelLocation(self.model_location)
                )
            except ValueError:
                pass


@dataclass(frozen=True)
class EngineConfig:
    """Inference engine selection and passthrough config."""

    type: EngineType
    backend: str | None = None  # engine-specific (e.g. helia-rt backend)
    config: dict[str, Any] = field(default_factory=dict)
    config_path: Path | None = None  # path to engine-specific YAML


@dataclass(frozen=True)
class HeartbeatConfig:
    """Liveness / progress-reporting settings.

    The firmware emits ``HPX_HEARTBEAT`` lines at configurable intervals so
    the host can (a) detect a hung run without using a large wall-clock
    timeout, and (b) show the user live progress.

    Attributes
    ----------
    enabled:
        Master switch.  When ``False``, no heartbeats are emitted or
        expected and the host falls back to the legacy line-gap timeout.
    every_n_ops:
        Emit a heartbeat after this many profiled ops.  ``0`` disables this
        trigger.  Lower values add more PMU/inter-op overhead but give
        finer-grained progress.
    every_ms:
        Emit a heartbeat when at least this many wall-clock milliseconds
        have elapsed since the last heartbeat.  ``0`` disables this
        trigger.  Useful for engines with a single large invocation (e.g.
        AOT or upcoming Ethos-U command streams) where ``every_n_ops`` does
        not fire.
    host_timeout_s:
        Maximum time the host will wait without receiving *any* line from
        the firmware before declaring the run hung.
    overall_timeout_s:
        Hard ceiling on total capture time, in seconds.  ``None`` means
        unbounded (rely on heartbeats).  Set to a positive int for a safety
        net in CI or unattended runs.
    """

    enabled: bool = True
    every_n_ops: int = DEFAULT_HB_EVERY_N_OPS
    every_ms: int = DEFAULT_HB_EVERY_MS
    host_timeout_s: int = DEFAULT_HB_HOST_TIMEOUT_S
    overall_timeout_s: int | None = DEFAULT_OVERALL_TIMEOUT_S


@dataclass(frozen=True)
class TimeoutsConfig:
    """Subprocess and network timeouts (seconds).

    Every subprocess and long-lived HTTP call in heliaPROFILER reads its
    timeout from this struct instead of hard-coding it.  Override any value
    in YAML under ``timeouts:`` to adapt to slow CI machines, laggy J-Link
    probes, or poor network conditions.

    Capture-time timeouts (heartbeat / overall) live on ``HeartbeatConfig``
    because they are tied to the on-device progress protocol.
    """

    configure_s: int = DEFAULT_CONFIGURE_TIMEOUT_S
    build_s: int = DEFAULT_BUILD_TIMEOUT_S
    flash_s: int = DEFAULT_FLASH_TIMEOUT_S
    toolchain_probe_s: int = DEFAULT_TOOLCHAIN_PROBE_S
    binary_probe_s: int = DEFAULT_BINARY_PROBE_S
    download_api_s: int = DEFAULT_DOWNLOAD_API_S
    download_asset_s: int = DEFAULT_DOWNLOAD_ASSET_S


@dataclass(frozen=True)
class TargetConfig:
    """Hardware target."""

    board: str = DEFAULT_BOARD
    toolchain: Toolchain = DEFAULT_TOOLCHAIN
    jlink_serial: str | None = None  # select J-Link by S/N (None = auto)
    transport: Transport = DEFAULT_TRANSPORT
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    # When True (default), scan for a Joulescope at the start of `hpx profile`
    # and enable current passthrough so the board powers on before flashing.
    # No-op when no Joulescope is detected.  Set to False to skip the scan
    # entirely (e.g. board is on a bench supply).
    ensure_board_powered: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.toolchain, Toolchain):
            object.__setattr__(self, "toolchain", Toolchain(self.toolchain))
        if not isinstance(self.transport, Transport):
            object.__setattr__(self, "transport", Transport(self.transport))


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
    # Extreme benchmarking mode: power down memory regions the model does not
    # use to lower the energy floor.  Currently powers down SSRAM (3 MB) and
    # collapses MRAM to a single bank (NVM0 only).  Only safe when the model
    # weights and arena both live in TCM. Code keeps running from MRAM, so
    # transports (RTT/USB/SWO) and printf remain available throughout the run.
    extreme_mode: bool = False


@dataclass(frozen=True)
class PowerConfig:
    """Power measurement settings."""

    enabled: bool = False
    driver: str = DEFAULT_POWER_DRIVER
    mode: PowerMode = DEFAULT_POWER_MODE
    duration_s: int = DEFAULT_POWER_DURATION_S
    io_voltage: float = DEFAULT_IO_VOLTAGE
    sync_gpio_pin: int = DEFAULT_SYNC_GPIO_PIN  # GPIO for external sync
    # Optional Joulescope serial number (e.g. "004204") to disambiguate
    # when more than one device is plugged in. Leave None to auto-pick the
    # single available device (and fail loudly if multiple are present).
    serial: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, PowerMode):
            object.__setattr__(self, "mode", PowerMode(self.mode))


@dataclass(frozen=True)
class OutputConfig:
    """Report output settings."""

    format: OutputFormat = OutputFormat.CSV
    dir: Path = Path("./results")
    model_explorer: bool = True  # always emit ME overlay alongside primary format
    detailed: bool = False  # emit per-preset/group CSVs and memory breakdown

    def __post_init__(self) -> None:
        if not isinstance(self.format, OutputFormat):
            object.__setattr__(self, "format", OutputFormat(self.format))


@dataclass(frozen=True)
class ProfileConfig:
    """Top-level immutable configuration for a profiling run."""

    model: ModelConfig
    engine: EngineConfig
    target: TargetConfig = field(default_factory=TargetConfig)
    profiling: ProfilingConfig = field(default_factory=ProfilingConfig)
    power: PowerConfig = field(default_factory=PowerConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    timeouts: TimeoutsConfig = field(default_factory=TimeoutsConfig)
    frozen: bool = False
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
    timeouts_d = d.get("timeouts", {}) or {}

    model = ModelConfig(
        path=Path(model_d["path"]),
        arena_size=model_d.get("arena_size"),
        model_location=model_d.get("model_location", "auto"),
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

    tc_raw = target_d.get("toolchain", DEFAULT_TOOLCHAIN)
    try:
        tc = Toolchain(tc_raw)
    except ValueError:
        supported = ", ".join(t.value for t in Toolchain)
        raise ValueError(f"Unknown toolchain '{tc_raw}'. Supported: {supported}") from None

    return ProfileConfig(
        model=model,
        engine=engine,
        target=TargetConfig(
            board=target_d.get("board", DEFAULT_BOARD),
            toolchain=tc,
            jlink_serial=target_d.get("jlink_serial"),
            transport=target_d.get("transport", DEFAULT_TRANSPORT),
            heartbeat=_build_heartbeat(target_d.get("heartbeat")),
            ensure_board_powered=bool(target_d.get("ensure_board_powered", True)),
        ),
        profiling=ProfilingConfig(
            pmu_presets=pmu_presets,
            pmu_counters=pmu_counters,
            per_layer=profiling_d.get("per_layer", True),
            iterations=profiling_d.get("iterations", DEFAULT_ITERATIONS),
            warmup=profiling_d.get("warmup", DEFAULT_WARMUP),
            extreme_mode=bool(profiling_d.get("extreme_mode", False)),
        ),
        power=PowerConfig(
            enabled=power_d.get("enabled", False),
            driver=power_d.get("driver", DEFAULT_POWER_DRIVER),
            mode=power_d.get("mode", DEFAULT_POWER_MODE),
            duration_s=power_d.get("duration_s", DEFAULT_POWER_DURATION_S),
            io_voltage=power_d.get("io_voltage", DEFAULT_IO_VOLTAGE),
            sync_gpio_pin=power_d.get("sync_gpio_pin", DEFAULT_SYNC_GPIO_PIN),
            serial=power_d.get("serial"),
        ),
        output=OutputConfig(
            format=output_d.get("format", "csv"),
            dir=Path(output_d.get("dir", "./results")),
            model_explorer=output_d.get("model_explorer", True),
            detailed=output_d.get("detailed", False),
        ),
        timeouts=TimeoutsConfig(
            configure_s=int(timeouts_d.get("configure_s", DEFAULT_CONFIGURE_TIMEOUT_S)),
            build_s=int(timeouts_d.get("build_s", DEFAULT_BUILD_TIMEOUT_S)),
            flash_s=int(timeouts_d.get("flash_s", DEFAULT_FLASH_TIMEOUT_S)),
            toolchain_probe_s=int(timeouts_d.get("toolchain_probe_s", DEFAULT_TOOLCHAIN_PROBE_S)),
            binary_probe_s=int(timeouts_d.get("binary_probe_s", DEFAULT_BINARY_PROBE_S)),
            download_api_s=int(timeouts_d.get("download_api_s", DEFAULT_DOWNLOAD_API_S)),
            download_asset_s=int(timeouts_d.get("download_asset_s", DEFAULT_DOWNLOAD_ASSET_S)),
        ),
        frozen=bool(d.get("frozen", False)),
        work_dir=Path(d["work_dir"]) if d.get("work_dir") else None,
        keep_work_dir=d.get("keep_work_dir", False),
        verbose=d.get("verbose", 0),
    )


def _build_heartbeat(raw: Any) -> HeartbeatConfig:
    """Build a ``HeartbeatConfig`` from YAML/CLI dict (or ``None``)."""
    if raw is None:
        return HeartbeatConfig()
    if not isinstance(raw, dict):
        return HeartbeatConfig()
    overall = raw.get("overall_timeout_s", DEFAULT_OVERALL_TIMEOUT_S)
    if overall is not None:
        overall = int(overall)
    return HeartbeatConfig(
        enabled=bool(raw.get("enabled", True)),
        every_n_ops=int(raw.get("every_n_ops", DEFAULT_HB_EVERY_N_OPS)),
        every_ms=int(raw.get("every_ms", DEFAULT_HB_EVERY_MS)),
        host_timeout_s=int(raw.get("host_timeout_s", DEFAULT_HB_HOST_TIMEOUT_S)),
        overall_timeout_s=overall,
    )
