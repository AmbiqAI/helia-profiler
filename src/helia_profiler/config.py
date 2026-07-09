"""ProfileConfig — immutable configuration resolved from CLI + YAML."""

from __future__ import annotations

import logging
import re
import warnings
from dataclasses import field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any
import difflib

from pydantic import ConfigDict, TypeAdapter, ValidationError, field_validator, model_validator
from pydantic.dataclasses import dataclass as pydantic_dataclass

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
from .power.base import PowerMode
from .target.lifecycle import ResetStrategy

log = logging.getLogger("helia_profiler.config")


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


@pydantic_dataclass(frozen=True, config=ConfigDict(extra="forbid"))
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
# Which binary is on the target during power capture. "dedicated" flashes the
# transport-free hpx_profiler_power image (see firmware/__init__.py WP2)
# before capture; SWO/UART/RTT/USB traffic on the shared transport binary was
# measured to add significant current contamination into the GPIO-gated
# Joulescope window on AP510 depending on transport, so "dedicated" is the
# default. "shared" restores the pre-WP2 behavior of reusing the
# already-flashed transport binary for power capture (useful when no J-Link
# is free to reflash, or for bring-up comparisons against the contaminated
# baseline).
POWER_FIRMWARE_MODES = ("dedicated", "shared")
DEFAULT_POWER_FIRMWARE = "dedicated"
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


@pydantic_dataclass(frozen=True, config=ConfigDict(extra="forbid"))
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
    model_location: ModelLocation | str = ModelLocation.AUTO
    arena_location: Placement | str | None = None
    weights_location: Placement | str | None = None

    @field_validator("model_location", mode="before")
    @classmethod
    def _coerce_model_location(cls, value: Any) -> Any:
        if isinstance(value, ModelLocation):
            return value
        try:
            return ModelLocation(value)
        except ValueError:
            return value

    @field_validator("arena_location", "weights_location", mode="before")
    @classmethod
    def _coerce_placement(cls, value: Any) -> Any:
        if value is None or isinstance(value, Placement):
            return value
        try:
            return Placement(value)
        except ValueError:
            return value


@pydantic_dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class EngineConfig:
    """Inference engine selection and passthrough config."""

    type: EngineType = EngineType.HELIA_RT
    backend: str | None = None  # engine-specific (e.g. helia-rt backend)
    config: dict[str, Any] = field(default_factory=dict)
    config_path: Path | None = None  # path to engine-specific YAML

    @field_validator("type", mode="before")
    @classmethod
    def _coerce_type(cls, value: Any) -> Any:
        if isinstance(value, EngineType):
            return value
        try:
            return EngineType(value)
        except ValueError as exc:
            supported = ", ".join(
                engine.value for engine in (EngineType.HELIA_RT, EngineType.HELIA_AOT)
            )
            raise ValueError(f"Invalid engine.type: {value!r}. Supported: {supported}") from exc


@pydantic_dataclass(frozen=True, config=ConfigDict(extra="forbid"))
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


@pydantic_dataclass(frozen=True, config=ConfigDict(extra="forbid"))
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


