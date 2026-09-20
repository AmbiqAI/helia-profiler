# helia_profiler.config

The run description: what to profile, on what, how, and what to write out. Every field of a YAML config file lands in one of these.

Every name on this page is imported from `helia_profiler`.

**API tier:** `stable`

Generated from [`src/helia_profiler` at `cc1c3ed`](https://github.com/AmbiqAI/helia-profiler/tree/cc1c3ed4a7908d4c5552f800e8b3790188b8bbd8/src/helia_profiler).

## helia_profiler.ClockSelection

`class` · `python`

```python
ClockSelection()
```

Per-domain clock speed selection for the generated firmware.

Each field names a speed within the SoC's matching clock domain using
Ambiq datasheet terminology (e.g. ``cpu="hp"``).  ``None`` selects that
domain's default speed.  Values are validated against the resolved SoC in
stage 1, so unknown names raise a clear ConfigError rather than failing
silently.

**API tier:** `stable`

Source: `src/helia_profiler/config/__init__.py:72`

### helia_profiler.ClockSelection.cpu

`attribute` · `python`

```python
cpu: str | None = None
```

Source: `src/helia_profiler/config/__init__.py:83`

## helia_profiler.OutputFormat

`class` · `python`

```python
OutputFormat()
```

Top-level report format emitted by the report stage.

**API tier:** `stable`

Source: `src/helia_profiler/config/__init__.py:117`

### helia_profiler.OutputFormat.CSV

`constant` · `python`

```python
CSV = 'csv'
```

Source: `src/helia_profiler/config/__init__.py:120`

### helia_profiler.OutputFormat.JSON

`constant` · `python`

```python
JSON = 'json'
```

Source: `src/helia_profiler/config/__init__.py:121`

### helia_profiler.OutputFormat.MODEL_EXPLORER

`constant` · `python`

```python
MODEL_EXPLORER = 'model-explorer'
```

Source: `src/helia_profiler/config/__init__.py:122`

## helia_profiler.ModelConfig

`class` · `python`

```python
ModelConfig()
```

Model file and arena sizing.

``arena_location`` and ``weights_location`` are the preferred placement
controls for runtime engines such as heliaRT: the arena is the mutable
tensor arena, while weights are the model flatbuffer/constant data.

    When a split field is omitted, the engine and memory planner choose the
    fastest region that fits. ``helia-aot`` translates these coarse controls
    into tensor rules; explicit ``engine.config.aot_args.memory.tensors`` rules
    remain available for per-kind and per-tensor placement.

**API tier:** `stable`

Source: `src/helia_profiler/config/__init__.py:189`

### helia_profiler.ModelConfig.path

`attribute` · `python`

```python
path: Path
```

Source: `src/helia_profiler/config/__init__.py:203`

### helia_profiler.ModelConfig.arena_size

`attribute` · `python`

```python
arena_size: int | None = None
```

Source: `src/helia_profiler/config/__init__.py:204`

### helia_profiler.ModelConfig.arena_location

`attribute` · `python`

```python
arena_location: Placement | str | None = None
```

Source: `src/helia_profiler/config/__init__.py:205`

### helia_profiler.ModelConfig.weights_location

`attribute` · `python`

```python
weights_location: Placement | str | None = None
```

Source: `src/helia_profiler/config/__init__.py:206`

## helia_profiler.EngineConfig

`class` · `python`

```python
EngineConfig()
```

Inference engine selection and passthrough config.

**API tier:** `stable`

Source: `src/helia_profiler/config/__init__.py:219`

### helia_profiler.EngineConfig.type

`attribute` · `python`

```python
type: EngineType = EngineType.HELIA_RT
```

Source: `src/helia_profiler/config/__init__.py:223`

### helia_profiler.EngineConfig.backend

`attribute` · `python`

```python
backend: str | None = None
```

Source: `src/helia_profiler/config/__init__.py:224`

### helia_profiler.EngineConfig.config

`attribute` · `python`

```python
config: dict[str, Any] = field(default_factory=dict)
```

Source: `src/helia_profiler/config/__init__.py:225`

### helia_profiler.EngineConfig.config_path

`attribute` · `python`

```python
config_path: Path | None = None
```

Source: `src/helia_profiler/config/__init__.py:226`

## helia_profiler.HeartbeatConfig

`class` · `python`

```python
HeartbeatConfig()
```

Liveness / progress-reporting settings.

The firmware emits ``HPX_HEARTBEAT`` lines at configurable intervals so
the host can (a) detect a hung run without using a large wall-clock
timeout, and (b) show the user live progress.

**API tier:** `stable`

Source: `src/helia_profiler/config/__init__.py:248`

### helia_profiler.HeartbeatConfig.enabled

`attribute` · `python`

```python
enabled: bool = True
```

Master switch.  When ``False``, no heartbeats are emitted or
expected and the host falls back to the legacy line-gap timeout.

Source: `src/helia_profiler/config/__init__.py:274`

### helia_profiler.HeartbeatConfig.every_n_ops

`attribute` · `python`

```python
every_n_ops: int = DEFAULT_HB_EVERY_N_OPS
```

Emit a heartbeat after this many profiled ops.  ``0``
disables this trigger.  Lower values add more PMU/inter-op
overhead but give finer-grained progress.

Source: `src/helia_profiler/config/__init__.py:275`

### helia_profiler.HeartbeatConfig.every_ms

`attribute` · `python`

```python
every_ms: int = DEFAULT_HB_EVERY_MS
```

Emit a heartbeat when at least this many wall-clock
milliseconds have elapsed since the last heartbeat.  ``0``
disables this trigger.  Useful for engines with a single large
invocation (e.g. AOT command streams) where ``every_n_ops`` does
not fire.

Source: `src/helia_profiler/config/__init__.py:276`

### helia_profiler.HeartbeatConfig.host_timeout_s

`attribute` · `python`

```python
host_timeout_s: int = DEFAULT_HB_HOST_TIMEOUT_S
```

Maximum time the host will wait without receiving
*any* line from the firmware before declaring the run hung.

Source: `src/helia_profiler/config/__init__.py:277`

### helia_profiler.HeartbeatConfig.overall_timeout_s

`attribute` · `python`

```python
overall_timeout_s: int | None = DEFAULT_OVERALL_TIMEOUT_S
```

Hard ceiling on total capture time, in seconds.
``None`` means unbounded (rely on heartbeats).  Set to a positive
int for a safety net in CI or unattended runs.

Source: `src/helia_profiler/config/__init__.py:278`

## helia_profiler.TimeoutsConfig

`class` · `python`

```python
TimeoutsConfig()
```

Subprocess and network timeouts (seconds).

Every subprocess and long-lived HTTP call in heliaPROFILER reads its
timeout from this struct instead of hard-coding it.  Override any value
in YAML under ``timeouts:`` to adapt to slow CI machines, laggy J-Link
probes, or poor network conditions.

Capture-time timeouts (heartbeat / overall) live on ``HeartbeatConfig``
because they are tied to the on-device progress protocol.

**API tier:** `stable`

Source: `src/helia_profiler/config/__init__.py:281`

### helia_profiler.TimeoutsConfig.configure_s

`attribute` · `python`

```python
configure_s: int = DEFAULT_CONFIGURE_TIMEOUT_S
```

Source: `src/helia_profiler/config/__init__.py:294`

### helia_profiler.TimeoutsConfig.build_s

`attribute` · `python`

```python
build_s: int = DEFAULT_BUILD_TIMEOUT_S
```

Source: `src/helia_profiler/config/__init__.py:295`

### helia_profiler.TimeoutsConfig.flash_s

`attribute` · `python`

```python
flash_s: int = DEFAULT_FLASH_TIMEOUT_S
```

Source: `src/helia_profiler/config/__init__.py:296`

### helia_profiler.TimeoutsConfig.toolchain_probe_s

`attribute` · `python`

```python
toolchain_probe_s: int = DEFAULT_TOOLCHAIN_PROBE_S
```

Source: `src/helia_profiler/config/__init__.py:297`

### helia_profiler.TimeoutsConfig.binary_probe_s

`attribute` · `python`

```python
binary_probe_s: int = DEFAULT_BINARY_PROBE_S
```

Source: `src/helia_profiler/config/__init__.py:298`

### helia_profiler.TimeoutsConfig.download_api_s

`attribute` · `python`

```python
download_api_s: int = DEFAULT_DOWNLOAD_API_S
```

Source: `src/helia_profiler/config/__init__.py:299`

### helia_profiler.TimeoutsConfig.download_asset_s

`attribute` · `python`

```python
download_asset_s: int = DEFAULT_DOWNLOAD_ASSET_S
```

Source: `src/helia_profiler/config/__init__.py:300`

## helia_profiler.TargetConfig

`class` · `python`

```python
TargetConfig()
```

Hardware target.

**API tier:** `stable`

Source: `src/helia_profiler/config/__init__.py:303`

### helia_profiler.TargetConfig.board

`attribute` · `python`

```python
board: str = DEFAULT_BOARD
```

Source: `src/helia_profiler/config/__init__.py:307`

### helia_profiler.TargetConfig.toolchain

`attribute` · `python`

```python
toolchain: Toolchain = DEFAULT_TOOLCHAIN
```

Source: `src/helia_profiler/config/__init__.py:308`

### helia_profiler.TargetConfig.jlink_serial

`attribute` · `python`

```python
jlink_serial: str | None = None
```

Source: `src/helia_profiler/config/__init__.py:309`

### helia_profiler.TargetConfig.transport

`attribute` · `python`

```python
transport: Transport = DEFAULT_TRANSPORT
```

Source: `src/helia_profiler/config/__init__.py:310`

### helia_profiler.TargetConfig.usb_port

`attribute` · `python`

```python
usb_port: str | None = None
```

Source: `src/helia_profiler/config/__init__.py:311`

### helia_profiler.TargetConfig.segger_rtt_path

`attribute` · `python`

```python
segger_rtt_path: Path | None = None
```

Source: `src/helia_profiler/config/__init__.py:312`

### helia_profiler.TargetConfig.rtt_buffer_size_up

`attribute` · `python`

```python
rtt_buffer_size_up: int | None = None
```

Source: `src/helia_profiler/config/__init__.py:313`

### helia_profiler.TargetConfig.clock

`attribute` · `python`

```python
clock: ClockSelection = field(default_factory=ClockSelection)
```

Source: `src/helia_profiler/config/__init__.py:314`

### helia_profiler.TargetConfig.psram

`attribute` · `python`

```python
psram: PsramConfig = field(default_factory=PsramConfig)
```

Source: `src/helia_profiler/config/__init__.py:315`

### helia_profiler.TargetConfig.heartbeat

`attribute` · `python`

```python
heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
```

Source: `src/helia_profiler/config/__init__.py:316`

### helia_profiler.TargetConfig.custom_socs

`attribute` · `python`

```python
custom_socs: dict[str, Any] | None = None
```

Source: `src/helia_profiler/config/__init__.py:317`

### helia_profiler.TargetConfig.custom_boards

`attribute` · `python`

```python
custom_boards: dict[str, Any] | None = None
```

Source: `src/helia_profiler/config/__init__.py:318`

### helia_profiler.TargetConfig.ensure_board_powered

`attribute` · `python`

```python
ensure_board_powered: bool = False
```

Source: `src/helia_profiler/config/__init__.py:327`

## helia_profiler.ProfilingConfig

`class` · `python`

```python
ProfilingConfig()
```

PMU capture settings.

Counter selection is specified via *pmu_counters* — a mapping of
compute-unit group (``cpu``, ``mve``, ``memory``, ``ethos_npu``) to a
selection:

* ``"default"`` — curated set of the most useful counters.
* ``"all"``     — every counter in the group (multi-pass).
* ``["NAME", …]`` — explicit counter names.

The ``ethos_npu`` group samples the Ethos-U NPU's own PMU (NPU cycles,
MAC activity, SRAM/external bus beats) and requires an NPU-equipped board
plus ``engine.backend: ethos_u`` (engine.type helia-rt or helia-aot).

