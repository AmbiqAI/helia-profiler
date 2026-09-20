# helia_profiler.results

What a profiling run produces: the run result, its per-layer and PMU detail, and the firmware and run metadata that say how it was measured.

Every name on this page is imported from `helia_profiler`.

**API tier:** `stable`

## helia_profiler.LayerResult

`class` · `python`

```python
LayerResult(
    id: int | str,
    op: str,
    counters: dict[str, float] = dict(),
    cycles: float | None = None,
    overflow: bool = False,
    source_index: int | None = None,
) -> None
```

`dataclass`

Profiling result for a single model layer (averaged across iterations).

``source_index`` is the ORIGINAL tflite operator index this measured
layer corresponds to, when the op label carries one (#218). ``id`` is
the firmware's execution position, which on AOT engines is NOT the
original index — helia-aot skips ops without renumbering.

**API tier:** `stable`

Source: `src/helia_profiler/results/models.py:47`

### helia_profiler.LayerResult.id

`attribute` · `python`

```python
id: int | str
```

Source: `src/helia_profiler/results/models.py:57`

### helia_profiler.LayerResult.op

`attribute` · `python`

```python
op: str
```

Source: `src/helia_profiler/results/models.py:58`

### helia_profiler.LayerResult.counters

`attribute` · `python`

```python
counters: dict[str, float] = field(default_factory=dict)
```

Source: `src/helia_profiler/results/models.py:59`

### helia_profiler.LayerResult.cycles

`attribute` · `python`

```python
cycles: float | None = None
```

Source: `src/helia_profiler/results/models.py:60`

### helia_profiler.LayerResult.overflow

`attribute` · `python`

```python
overflow: bool = False
```

Source: `src/helia_profiler/results/models.py:61`

### helia_profiler.LayerResult.source_index

`attribute` · `python`

```python
source_index: int | None = None
```

Source: `src/helia_profiler/results/models.py:62`

## helia_profiler.PresetResult

`class` · `python`

```python
PresetResult(
    name: str,
    header: list[str] = list(),
    iterations: list[list[LayerResult]] = list(),
    layers: list[LayerResult] = list(),
) -> None
```

`dataclass`

Results for a single PMU counter preset (e.g. ``basic_cpu``).

**API tier:** `stable`

Source: `src/helia_profiler/results/models.py:71`

### helia_profiler.PresetResult.name

`attribute` · `python`

```python
name: str
```

Source: `src/helia_profiler/results/models.py:75`

### helia_profiler.PresetResult.header

`attribute` · `python`

```python
header: list[str] = field(default_factory=list)
```

Source: `src/helia_profiler/results/models.py:76`

### helia_profiler.PresetResult.iterations

`attribute` · `python`

```python
iterations: list[list[LayerResult]] = field(default_factory=list)
```

Source: `src/helia_profiler/results/models.py:77`

### helia_profiler.PresetResult.layers

`attribute` · `python`

```python
layers: list[LayerResult] = field(default_factory=list)
```

Source: `src/helia_profiler/results/models.py:78`

## helia_profiler.FirmwareMeta

`class` · `python`

```python
FirmwareMeta(
    model_size: int | None = None,
    arena_size: int | None = None,
    allocated_arena: int | None = None,
    input_size: int | None = None,
    output_size: int | None = None,
    num_tensors: int | None = None,
    num_inputs: int | None = None,
    num_outputs: int | None = None,
    num_presets: int | None = None,
    system_clock_hz: int | None = None,
    profiled_infer_count: int | None = None,
    profiled_infer_total_us: int | None = None,
    profiled_infer_avg_us: int | None = None,
    clean_infer_count: int | None = None,
    clean_infer_total_cycles: int | None = None,
    clean_infer_avg_cycles: int | None = None,
    clean_infer_avg_us: int | None = None,
    clean_stalled_iters: int | None = None,
    clean_partial_iters: int | None = None,
    clean_ref_cycles: int | None = None,
    clean_dwt_rate_cyc: int | None = None,
    clean_dwt_rate_us: int | None = None,
    clean_attach_wait_us: int | None = None,
    psram: PsramInfo | None = None,
    presets: tuple[str, ...] = (),
) -> None
```

`dataclass`

Metadata reported by the profiler firmware at startup.

All fields are optional because older firmware versions may not report
every field.

**API tier:** `stable`

Source: `src/helia_profiler/results/models.py:95`

### helia_profiler.FirmwareMeta.model_size

`attribute` · `python`

```python
model_size: int | None = None
```

Source: `src/helia_profiler/results/models.py:103`

### helia_profiler.FirmwareMeta.arena_size

`attribute` · `python`

```python
arena_size: int | None = None
```

Source: `src/helia_profiler/results/models.py:104`

### helia_profiler.FirmwareMeta.allocated_arena

`attribute` · `python`

```python
allocated_arena: int | None = None
```

Source: `src/helia_profiler/results/models.py:105`

### helia_profiler.FirmwareMeta.input_size

`attribute` · `python`

```python
input_size: int | None = None
```

Source: `src/helia_profiler/results/models.py:106`

### helia_profiler.FirmwareMeta.output_size

`attribute` · `python`

```python
output_size: int | None = None
```

Source: `src/helia_profiler/results/models.py:107`

### helia_profiler.FirmwareMeta.num_tensors

`attribute` · `python`

```python
num_tensors: int | None = None
```

Source: `src/helia_profiler/results/models.py:108`

### helia_profiler.FirmwareMeta.num_inputs

`attribute` · `python`

```python
num_inputs: int | None = None
```

Source: `src/helia_profiler/results/models.py:109`

### helia_profiler.FirmwareMeta.num_outputs

`attribute` · `python`

```python
num_outputs: int | None = None
```

Source: `src/helia_profiler/results/models.py:110`

### helia_profiler.FirmwareMeta.num_presets

`attribute` · `python`

```python
num_presets: int | None = None
```

Source: `src/helia_profiler/results/models.py:111`

### helia_profiler.FirmwareMeta.system_clock_hz

`attribute` · `python`

```python
system_clock_hz: int | None = None
```

Source: `src/helia_profiler/results/models.py:114`

### helia_profiler.FirmwareMeta.profiled_infer_count

`attribute` · `python`

```python
profiled_infer_count: int | None = None
```

Source: `src/helia_profiler/results/models.py:115`

### helia_profiler.FirmwareMeta.profiled_infer_total_us

`attribute` · `python`

```python
profiled_infer_total_us: int | None = None
```

Source: `src/helia_profiler/results/models.py:116`

### helia_profiler.FirmwareMeta.profiled_infer_avg_us

`attribute` · `python`

```python
profiled_infer_avg_us: int | None = None
```

Source: `src/helia_profiler/results/models.py:117`

### helia_profiler.FirmwareMeta.clean_infer_count

`attribute` · `python`

```python
clean_infer_count: int | None = None
```

Source: `src/helia_profiler/results/models.py:121`

### helia_profiler.FirmwareMeta.clean_infer_total_cycles

`attribute` · `python`

```python
clean_infer_total_cycles: int | None = None
```

Source: `src/helia_profiler/results/models.py:122`

### helia_profiler.FirmwareMeta.clean_infer_avg_cycles

`attribute` · `python`

```python
clean_infer_avg_cycles: int | None = None
```

Source: `src/helia_profiler/results/models.py:123`

### helia_profiler.FirmwareMeta.clean_infer_avg_us

`attribute` · `python`

```python
clean_infer_avg_us: int | None = None
```

Source: `src/helia_profiler/results/models.py:124`

### helia_profiler.FirmwareMeta.clean_stalled_iters

`attribute` · `python`

```python
clean_stalled_iters: int | None = None
```

Source: `src/helia_profiler/results/models.py:129`

### helia_profiler.FirmwareMeta.clean_partial_iters

`attribute` · `python`

```python
clean_partial_iters: int | None = None
```

Source: `src/helia_profiler/results/models.py:134`

### helia_profiler.FirmwareMeta.clean_ref_cycles

`attribute` · `python`

```python
clean_ref_cycles: int | None = None
```

Source: `src/helia_profiler/results/models.py:144`

### helia_profiler.FirmwareMeta.clean_dwt_rate_cyc

`attribute` · `python`

```python
clean_dwt_rate_cyc: int | None = None
```

Source: `src/helia_profiler/results/models.py:150`

### helia_profiler.FirmwareMeta.clean_dwt_rate_us

`attribute` · `python`

```python
clean_dwt_rate_us: int | None = None
```

Source: `src/helia_profiler/results/models.py:151`

### helia_profiler.FirmwareMeta.clean_attach_wait_us

`attribute` · `python`

```python
clean_attach_wait_us: int | None = None
```

Source: `src/helia_profiler/results/models.py:156`

### helia_profiler.FirmwareMeta.psram

`attribute` · `python`

```python
psram: PsramInfo | None = None
```

Source: `src/helia_profiler/results/models.py:157`

### helia_profiler.FirmwareMeta.presets

`attribute` · `python`

```python
presets: tuple[str, ...] = ()
```

Source: `src/helia_profiler/results/models.py:158`

### helia_profiler.FirmwareMeta.reported_model_bytes

`attribute` · `python`

```python
reported_model_bytes: int | None
```

``model_size`` when it is a usable byte count, else ``None``.

The wire parser keeps an unparseable ``HPX_MODEL_SIZE`` as the raw
string it received, so a corrupted or foreign line reaches consumers
as text. Anything that needs a number asks here instead of assuming;
the raw value stays on ``model_size`` so diagnostics can still quote
what the device actually said (#281).

Source: `src/helia_profiler/results/models.py:161`

## helia_profiler.PmuResult

`class` · `python`

```python
PmuResult(
    meta: FirmwareMeta,
    presets: dict[str, PresetResult] = dict(),
    layers: list[LayerResult] = list(),
    overflow_detected: bool = False,
    groups: dict[str, list[LayerResult]] = dict(),
) -> None
```

`dataclass`

Complete PMU profiling result across all presets.

**API tier:** `stable`

Source: `src/helia_profiler/results/models.py:176`

### helia_profiler.PmuResult.meta

`attribute` · `python`

```python
meta: FirmwareMeta
```

Source: `src/helia_profiler/results/models.py:180`

### helia_profiler.PmuResult.presets

`attribute` · `python`

```python
presets: dict[str, PresetResult] = field(default_factory=dict)
```

Source: `src/helia_profiler/results/models.py:181`

### helia_profiler.PmuResult.layers

`attribute` · `python`

```python
layers: list[LayerResult] = field(default_factory=list)
```

Source: `src/helia_profiler/results/models.py:182`

### helia_profiler.PmuResult.overflow_detected

`attribute` · `python`

```python
overflow_detected: bool = False
```

Source: `src/helia_profiler/results/models.py:183`

### helia_profiler.PmuResult.groups

`attribute` · `python`

```python
groups: dict[str, list[LayerResult]] = field(default_factory=dict)
```

Source: `src/helia_profiler/results/models.py:188`

## helia_profiler.RunMetadata

`class` · `python`

```python
RunMetadata(
    hpx_version: str = '',
    run_id: str = '',
    timestamp: str = '',
    config_snapshot: dict[str, Any] = dict(),
    platform: PlatformInfo | None = None,
    model: ModelInfo | None = None,
    toolchain: ToolchainInfo | None = None,
    build_images: tuple[BuildImage, ...] = (),
    engine: EngineInfo | None = None,
    firmware: FirmwareMeta | None = None,
    memory_plan: 'MemoryPlan | None' = None,
    timing: TimingInfo | None = None,
    compatibility: CompatibilityResolution | None = None,
    dependencies: 'DependencyProvenance | None' = None,
) -> None
```

`dataclass`

Accumulated run metadata — enriched by stages, consumed by reports.

**API tier:** `stable`

Source: `src/helia_profiler/results/models.py:287`

### helia_profiler.RunMetadata.hpx_version

`attribute` · `python`

```python
hpx_version: str = ''
```

Source: `src/helia_profiler/results/models.py:291`

### helia_profiler.RunMetadata.run_id

`attribute` · `python`

```python
run_id: str = ''
```

Source: `src/helia_profiler/results/models.py:292`

### helia_profiler.RunMetadata.timestamp

`attribute` · `python`

```python
timestamp: str = ''
```

Source: `src/helia_profiler/results/models.py:293`

### helia_profiler.RunMetadata.config_snapshot

`attribute` · `python`

```python
config_snapshot: dict[str, Any] = field(default_factory=dict)
```

Source: `src/helia_profiler/results/models.py:294`

### helia_profiler.RunMetadata.platform

`attribute` · `python`

```python
platform: PlatformInfo | None = None
```

Source: `src/helia_profiler/results/models.py:295`

### helia_profiler.RunMetadata.model

`attribute` · `python`

```python
model: ModelInfo | None = None
```

Source: `src/helia_profiler/results/models.py:296`

### helia_profiler.RunMetadata.toolchain

`attribute` · `python`

```python
toolchain: ToolchainInfo | None = None
```

Source: `src/helia_profiler/results/models.py:297`

### helia_profiler.RunMetadata.build_images

`attribute` · `python`

```python
build_images: tuple[BuildImage, ...] = ()
```

Source: `src/helia_profiler/results/models.py:300`

### helia_profiler.RunMetadata.engine

`attribute` · `python`

```python
engine: EngineInfo | None = None
```

Source: `src/helia_profiler/results/models.py:301`

### helia_profiler.RunMetadata.firmware

`attribute` · `python`

```python
firmware: FirmwareMeta | None = None
```

Source: `src/helia_profiler/results/models.py:302`

### helia_profiler.RunMetadata.memory_plan

`attribute` · `python`

```python
memory_plan: 'MemoryPlan | None' = None
```

Source: `src/helia_profiler/results/models.py:303`

### helia_profiler.RunMetadata.timing

`attribute` · `python`

```python
timing: TimingInfo | None = None
```

Source: `src/helia_profiler/results/models.py:304`

### helia_profiler.RunMetadata.compatibility

`attribute` · `python`

```python
compatibility: CompatibilityResolution | None = None
```

Source: `src/helia_profiler/results/models.py:305`

### helia_profiler.RunMetadata.dependencies

`attribute` · `python`

```python
dependencies: 'DependencyProvenance | None' = None
```

Source: `src/helia_profiler/results/models.py:306`

## helia_profiler.ProfileResult

`class` · `python`

```python
ProfileResult(
    pmu: PmuResult,
    power: PowerResult | None = None,
    power_observation: PowerObservation | None = None,
    power_terminal: PowerTerminalRecord | None = None,
    on_device_power: OnDevicePowerSummary | None = None,
    metadata: RunMetadata = RunMetadata(),
    report_paths: list[Path] = list(),
) -> None
```

`dataclass`

Complete profiling result — the public return type of ``hpx.profile()``.

This is the one object a programmatic user needs.  It carries everything:
PMU data, optional power data, run metadata, and report file paths.

**API tier:** `stable`

Source: `src/helia_profiler/results/models.py:615`

### helia_profiler.ProfileResult.pmu

`attribute` · `python`

```python
pmu: PmuResult
```

Source: `src/helia_profiler/results/models.py:623`

### helia_profiler.ProfileResult.power

`attribute` · `python`

```python
power: PowerResult | None = None
```

Source: `src/helia_profiler/results/models.py:624`

### helia_profiler.ProfileResult.power_observation

`attribute` · `python`

```python
power_observation: PowerObservation | None = None
```

Source: `src/helia_profiler/results/models.py:625`

### helia_profiler.ProfileResult.power_terminal

`attribute` · `python`

```python
power_terminal: PowerTerminalRecord | None = None
```

Source: `src/helia_profiler/results/models.py:626`

### helia_profiler.ProfileResult.on_device_power

`attribute` · `python`

```python
on_device_power: OnDevicePowerSummary | None = None
```

Source: `src/helia_profiler/results/models.py:627`

### helia_profiler.ProfileResult.metadata

`attribute` · `python`

```python
metadata: RunMetadata = field(default_factory=RunMetadata)
```

Source: `src/helia_profiler/results/models.py:628`

### helia_profiler.ProfileResult.report_paths

`attribute` · `python`

```python
report_paths: list[Path] = field(default_factory=list)
```

Source: `src/helia_profiler/results/models.py:629`

### helia_profiler.ProfileResult.layers

`attribute` · `python`

```python
layers: list[LayerResult]
```

Merged per-layer results across all PMU presets.

Source: `src/helia_profiler/results/models.py:634`

### helia_profiler.ProfileResult.total_cycles

`attribute` · `python`

```python
total_cycles: float
```

Total CPU cycles across all layers.

Source: `src/helia_profiler/results/models.py:639`

### helia_profiler.ProfileResult.layer_count

`attribute` · `python`

```python
layer_count: int
```

Source: `src/helia_profiler/results/models.py:644`

### helia_profiler.ProfileResult.overflow_detected

`attribute` · `python`

```python
overflow_detected: bool
```

Source: `src/helia_profiler/results/models.py:648`
