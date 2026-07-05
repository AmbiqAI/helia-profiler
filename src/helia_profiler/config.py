"""ProfileConfig — immutable configuration resolved from CLI + YAML."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .engines import EngineType
from .errors import ConfigError
from .placement import ModelLocation, Placement
from .platform import (
    DEFAULT_GO_GPIO_PIN,
    DEFAULT_STATE_GPIO_PIN,
    DEFAULT_SYNC_GPIO_PIN,
    BoardDef,
    ClockDomain,
    ClockSpeed,
    CoreArch,
    MemoryLayout,
    PerfTier,
    PlatformRegistry,
    PmuTier,
    SocDef,
    SocFamily,
    build_platform_registry,
    get_board,
    get_default_go_gpio_pin,
    get_default_state_gpio_pin,
    get_default_sync_gpio_pin,
    get_soc,
)
from .target.lifecycle import ResetStrategy
from .power.base import PowerMode


# Shared default used when the user leaves model.arena_size unset.
# Keep plan-memory and firmware generation aligned so auto placement
# matches the section attributes emitted into the generated app.
DEFAULT_ARENA_SIZE_BYTES = 256 * 1024


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
    UART = "uart"


@dataclass(frozen=True)
class ClockSelection:
    """Per-domain clock speed selection for the generated firmware.

    Each field names a speed within the SoC's matching clock domain using
    Ambiq datasheet terminology (e.g. ``cpu="hp"``).  ``None`` selects that
    domain's default speed.  Values are validated against the resolved SoC in
    stage 1, so unknown names raise a clear ConfigError rather than failing
    silently.
    """

    cpu: str | None = None


class OutputFormat(StrEnum):
    """Top-level report format emitted by the report stage."""

    CSV = "csv"
    JSON = "json"
    MODEL_EXPLORER = "model-explorer"


DEFAULT_BOARD = "apollo510_evb"
DEFAULT_TOOLCHAIN = Toolchain.ARM_NONE_EABI_GCC
DEFAULT_ITERATIONS = 100
DEFAULT_WARMUP = 5
# Clean / GPIO-gated end-to-end window sizing.  ``"fixed"`` reuses
# ``iterations`` for the clean pass (historical behaviour).  ``"auto"`` (default)
# lets the firmware size the gated window at runtime from the measured
# clean-inference time so short models run more iterations and big models run
# fewer — filling a consistent ~1 s wall-clock window for ordinary profiling.
# External power capture raises this to a longer minimum window so host-side
# GPIO polling and Joulescope packet alignment have several seconds to settle.
WINDOW_MODES = ("fixed", "auto")
DEFAULT_WINDOW_MODE = "auto"
DEFAULT_WINDOW_TARGET_MS = 1000
DEFAULT_POWER_WINDOW_TARGET_MS = 5000
DEFAULT_WINDOW_MIN = 10
DEFAULT_WINDOW_MAX = 2000
CLEAN_WINDOW_PROBES = ("infer", "busy_loop")
DEFAULT_CLEAN_WINDOW_PROBE = "infer"
# Per-iteration aggregation estimator for per-layer counters.  ``median`` is
# the default because it rejects the occasional corrupted iteration (e.g. an
# Apollo4 DWT->CYCCNT uint32 wrap or a frozen-zero read while the host probe is
# still settling) that a plain mean would smear across the whole layer.
DEFAULT_AGGREGATION = "median"
AGGREGATION_METHODS = ("mean", "median", "trimmed")
DEFAULT_PMU_PRESETS = ("basic_cpu",)
DEFAULT_POWER_DURATION_S = 30
DEFAULT_IO_VOLTAGE = 1.8
DEFAULT_POWER_DRIVER = "joulescope"
DEFAULT_POWER_MODE = PowerMode.EXTERNAL
DEFAULT_POWER_SYNC_INPUT_INDEX = 0
# 3-wire lock-step sync: extra host-side digital channels. gate=INPUT0,
# state/error=INPUT1, go (host->device) = OUTPUT0. Lock-step is off by default
# so existing 1-wire gate captures are unchanged.
DEFAULT_POWER_STATE_INPUT_INDEX = 1
DEFAULT_POWER_GO_OUTPUT_INDEX = 0
#: Host-side statistics rate (Hz) for GPIO-gated Joulescope capture. The device
#: integrates charge/energy at its full native rate (~2 MSPS) and delivers the
#: integrals as stat packets at this cadence; ~1 kHz brackets a ~250 ms window
#: to <1% while keeping the data volume at a few KB (vs MB/s for raw streaming).
DEFAULT_POWER_STATS_RATE_HZ = 1000
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

    ``arena_location`` and ``weights_location`` are the preferred placement
    controls for runtime engines such as heliaRT: the arena is the mutable
    tensor arena, while weights are the model flatbuffer/constant data.

    ``model_location`` is retained as a compatibility preset for older configs.
    Split fields take precedence when present. ``helia-aot`` uses its own
    tensor-kind placement controls via ``engine.config.aot_args.memory.tensors``.

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
    arena_location: Placement | None = None
    weights_location: Placement | None = None

    def __post_init__(self) -> None:
        # Tolerate invalid raw strings here — :class:`PreflightStage`
        # produces a friendlier ``ConfigError`` for unknown values.
        if not isinstance(self.model_location, ModelLocation):
            try:
                object.__setattr__(self, "model_location", ModelLocation(self.model_location))
            except ValueError:
                pass
        for field_name in ("arena_location", "weights_location"):
            raw = getattr(self, field_name)
            if raw is not None and not isinstance(raw, Placement):
                try:
                    object.__setattr__(self, field_name, Placement(raw))
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
        AOT command streams) where ``every_n_ops`` does
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
    usb_port: str | None = None
    rtt_buffer_size_up: int | None = None
    clock: ClockSelection = field(default_factory=ClockSelection)
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    # When True, scan for a Joulescope at the start of `hpx profile` and
    # enable current passthrough so the board powers on before flashing.
    # Default is False (opt-in): most boards are powered independently of
    # any Joulescope, and probing for one on every run is unnecessary I/O
    # that isn't worth the risk on every invocation. Set to True (or pass
    # --ensure-power) when the board's power genuinely comes from the
    # Joulescope rail. Always runs when power.enabled is True, since power
    # capture requires the driver regardless.
    ensure_board_powered: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.toolchain, Toolchain):
            object.__setattr__(self, "toolchain", Toolchain(self.toolchain))
        if not isinstance(self.transport, Transport):
            object.__setattr__(self, "transport", Transport(self.transport))
        if not isinstance(self.clock, ClockSelection):
            object.__setattr__(self, "clock", ClockSelection(**self.clock))


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
    # Clean / GPIO-gated end-to-end window sizing.  ``"fixed"`` (default) runs
    # exactly ``iterations`` clean inferences.  ``"auto"`` lets the firmware
    # choose the clean-window iteration count at runtime to fill
    # ``window_target_ms`` of wall-time, clamped to ``[window_min, window_max]``.
    # The firmware reports the actual count it ran (``HPX_CLEAN_INFER_COUNT``),
    # which the host divides into the gated energy for per-inference numbers.
    window_mode: str = DEFAULT_WINDOW_MODE
    window_target_ms: int = DEFAULT_WINDOW_TARGET_MS
    window_min: int = DEFAULT_WINDOW_MIN
    window_max: int = DEFAULT_WINDOW_MAX
    # Optional bench probe for the clean GPIO-gated window. ``busy_loop`` keeps
    # the gate high around a calibrated CPU spin for roughly window_target_ms
    # so bring-up can distinguish wrong gate semantics from inference behavior.
    clean_window_probe: str = DEFAULT_CLEAN_WINDOW_PROBE
    # Sanity-check diagnostic: when true, the firmware emits an
    # ``HPX_CLEAN_ITER=<n>`` line over the active transport on every iteration
    # of the clean (GPIO-gated power) window.  This proves the device is
    # genuinely looping inferences for the whole measured window rather than
    # stalling/sleeping.  It perturbs the power reading (extra transport
    # traffic inside the gate), so leave it OFF for real measurements.
    clean_window_trace: bool = False
    # Static-power diagnostic: when true, the firmware unconditionally powers
    # and retains the full shared SSRAM array at boot (mirroring AutoDeploy's
    # ns_power_config(bNeedSharedSRAM=true)), even when the model runs entirely
    # from TCM.  Used to measure the SSRAM static/retention contribution to
    # the power floor.  SRAM-resident arena/weights power the array on
    # regardless; this flag forces it on for the TCM case too.
    force_shared_sram: bool = False
    # How per-layer counters are aggregated across profiled iterations:
    # ``mean`` (arithmetic mean), ``median`` (robust default), or ``trimmed``
    # (drop the high/low extremes, then mean).  All methods first reject
    # structurally-invalid samples (uint32-wrap / frozen-zero) and log them.
    aggregation: str = DEFAULT_AGGREGATION
    # Extreme benchmarking mode: power down memory regions the model does not
    # use to lower the energy floor.  Currently powers down SSRAM (3 MB) and
    # collapses MRAM to a single bank (NVM0 only).  Only safe when the model
    # weights and arena both live in TCM. Code keeps running from MRAM, so
    # transports (RTT/USB/SWO) and printf remain available throughout the run.
    extreme_mode: bool = False

    def __post_init__(self) -> None:
        if self.aggregation not in AGGREGATION_METHODS:
            raise ValueError(
                f"Invalid aggregation '{self.aggregation}'. "
                f"Choose one of: {', '.join(AGGREGATION_METHODS)}."
            )
        if self.window_mode not in WINDOW_MODES:
            raise ValueError(
                f"Invalid window_mode '{self.window_mode}'. "
                f"Choose one of: {', '.join(WINDOW_MODES)}."
            )
        if self.window_min < 1:
            raise ValueError(f"window_min must be >= 1, got {self.window_min}.")
        if self.window_max < self.window_min:
            raise ValueError(
                f"window_max ({self.window_max}) must be >= window_min ({self.window_min})."
            )
        if self.window_target_ms < 1:
            raise ValueError(f"window_target_ms must be >= 1, got {self.window_target_ms}.")
        if self.clean_window_probe not in CLEAN_WINDOW_PROBES:
            raise ValueError(
                f"Invalid clean_window_probe '{self.clean_window_probe}'. "
                f"Choose one of: {', '.join(CLEAN_WINDOW_PROBES)}."
            )


@dataclass(frozen=True)
class PowerConfig:
    """Power measurement settings."""

    enabled: bool = False
    driver: str = DEFAULT_POWER_DRIVER
    mode: PowerMode = DEFAULT_POWER_MODE
    duration_s: int = DEFAULT_POWER_DURATION_S
    io_voltage: float = DEFAULT_IO_VOLTAGE
    sync_gpio_pin: int = DEFAULT_SYNC_GPIO_PIN  # GPIO for external sync
    # Host-side sync input index on external instruments. For Joulescope this
    # is the digital input channel number (validated default wiring is INPUT0).
    sync_input_index: int = DEFAULT_POWER_SYNC_INPUT_INDEX
    # Optional 3-wire lock-step handshake (AutoDeploy-compatible wiring).
    # gate=sync_gpio_pin (device->host), state_gpio_pin (device->host),
    # go_gpio_pin (host->device). 0 disables a wire; lockstep stays off until
    # the monitor exposes a GO output and both extra pins are configured.
    # ``None`` means "not explicitly set": callers resolve the effective value
    # via ``target.lifecycle.resolve_power_lockstep``, which auto-enables
    # lock-step when the board is wired for it and the SoC family's default
    # power reset policy needs it to stay race-free (e.g. Apollo5's
    # debug_reset+swpoi_reset combo -- see the AP510 combo+RTT gate-race
    # investigation). An explicit ``true``/``false`` here always wins.
    lockstep: bool | None = None
    state_gpio_pin: int = DEFAULT_STATE_GPIO_PIN
    go_gpio_pin: int = DEFAULT_GO_GPIO_PIN
    state_input_index: int = DEFAULT_POWER_STATE_INPUT_INDEX
    go_output_index: int = DEFAULT_POWER_GO_OUTPUT_INDEX
    # Host-side statistics rate (Hz) for gated Joulescope capture. Controls the
    # cadence of on-device-integrated charge/energy stat packets used to bracket
    # the gated window. Higher = finer edge resolution, more (still tiny) packets.
    stats_rate_hz: int = DEFAULT_POWER_STATS_RATE_HZ
    # Reset strategy before power capture. "auto" keeps board/SoC defaults;
    # explicit strategies are for bring-up experiments and custom boards.
    reset_strategy: ResetStrategy = ResetStrategy.AUTO
    # Optional Joulescope serial number (e.g. "004204") to disambiguate
    # when more than one device is plugged in. Leave None to auto-pick the
    # single available device (and fail loudly if multiple are present).
    serial: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, PowerMode):
            object.__setattr__(self, "mode", PowerMode(self.mode))
        if not isinstance(self.reset_strategy, ResetStrategy):
            object.__setattr__(self, "reset_strategy", ResetStrategy(self.reset_strategy))
        if self.sync_input_index < 0:
            raise ValueError(f"power.sync_input_index must be >= 0, got {self.sync_input_index}.")
        if self.stats_rate_hz < 1:
            raise ValueError(f"power.stats_rate_hz must be >= 1, got {self.stats_rate_hz}.")
        if self.lockstep and (self.state_gpio_pin <= 0 or self.go_gpio_pin <= 0):
            raise ValueError("power.lockstep requires both state_gpio_pin and go_gpio_pin > 0.")


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
class NsxModuleOverride:
    """Override resolution for a single NSX module.

    Exactly one mode must be set:
    * *path* — use a local directory as the module source (``local: true``).
    * *ref* — resolve the module's project at a specific git ref/tag.
    * *version* — pin the module to an exact version constraint.
    """

    path: Path | None = None
    ref: str | None = None
    version: str | None = None

    def __post_init__(self) -> None:
        modes = sum(x is not None for x in (self.path, self.ref, self.version))
        if modes == 0:
            raise ConfigError("NsxModuleOverride requires exactly one of path, ref, or version")
        if modes > 1:
            raise ConfigError(
                f"NsxModuleOverride accepts only one of path, ref, or version (got {modes})",
                hint="Remove the extra keys so only one override mode is set.",
            )


@dataclass(frozen=True)
class BuildConfig:
    """NSX build-system overrides.

    Controls how the generated firmware's NSX manifest resolves modules.
    Default behaviour keeps the selected board's default NSX channel, but
    generated manifests explicitly track ``main`` for the ``neuralspotx`` and
    ``nsx-ambiq-sdk`` projects unless the user overrides those modules.

    Advanced users can pin individual modules to a version, point them at
    a local checkout, or select a custom git ref — useful for SoC/board
    bring-up before changes land in the stable channel.

    ``compiler_launcher`` selects a CMake compiler launcher (e.g. ``sccache``
    or ``ccache``) that wraps every compile to cache object output and speed
    up repeated builds.  ``"auto"`` (the default) uses ``sccache`` then
    ``ccache`` if either is on ``PATH`` and otherwise does nothing — so the
    mere presence of the binary is the opt-in.  ``"none"`` disables it; an
    explicit tool name or path requires that the launcher be found.
    """

    channel: str | None = None
    nsx_modules: dict[str, NsxModuleOverride] = field(default_factory=dict)
    compiler_launcher: str = "auto"


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
    build: BuildConfig = field(default_factory=BuildConfig)
    platform_registry: PlatformRegistry = field(default_factory=build_platform_registry)
    frozen: bool = False
    work_dir: Path | None = None  # None = use persistent cache dir
    keep_work_dir: bool = False  # legacy — cache dir is always kept
    clean: bool = False  # wipe cached work dir before building
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
    build_d = d.get("build", {}) or {}
    platform_registry = _build_platform_registry(target_d)

    model = ModelConfig(
        path=Path(model_d["path"]),
        arena_size=model_d.get("arena_size"),
        model_location=model_d.get("model_location", "auto"),
        arena_location=model_d.get("arena_location"),
        weights_location=model_d.get("weights_location"),
    )

    engine_type_raw = engine_d.get("type", EngineType.HELIA_RT.value)
    if engine_type_raw == EngineType.TFLM.value:
        raise ConfigError(
            "engine.type='tflm' is temporarily unavailable",
            hint="Use engine.type='helia-rt' for the interpreter runtime.",
        )
    try:
        engine_type = EngineType(engine_type_raw)
    except ValueError as exc:
        supported = ", ".join(
            engine.value for engine in (EngineType.HELIA_RT, EngineType.HELIA_AOT)
        )
        raise ConfigError(
            f"Invalid engine.type: {engine_type_raw!r}. Supported: {supported}"
        ) from exc
    engine = EngineConfig(
        type=engine_type,
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

    board_name = target_d.get("board", DEFAULT_BOARD)
    sync_gpio_pin = power_d.get(
        "sync_gpio_pin",
        get_default_sync_gpio_pin(
            board_name,
            fallback=DEFAULT_SYNC_GPIO_PIN,
            registry=platform_registry,
        ),
    )
    state_gpio_pin = power_d.get(
        "state_gpio_pin",
        get_default_state_gpio_pin(
            board_name,
            fallback=DEFAULT_STATE_GPIO_PIN,
            registry=platform_registry,
        ),
    )
    go_gpio_pin = power_d.get(
        "go_gpio_pin",
        get_default_go_gpio_pin(
            board_name,
            fallback=DEFAULT_GO_GPIO_PIN,
            registry=platform_registry,
        ),
    )

    return ProfileConfig(
        model=model,
        engine=engine,
        target=TargetConfig(
            board=board_name,
            toolchain=tc,
            jlink_serial=target_d.get("jlink_serial"),
            transport=target_d.get("transport", DEFAULT_TRANSPORT),
            usb_port=target_d.get("usb_port"),
            rtt_buffer_size_up=target_d.get("rtt_buffer_size_up"),
            clock=ClockSelection(**(target_d.get("clock") or {})),
            heartbeat=_build_heartbeat(target_d.get("heartbeat")),
            ensure_board_powered=bool(target_d.get("ensure_board_powered", False)),
        ),
        profiling=ProfilingConfig(
            pmu_presets=pmu_presets,
            pmu_counters=pmu_counters,
            per_layer=profiling_d.get("per_layer", True),
            iterations=profiling_d.get("iterations", DEFAULT_ITERATIONS),
            warmup=profiling_d.get("warmup", DEFAULT_WARMUP),
            aggregation=profiling_d.get("aggregation", DEFAULT_AGGREGATION),
            window_mode=profiling_d.get("window_mode", DEFAULT_WINDOW_MODE),
            window_target_ms=profiling_d.get("window_target_ms", DEFAULT_WINDOW_TARGET_MS),
            window_min=profiling_d.get("window_min", DEFAULT_WINDOW_MIN),
            window_max=profiling_d.get("window_max", DEFAULT_WINDOW_MAX),
            clean_window_probe=profiling_d.get("clean_window_probe", DEFAULT_CLEAN_WINDOW_PROBE),
            clean_window_trace=bool(profiling_d.get("clean_window_trace", False)),
            force_shared_sram=bool(profiling_d.get("force_shared_sram", False)),
            extreme_mode=bool(profiling_d.get("extreme_mode", False)),
        ),
        power=PowerConfig(
            enabled=power_d.get("enabled", False),
            driver=power_d.get("driver", DEFAULT_POWER_DRIVER),
            mode=power_d.get("mode", DEFAULT_POWER_MODE),
            duration_s=power_d.get("duration_s", DEFAULT_POWER_DURATION_S),
            io_voltage=power_d.get("io_voltage", DEFAULT_IO_VOLTAGE),
            sync_gpio_pin=sync_gpio_pin,
            sync_input_index=power_d.get("sync_input_index", DEFAULT_POWER_SYNC_INPUT_INDEX),
            lockstep=(bool(power_d["lockstep"]) if "lockstep" in power_d else None),
            state_gpio_pin=state_gpio_pin,
            go_gpio_pin=go_gpio_pin,
            state_input_index=power_d.get("state_input_index", DEFAULT_POWER_STATE_INPUT_INDEX),
            go_output_index=power_d.get("go_output_index", DEFAULT_POWER_GO_OUTPUT_INDEX),
            stats_rate_hz=power_d.get("stats_rate_hz", DEFAULT_POWER_STATS_RATE_HZ),
            reset_strategy=power_d.get("reset_strategy", ResetStrategy.AUTO.value),
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
        build=_build_build_config(build_d),
        platform_registry=platform_registry,
        frozen=bool(d.get("frozen", False)),
        work_dir=Path(d["work_dir"]) if d.get("work_dir") else None,
        keep_work_dir=d.get("keep_work_dir", False),
        clean=bool(d.get("clean", False)),
        verbose=d.get("verbose", 0),
    )


def _build_platform_registry(target_d: dict[str, Any]) -> PlatformRegistry:
    base = build_platform_registry()
    custom_socs = _build_custom_socs(target_d.get("custom_socs"), base)
    registry_with_socs = build_platform_registry(base=base, socs=custom_socs)
    custom_boards = _build_custom_boards(target_d.get("custom_boards"), registry_with_socs)
    return build_platform_registry(base=registry_with_socs, boards=custom_boards)


def _build_custom_socs(raw: Any, base: PlatformRegistry) -> dict[str, SocDef]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError("target.custom_socs must be a mapping of name -> definition")

    custom: dict[str, SocDef] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            raise ConfigError(f"target.custom_socs.{name} must be a mapping")
        overlay = build_platform_registry(base=base, socs=custom)
        based_on = spec.get("based_on")
        base_soc = get_soc(based_on, registry=overlay) if based_on else None
        family = _enum_value(
            SocFamily,
            spec.get("family", base_soc.family if base_soc else None),
            field_name=f"target.custom_socs.{name}.family",
        )
        core = _enum_value(
            CoreArch,
            spec.get("core", base_soc.core if base_soc else None),
            field_name=f"target.custom_socs.{name}.core",
        )
        pmu_tier = _enum_value(
            PmuTier,
            spec.get("pmu_tier", base_soc.pmu_tier if base_soc else None),
            field_name=f"target.custom_socs.{name}.pmu_tier",
        )
        has_mve = spec.get("has_mve", base_soc.has_mve if base_soc else None)
        if has_mve is None:
            raise ConfigError(f"target.custom_socs.{name}.has_mve is required")
        c_define = spec.get("c_define", base_soc.c_define if base_soc else None)
        cmsis_header = spec.get("cmsis_header", base_soc.cmsis_header if base_soc else None)
        if c_define is None:
            raise ConfigError(f"target.custom_socs.{name}.c_define is required")
        if cmsis_header is None:
            raise ConfigError(f"target.custom_socs.{name}.cmsis_header is required")
        custom[name] = SocDef(
            name=name,
            family=family,
            core=core,
            pmu_tier=pmu_tier,
            has_mve=bool(has_mve),
            memory=_build_memory_layout(
                spec.get("memory"),
                field_name=f"target.custom_socs.{name}.memory",
                base=base_soc.memory if base_soc else None,
            ),
            clocks=_build_clock_domains(
                spec.get("clocks"),
                field_name=f"target.custom_socs.{name}.clocks",
                base=base_soc.clocks if base_soc else None,
            ),
            c_define=str(c_define),
            cmsis_header=str(cmsis_header),
            rtt_scan_ranges=_build_rtt_scan_ranges(
                spec.get("rtt_scan_ranges", base_soc.rtt_scan_ranges if base_soc else None),
                field_name=f"target.custom_socs.{name}.rtt_scan_ranges",
            ),
            jlink_device=str(spec.get("jlink_device", base_soc.jlink_device if base_soc else "")),
            pmu_max_ops=int(spec.get("pmu_max_ops", base_soc.pmu_max_ops if base_soc else 2048)),
        )
    return custom


def _build_custom_boards(raw: Any, registry: PlatformRegistry) -> dict[str, BoardDef]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError("target.custom_boards must be a mapping of name -> definition")

    custom: dict[str, BoardDef] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            raise ConfigError(f"target.custom_boards.{name} must be a mapping")
        overlay = build_platform_registry(base=registry, boards=custom)
        based_on = spec.get("based_on")
        base_board = get_board(based_on, registry=overlay) if based_on else None
        soc = spec.get("soc", base_board.soc if base_board else None)
        channel = spec.get("channel", base_board.channel if base_board else None)
        if soc is None:
            raise ConfigError(f"target.custom_boards.{name}.soc is required")
        if channel is None:
            raise ConfigError(f"target.custom_boards.{name}.channel is required")
        starter_profile_board = spec.get(
            "starter_profile_board",
            base_board.profile_source_board if base_board else None,
        )
        custom[name] = BoardDef(
            name=name,
            soc=str(soc),
            channel=str(channel),
            psram_kb=_optional_int(
                spec.get("psram_kb", base_board.psram_kb if base_board else None)
            ),
            default_sync_gpio_pin=int(
                spec.get(
                    "default_sync_gpio_pin",
                    base_board.default_sync_gpio_pin if base_board else DEFAULT_SYNC_GPIO_PIN,
                )
            ),
            default_state_gpio_pin=int(
                spec.get(
                    "default_state_gpio_pin",
                    base_board.default_state_gpio_pin if base_board else DEFAULT_STATE_GPIO_PIN,
                )
            ),
            default_go_gpio_pin=int(
                spec.get(
                    "default_go_gpio_pin",
                    base_board.default_go_gpio_pin if base_board else DEFAULT_GO_GPIO_PIN,
                )
            ),
            starter_profile_board=(
                str(starter_profile_board) if starter_profile_board is not None else None
            ),
            description=str(spec.get("description", base_board.description if base_board else "")),
        )
    return custom


def _enum_value(enum_cls: type, raw: Any, *, field_name: str):
    if isinstance(raw, enum_cls):
        return raw
    if raw is None:
        raise ConfigError(f"{field_name} is required")
    try:
        return enum_cls(raw)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_cls)
        raise ConfigError(f"Invalid {field_name}: {raw!r}. Supported: {allowed}") from exc


def _build_memory_layout(raw: Any, *, field_name: str, base: MemoryLayout | None) -> MemoryLayout:
    if raw is None:
        if base is None:
            raise ConfigError(f"{field_name} is required")
        return base
    if not isinstance(raw, dict):
        raise ConfigError(f"{field_name} must be a mapping")
    values = {
        "mram_kb": base.mram_kb if base else 0,
        "sram_kb": base.sram_kb if base else 0,
        "dtcm_kb": base.dtcm_kb if base else 0,
        "itcm_kb": base.itcm_kb if base else 0,
        "psram_kb": base.psram_kb if base else 0,
        "nvm_kb": base.nvm_kb if base else 0,
    }
    for key in values:
        if key in raw:
            values[key] = int(raw[key])
    return MemoryLayout(**values)


def _build_clock_domains(
    raw: Any,
    *,
    field_name: str,
    base: tuple[ClockDomain, ...] | None,
) -> tuple[ClockDomain, ...]:
    if raw is None:
        if base is None:
            raise ConfigError(f"{field_name} is required")
        return base
    if not isinstance(raw, list):
        raise ConfigError(f"{field_name} must be a list")
    domains: list[ClockDomain] = []
    for index, domain in enumerate(raw):
        if not isinstance(domain, dict):
            raise ConfigError(f"{field_name}[{index}] must be a mapping")
        speeds_raw = domain.get("speeds")
        if not isinstance(speeds_raw, list) or not speeds_raw:
            raise ConfigError(f"{field_name}[{index}].speeds must be a non-empty list")
        speeds: list[ClockSpeed] = []
        for speed_index, speed in enumerate(speeds_raw):
            if not isinstance(speed, dict):
                raise ConfigError(f"{field_name}[{index}].speeds[{speed_index}] must be a mapping")
            perf_tier = speed.get("perf_tier")
            speeds.append(
                ClockSpeed(
                    name=str(speed["name"]),
                    mhz=int(speed["mhz"]),
                    perf_tier=(
                        _enum_value(
                            PerfTier,
                            perf_tier,
                            field_name=(f"{field_name}[{index}].speeds[{speed_index}].perf_tier"),
                        )
                        if perf_tier is not None
                        else None
                    ),
                )
            )
        domains.append(
            ClockDomain(
                name=str(domain["name"]),
                speeds=tuple(speeds),
                default=str(domain["default"]),
            )
        )
    return tuple(domains)


def _build_rtt_scan_ranges(raw: Any, *, field_name: str) -> tuple[tuple[int, int], ...]:
    if raw is None:
        raise ConfigError(f"{field_name} is required")
    if not isinstance(raw, (list, tuple)):
        raise ConfigError(f"{field_name} must be a list of [base, length] pairs")
    ranges: list[tuple[int, int]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ConfigError(f"{field_name}[{index}] must be a [base, length] pair")
        ranges.append((int(item[0]), int(item[1])))
    if not ranges:
        raise ConfigError(f"{field_name} must not be empty")
    return tuple(ranges)


def _optional_int(raw: Any) -> int | None:
    if raw is None:
        return None
    return int(raw)


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


_CHANNEL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")


def _build_build_config(raw: dict[str, Any]) -> BuildConfig:
    """Build a ``BuildConfig`` from YAML/CLI dict."""
    if not raw:
        return BuildConfig()
    channel = raw.get("channel")
    if channel is not None and not _CHANNEL_RE.match(channel):
        raise ConfigError(
            f"Invalid build.channel value: {channel!r}",
            hint="Channel must be an identifier (letters, digits, hyphens, underscores).",
        )
    nsx_modules_raw = raw.get("nsx_modules", {})
    nsx_modules: dict[str, NsxModuleOverride] = {}
    if isinstance(nsx_modules_raw, dict):
        for name, spec in nsx_modules_raw.items():
            if not isinstance(spec, dict):
                raise ConfigError(
                    f"build.nsx_modules.{name} must be a mapping (got {type(spec).__name__})",
                    hint="Use path: /dir, ref: branch, or version: X.Y.Z",
                )
            nsx_modules[name] = NsxModuleOverride(
                path=Path(spec["path"]) if spec.get("path") else None,
                ref=spec.get("ref"),
                version=spec.get("version"),
            )
    compiler_launcher = raw.get("compiler_launcher", "auto")
    if compiler_launcher is None or compiler_launcher is False:
        compiler_launcher = "none"
    if not isinstance(compiler_launcher, str):
        raise ConfigError(
            f"build.compiler_launcher must be a string (got {type(compiler_launcher).__name__})",
            hint="Use 'auto', 'none', or a tool name/path like 'sccache' or 'ccache'.",
        )
    return BuildConfig(
        channel=channel,
        nsx_modules=nsx_modules,
        compiler_launcher=compiler_launcher,
    )