**API tier:** `stable`

Source: `src/helia_profiler/config/__init__.py:360`

### helia_profiler.ProfilingConfig.pmu_counters

`attribute` · `python`

```python
pmu_counters: dict[str, str | list[str]] = field(default_factory=lambda : ...)
```

Source: `src/helia_profiler/config/__init__.py:378`

### helia_profiler.ProfilingConfig.per_layer

`attribute` · `python`

```python
per_layer: bool = True
```

Source: `src/helia_profiler/config/__init__.py:379`

### helia_profiler.ProfilingConfig.iterations

`attribute` · `python`

```python
iterations: int = DEFAULT_ITERATIONS
```

Source: `src/helia_profiler/config/__init__.py:380`

### helia_profiler.ProfilingConfig.warmup

`attribute` · `python`

```python
warmup: int = DEFAULT_WARMUP
```

Source: `src/helia_profiler/config/__init__.py:381`

### helia_profiler.ProfilingConfig.window_mode

`attribute` · `python`

```python
window_mode: WindowMode = DEFAULT_WINDOW_MODE
```

Source: `src/helia_profiler/config/__init__.py:387`

### helia_profiler.ProfilingConfig.window_target_ms

`attribute` · `python`

```python
window_target_ms: int = DEFAULT_WINDOW_TARGET_MS
```