@pydantic_dataclass(frozen=True, config=ConfigDict(extra="forbid"))
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
    custom_socs: dict[str, Any] | None = None
    custom_boards: dict[str, Any] | None = None
    # When True, scan for a Joulescope at the start of `hpx profile` and
    # enable current passthrough so the board powers on before flashing.
    # Default is False (opt-in): most boards are powered independently of
    # any Joulescope, and probing for one on every run is unnecessary I/O
    # that isn't worth the risk on every invocation. Set to True (or pass
    # --ensure-power) when the board's power genuinely comes from the
    # Joulescope rail. Always runs when power.enabled is True, since power
    # capture requires the driver regardless.
    ensure_board_powered: bool = False

    @field_validator("toolchain", mode="before")
    @classmethod
    def _coerce_toolchain(cls, value: Any) -> Any:
        if isinstance(value, Toolchain):
            return value
        try:
            return Toolchain(value)
        except ValueError:
            supported = ", ".join(t.value for t in Toolchain)
            raise ValueError(f"Unknown toolchain '{value}'. Supported: {supported}") from None

    @field_validator("toolchain")
    @classmethod
    def _normalize_gcc_alias(cls, value: Toolchain) -> Toolchain:
        return Toolchain.ARM_NONE_EABI_GCC if value is Toolchain.GCC else value

    @field_validator("clock", mode="before")
    @classmethod
    def _coerce_clock(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return ClockSelection(**value)
        return value


@pydantic_dataclass(frozen=True, config=ConfigDict(extra="forbid"))
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

    @field_validator("pmu_presets", mode="before")
    @classmethod
    def _coerce_pmu_presets(cls, value: Any) -> Any:
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("pmu_counters", mode="before")
    @classmethod
    def _normalize_pmu_counters(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized: dict[str, str | list[str]] = {}
        for group, selection in value.items():
            normalized[str(group)] = selection if isinstance(selection, list) else str(selection)
        return normalized

    @model_validator(mode="after")
    def _validate(self) -> ProfilingConfig:
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
        return self


@pydantic_dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class PowerConfig:
    """Power measurement settings."""

    enabled: bool = False
    driver: str = DEFAULT_POWER_DRIVER
    # "dedicated" flashes hpx_profiler_power (transport-free) before capture;
    # "shared" reuses the already-flashed transport binary. See
    # POWER_FIRMWARE_MODES above for the contamination rationale.
    firmware: str = DEFAULT_POWER_FIRMWARE
    mode: PowerMode = DEFAULT_POWER_MODE
    # ``None`` means "not explicitly set": consumers use
    # DEFAULT_POWER_DURATION_S and may auto-tune the bound from PMU-phase
    # timing.  An explicit value (YAML or --power-duration, even if equal to
    # the default) always wins and disables auto-tuning.
    duration_s: int | None = None
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

    @model_validator(mode="after")
    def _validate(self) -> PowerConfig:
        if self.sync_input_index < 0:
            raise ValueError(f"power.sync_input_index must be >= 0, got {self.sync_input_index}.")
        if self.stats_rate_hz < 1:
            raise ValueError(f"power.stats_rate_hz must be >= 1, got {self.stats_rate_hz}.")
        if self.lockstep and (self.state_gpio_pin <= 0 or self.go_gpio_pin <= 0):
            raise ValueError("power.lockstep requires both state_gpio_pin and go_gpio_pin > 0.")
        if self.firmware not in POWER_FIRMWARE_MODES:
            raise ValueError(
                f"Unknown power.firmware '{self.firmware}'. "
                f"Choose one of: {', '.join(POWER_FIRMWARE_MODES)}."
            )
        return self


@pydantic_dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class OutputConfig:
    """Report output settings."""

    format: OutputFormat = OutputFormat.CSV
    dir: Path = Path("./results")
    model_explorer: bool = True  # always emit ME overlay alongside primary format
    detailed: bool = False  # emit per-preset/group CSVs and memory breakdown

    @model_validator(mode="after")
    def _validate(self) -> OutputConfig:
        if self.format is OutputFormat.MODEL_EXPLORER:
            raise ConfigError(
                "output.format: model-explorer is not a valid primary report format",
                hint=(
                    "Use output.format: csv or json; Model Explorer overlays are "
                    "controlled separately via output.model_explorer: true."
                ),
            )
        return self


@pydantic_dataclass(frozen=True, config=ConfigDict(extra="forbid"))
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

    @model_validator(mode="after")
    def _validate(self) -> NsxModuleOverride:
        modes = sum(x is not None for x in (self.path, self.ref, self.version))
        if modes == 0:
            raise ConfigError("NsxModuleOverride requires exactly one of path, ref, or version")
        if modes > 1:
            raise ConfigError(
                f"NsxModuleOverride accepts only one of path, ref, or version (got {modes})",
                hint="Remove the extra keys so only one override mode is set.",
            )
        return self


_CHANNEL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")


@pydantic_dataclass(frozen=True, config=ConfigDict(extra="forbid"))
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

    @field_validator("channel")
    @classmethod
    def _validate_channel(cls, value: str | None) -> str | None:
        if value is not None and not _CHANNEL_RE.match(value):
            raise ConfigError(
                f"Invalid build.channel value: {value!r}",
                hint="Channel must be an identifier (letters, digits, hyphens, underscores).",
            )
        return value

    @field_validator("nsx_modules", mode="before")
    @classmethod
    def _validate_nsx_modules(cls, value: Any) -> Any:
        if value is None:
            return {}
        if not isinstance(value, dict):
            return value
        for name, spec in value.items():
            if isinstance(spec, NsxModuleOverride) or isinstance(spec, dict):
                continue
            raise ConfigError(
                f"build.nsx_modules.{name} must be a mapping (got {type(spec).__name__})",
                hint="Use path: /dir, ref: branch, or version: X.Y.Z",
            )
        return value

    @field_validator("compiler_launcher", mode="before")
    @classmethod
    def _normalize_compiler_launcher(cls, value: Any) -> Any:
        if value is None or value is False:
            return "none"
        if not isinstance(value, str):
            raise ConfigError(
                f"build.compiler_launcher must be a string (got {type(value).__name__})",
                hint="Use 'auto', 'none', or a tool name/path like 'sccache' or 'ccache'.",
            )
        return value


@pydantic_dataclass(
    frozen=True,
    config=ConfigDict(extra="forbid", arbitrary_types_allowed=True),
)
class ProfileConfig:
    """Top-level immutable configuration for a profiling run."""

    model: ModelConfig
    engine: EngineConfig = field(default_factory=lambda: EngineConfig(type=EngineType.HELIA_RT))
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


_PROFILE_CONFIG_ADAPTER = TypeAdapter(ProfileConfig)


def _build_valid_field_names() -> dict[tuple[str, ...], tuple[str, ...]]:
    """Derive the did-you-mean lookup table from the config dataclasses.

    Walks the ``ProfileConfig`` tree so suggestions can never drift from the
    real field names.  Dict-of-dataclass fields (``build.nsx_modules``) get a
    ``"*"`` wildcard segment standing in for the free-form key.
    """
    import dataclasses as _dc
    import typing as _t

    result: dict[tuple[str, ...], tuple[str, ...]] = {}

    def visit(cls: type, path: tuple[str, ...]) -> None:
        names = tuple(f.name for f in _dc.fields(cls))
        if path == ():
            names = tuple(n for n in names if n != "platform_registry")
        result[path] = names
        hints = _t.get_type_hints(cls)
        for f in _dc.fields(cls):
            if path == () and f.name == "platform_registry":
                continue  # resolved runtime object, never user-settable
            tp = hints.get(f.name)
            for cand in (tp, *_t.get_args(tp)):
                if _dc.is_dataclass(cand):
                    visit(cand, (*path, f.name))
                    break
                if _t.get_origin(cand) is dict:
                    value_args = _t.get_args(cand)
                    if len(value_args) == 2 and _dc.is_dataclass(value_args[1]):
                        visit(value_args[1], (*path, f.name, "*"))
                        break

    visit(ProfileConfig, ())
    return result


_VALID_FIELD_NAMES = _build_valid_field_names()

_GENERIC_CONFIG_HINT = "Run with --help or see the config reference."


def load_config(yaml_path: Path | None, cli_overrides: dict[str, Any]) -> ProfileConfig:
    """Merge YAML config file with CLI overrides into a frozen ProfileConfig.

    CLI values take precedence over YAML values. Missing values fall back to
    dataclass defaults.

    Raises `ConfigError` (never a raw exception) for any problem with
    the YAML file or the merged configuration values.
    """
    import yaml

    base: dict[str, Any] = {}
    if yaml_path is not None:
        try:
            with open(yaml_path) as f:
                base = yaml.safe_load(f) or {}
        except FileNotFoundError as exc:
            raise ConfigError(
                f"Config file not found: {yaml_path}",
                hint="Check the --config path, or omit --config to use CLI-only configuration.",
            ) from exc
        except yaml.YAMLError as exc:
            raise ConfigError(
                f"Malformed YAML in config file {yaml_path}: {exc}",
                hint="Check the file for YAML syntax errors (indentation, colons, quoting).",
            ) from exc

        if not isinstance(base, dict):
            raise ConfigError(
                f"Config file {yaml_path} must contain a YAML mapping (key: value pairs), "
                f"got {type(base).__name__}.",
                hint="Top-level YAML must be a mapping with keys like model, engine, target.",
            )

    merged = _deep_merge(base, cli_overrides)
    _check_reserved_user_keys(merged)
    _check_required_model_path(merged)
    _emit_deprecation_warnings(merged)
    prepared, platform_registry = _prepare_merged_config(merged)

    try:
        config = _PROFILE_CONFIG_ADAPTER.validate_python(prepared)
    except ConfigError:
        raise
    except ValidationError as exc:
        message, hint = _format_validation_error(exc)
        raise ConfigError(message, hint=hint) from exc
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    if config.engine.type is EngineType.TFLM:
        raise ConfigError(
            "engine.type='tflm' is temporarily unavailable",
            hint="Use engine.type='helia-rt' for the interpreter runtime.",
        )

    return replace(config, platform_registry=platform_registry)


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, returning a new dict."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _check_reserved_user_keys(merged: dict[str, Any]) -> None:
    if "platform_registry" in merged:
        raise ConfigError(
            "platform_registry is not a user-configurable field",
            hint="Remove platform_registry from the YAML/CLI input; it is resolved automatically.",
        )


def _check_required_model_path(merged: dict[str, Any]) -> None:
    model = merged.get("model")
    if isinstance(model, dict) and model.get("path"):
        return
    if model is None or (isinstance(model, dict) and not model.get("path")):
        raise ConfigError(
            "model.path is required",
            hint=(
                "Provide a model via the CLI positional argument, --config YAML "
                "(model: path: ...), or the model.path key."
            ),
        )


def _emit_deprecation_warnings(merged: dict[str, Any]) -> None:
    model = merged.get("model")
    if isinstance(model, dict) and "model_location" in model:
        model_location = model.get("model_location")
        if model_location not in (None, ModelLocation.AUTO, ModelLocation.AUTO.value, "auto"):
            _warn_deprecated(
                "model.model_location is deprecated; prefer model.arena_location and "
                "model.weights_location for placement control.",
                stacklevel=3,
            )

    profiling = merged.get("profiling")
    if isinstance(profiling, dict) and "pmu_presets" in profiling:
        raw = profiling.get("pmu_presets")
        normalized = tuple(raw) if isinstance(raw, list) else raw
        if normalized != DEFAULT_PMU_PRESETS:
            _warn_deprecated(
                "profiling.pmu_presets is deprecated; prefer profiling.pmu_counters.",
                stacklevel=3,
            )

    if "keep_work_dir" in merged:
        _warn_deprecated(
            "keep_work_dir is deprecated and has no effect; the cache work directory is always kept.",
            stacklevel=3,
        )


def _warn_deprecated(message: str, *, stacklevel: int) -> None:
    warnings.warn(message, DeprecationWarning, stacklevel=stacklevel)
    log.warning(message)


def _prepare_merged_config(merged: dict[str, Any]) -> tuple[dict[str, Any], PlatformRegistry]:
    prepared = dict(merged)
    raw_target = prepared.get("target")
    target = dict(raw_target) if isinstance(raw_target, dict) else raw_target
    raw_power = prepared.get("power")
    power = dict(raw_power) if isinstance(raw_power, dict) else raw_power

    if isinstance(target, dict):
        platform_registry = _build_platform_registry(target)
        board_name = target.get("board", DEFAULT_BOARD)
        prepared["target"] = target
    else:
        platform_registry = build_platform_registry()
        board_name = DEFAULT_BOARD

    if power is None:
        power = {}
    if isinstance(power, dict):
        power.setdefault(
            "sync_gpio_pin",
            get_default_sync_gpio_pin(
                board_name,
                fallback=DEFAULT_SYNC_GPIO_PIN,
                registry=platform_registry,
            ),
        )
        power.setdefault(
            "state_gpio_pin",
            get_default_state_gpio_pin(
                board_name,
                fallback=DEFAULT_STATE_GPIO_PIN,
                registry=platform_registry,
            ),
        )
        power.setdefault(
            "go_gpio_pin",
            get_default_go_gpio_pin(
                board_name,
                fallback=DEFAULT_GO_GPIO_PIN,
                registry=platform_registry,
            ),
        )
        prepared["power"] = power

    return prepared, platform_registry


def _format_validation_error(exc: ValidationError) -> tuple[str, str]:
    lines: list[str] = []
    hints: list[str] = []

    for error in exc.errors(include_url=False):
        loc = tuple(str(part) for part in error.get("loc", ()))
        path = _format_error_path(loc)
        msg = _normalize_validation_message(error.get("msg", "Invalid value"))
        suggestion = _suggest_field_name(loc, error.get("type", ""))
        if suggestion:
            msg = f"{msg}. Did you mean '{suggestion}'?"
            hints.append(f"{path}: did you mean '{suggestion}'?")
        lines.append(f"{path}: {msg}")

    hint = " ; ".join(hints) if hints else _GENERIC_CONFIG_HINT
    return "\n".join(lines), hint


def _normalize_validation_message(message: str) -> str:
    for prefix in ("Value error, ", "Assertion failed, "):
        if message.startswith(prefix):
            return message[len(prefix) :]
    return message


def _format_error_path(loc: tuple[str, ...]) -> str:
    if not loc:
        return "<root>"
    parts: list[str] = []
    for part in loc:
        if part.isdigit():
            if parts:
                parts[-1] = f"{parts[-1]}[{part}]"
            else:
                parts.append(f"[{part}]")
        else:
            parts.append(part)
    return ".".join(parts)


def _suggest_field_name(loc: tuple[str, ...], error_type: str) -> str | None:
    if error_type not in {"extra_forbidden", "unexpected_keyword_argument"} or not loc:
        return None
    parent = loc[:-1]
    field_names = _valid_field_names_for_path(parent)
    if not field_names:
        return None
    matches = difflib.get_close_matches(loc[-1], field_names, n=1)
    return matches[0] if matches else None


def _valid_field_names_for_path(path: tuple[str, ...]) -> tuple[str, ...]:
    if path in _VALID_FIELD_NAMES:
        return _VALID_FIELD_NAMES[path]
    # Dict-of-dataclass levels (e.g. build.nsx_modules.<name>) are keyed with a
    # "*" wildcard standing in for the free-form dict key.
    if path:
        wildcard = (*path[:-1], "*")
        if wildcard in _VALID_FIELD_NAMES:
            return _VALID_FIELD_NAMES[wildcard]
    return ()


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
            psram_kb=_optional_int(spec.get("psram_kb", base_board.psram_kb if base_board else None)),
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