Source: `src/helia_profiler/config/__init__.py:388`

### helia_profiler.ProfilingConfig.window_min

`attribute` · `python`

```python
window_min: int = DEFAULT_WINDOW_MIN
```

Source: `src/helia_profiler/config/__init__.py:389`

### helia_profiler.ProfilingConfig.window_max

`attribute` · `python`

```python
window_max: int = DEFAULT_WINDOW_MAX
```

Source: `src/helia_profiler/config/__init__.py:390`

### helia_profiler.ProfilingConfig.clean_window_probe

`attribute` · `python`

```python
clean_window_probe: CleanWindowProbe = DEFAULT_CLEAN_WINDOW_PROBE
```

Source: `src/helia_profiler/config/__init__.py:393`

### helia_profiler.ProfilingConfig.clean_window_trace

`attribute` · `python`

```python
clean_window_trace: bool = False
```

Source: `src/helia_profiler/config/__init__.py:400`

### helia_profiler.ProfilingConfig.force_shared_sram

`attribute` · `python`

```python
force_shared_sram: bool = False
```

Source: `src/helia_profiler/config/__init__.py:407`

### helia_profiler.ProfilingConfig.aggregation

`attribute` · `python`

```python
aggregation: Aggregation = DEFAULT_AGGREGATION
```

Source: `src/helia_profiler/config/__init__.py:411`

### helia_profiler.ProfilingConfig.extreme_mode

`attribute` · `python`

```python
extreme_mode: bool = False
```

Source: `src/helia_profiler/config/__init__.py:417`

## helia_profiler.OutputConfig

`class` · `python`

```python
OutputConfig()
```

Report output settings.

**API tier:** `stable`

Source: `src/helia_profiler/config/__init__.py:450`

### helia_profiler.OutputConfig.format

`attribute` · `python`

```python
format: OutputFormat = OutputFormat.CSV
```

Source: `src/helia_profiler/config/__init__.py:454`

### helia_profiler.OutputConfig.dir

`attribute` · `python`

```python
dir: Path = Path('./results')
```

Source: `src/helia_profiler/config/__init__.py:455`

### helia_profiler.OutputConfig.model_explorer

`attribute` · `python`

```python
model_explorer: bool = True
```

Source: `src/helia_profiler/config/__init__.py:456`

### helia_profiler.OutputConfig.detailed

`attribute` · `python`

```python
detailed: bool = False
```

Source: `src/helia_profiler/config/__init__.py:457`

### helia_profiler.OutputConfig.fail_on_invalid

`attribute` · `python`

```python
fail_on_invalid: bool = False
```

Source: `src/helia_profiler/config/__init__.py:462`

## helia_profiler.BuildConfig

`class` · `python`

```python
BuildConfig()
```

NSX build-system overrides.

Controls how the generated firmware's NSX manifest resolves modules.
Default behaviour keeps the selected board's default NSX channel, and
generated manifests pin the qualified compatibility baseline's full commit
SHAs for the ``neuralspotx`` and ``nsx-ambiq-sdk`` projects unless the
user overrides those modules.

Advanced users can pin individual modules to a version, point them at
a local checkout, or select a custom git ref — useful for SoC/board
bring-up before changes land in the stable channel.

``compiler_launcher`` selects a CMake compiler launcher (e.g. ``sccache``
or ``ccache``) that wraps every compile to cache object output and speed
up repeated builds.  ``"auto"`` (the default) uses ``sccache`` then
``ccache`` if either is on ``PATH`` and otherwise does nothing — so the
mere presence of the binary is the opt-in.  ``"none"`` disables it; an
explicit tool name or path requires that the launcher be found.

Ordinary profiles reuse a structurally compatible ``nsx.lock`` byte for
byte and always materialize with frozen sync. ``update_dependencies`` is
the only mode that intentionally advances refs. ``offline`` additionally
requires the exact lock and all locked module trees to already exist.

**API tier:** `stable`

Source: `src/helia_profiler/config/__init__.py:513`

### helia_profiler.BuildConfig.channel

`attribute` · `python`

```python
channel: str | None = None
```

Source: `src/helia_profiler/config/__init__.py:540`

### helia_profiler.BuildConfig.nsx_modules

`attribute` · `python`

```python
nsx_modules: dict[str, NsxModuleOverride] = field(default_factory=dict)
```

Source: `src/helia_profiler/config/__init__.py:541`

### helia_profiler.BuildConfig.compiler_launcher

`attribute` · `python`

```python
compiler_launcher: str = 'auto'
```

Source: `src/helia_profiler/config/__init__.py:542`

### helia_profiler.BuildConfig.update_dependencies

`attribute` · `python`

```python
update_dependencies: bool = False
```

Source: `src/helia_profiler/config/__init__.py:543`

### helia_profiler.BuildConfig.offline

`attribute` · `python`

```python
offline: bool = False
```

Source: `src/helia_profiler/config/__init__.py:544`

## helia_profiler.ProfileConfig

`class` · `python`

```python
ProfileConfig()
```

Top-level immutable configuration for a profiling run.

**API tier:** `stable`

Source: `src/helia_profiler/config/__init__.py:594`

### helia_profiler.ProfileConfig.model

`attribute` · `python`

```python
model: ModelConfig
```

Source: `src/helia_profiler/config/__init__.py:601`

### helia_profiler.ProfileConfig.engine

`attribute` · `python`

```python
engine: EngineConfig = field(default_factory=lambda : ...)
```

Source: `src/helia_profiler/config/__init__.py:602`

### helia_profiler.ProfileConfig.target

`attribute` · `python`

```python
target: TargetConfig = field(default_factory=TargetConfig)
```

Source: `src/helia_profiler/config/__init__.py:603`

### helia_profiler.ProfileConfig.profiling

`attribute` · `python`

```python
profiling: ProfilingConfig = field(default_factory=ProfilingConfig)
```

Source: `src/helia_profiler/config/__init__.py:604`

### helia_profiler.ProfileConfig.power

`attribute` · `python`

```python
power: PowerConfig = field(default_factory=PowerConfig)
```

Source: `src/helia_profiler/config/__init__.py:605`

### helia_profiler.ProfileConfig.output

`attribute` · `python`

```python
output: OutputConfig = field(default_factory=OutputConfig)
```

Source: `src/helia_profiler/config/__init__.py:606`

### helia_profiler.ProfileConfig.timeouts

`attribute` · `python`

```python
timeouts: TimeoutsConfig = field(default_factory=TimeoutsConfig)
```

Source: `src/helia_profiler/config/__init__.py:607`

### helia_profiler.ProfileConfig.build

`attribute` · `python`

```python
build: BuildConfig = field(default_factory=BuildConfig)
```

Source: `src/helia_profiler/config/__init__.py:608`

### helia_profiler.ProfileConfig.platform_registry

`attribute` · `python`

```python
platform_registry: PlatformRegistry = field(default_factory=build_platform_registry)
```

Source: `src/helia_profiler/config/__init__.py:609`

### helia_profiler.ProfileConfig.compatibility_baseline

`attribute` · `python`

```python
compatibility_baseline: CompatibilityBaseline = field(default_factory=load_compatibility_baseline, init=False, repr=False)
```

Source: `src/helia_profiler/config/__init__.py:612`

### helia_profiler.ProfileConfig.compatibility

`attribute` · `python`

```python
compatibility: CompatibilityResolution | None = field(default=None, init=False, repr=False, compare=False)
```

Source: `src/helia_profiler/config/__init__.py:617`

### helia_profiler.ProfileConfig.frozen

`attribute` · `python`

```python
frozen: bool = False
```

Source: `src/helia_profiler/config/__init__.py:623`

### helia_profiler.ProfileConfig.work_dir

`attribute` · `python`

```python
work_dir: Path | None = None
```

Source: `src/helia_profiler/config/__init__.py:624`

### helia_profiler.ProfileConfig.clean

`attribute` · `python`

```python
clean: bool = False
```

Source: `src/helia_profiler/config/__init__.py:625`

### helia_profiler.ProfileConfig.verbose

`attribute` · `python`

```python
verbose: int = 0
```

Source: `src/helia_profiler/config/__init__.py:626`

### helia_profiler.ProfileConfig.effective_window_target_ms

`attribute` · `python`

```python
effective_window_target_ms: int
```

The clean-window target the firmware is actually built with.

``profiling.window_target_ms`` is raised to a power-usable floor only
in ``window_mode: auto`` -- ``fixed`` means "use my number". This is a
derived property rather than a helper each caller re-implements
because the rule spans two config sections and had drifted into three
hand-rolled copies, one of which disagreed: the firmware render used
the mode-gated form while the power planner clamped unconditionally,
so a ``fixed`` window shorter than the floor produced a plan
describing a 5 s window against firmware built to spin for 1 s. That
is invisible under the default ``auto``, which is why nothing caught
it (found by review of #136).

Source: `src/helia_profiler/config/__init__.py:649`
