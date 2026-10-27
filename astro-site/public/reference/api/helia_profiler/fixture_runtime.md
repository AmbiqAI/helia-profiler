# helia_profiler.fixture_runtime

The compiled firmware a build produces and what it recorded: the build record, its memory plan, toolchain and PSRAM weight placement.

Every name on this page is imported from `helia_profiler`.

**API tier:** `experimental`

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__`.

## helia_profiler.FixturePsramPayload

`class` · `python`

```python
FixturePsramPayload(region_id: int, size: int, checksum: int) -> None
```

`dataclass`

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_psram.py:25`

### helia_profiler.FixturePsramPayload.region_id

`attribute` · `python`

```python
region_id: int
```

Source: `src/helia_profiler/fixture_psram.py:27`

### helia_profiler.FixturePsramPayload.size

`attribute` · `python`

```python
size: int
```

Source: `src/helia_profiler/fixture_psram.py:28`

### helia_profiler.FixturePsramPayload.checksum

`attribute` · `python`

```python
checksum: int
```

Source: `src/helia_profiler/fixture_psram.py:29`

## helia_profiler.FixturePsramWeights

`class` · `python`

```python
FixturePsramWeights(
    payloads: tuple[FixturePsramPayload, ...],
    image_sha256: str | None = None,
    clock_hz: int = 96000000,
) -> None
```

`dataclass`

Expected constant payloads; an uncompiled build has no image binding.

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_psram.py:43`

### helia_profiler.FixturePsramWeights.payloads

`attribute` · `python`

```python
payloads: tuple[FixturePsramPayload, ...]
```

Source: `src/helia_profiler/fixture_psram.py:47`

### helia_profiler.FixturePsramWeights.image_sha256

`attribute` · `python`

```python
image_sha256: str | None = None
```

Source: `src/helia_profiler/fixture_psram.py:48`

### helia_profiler.FixturePsramWeights.clock_hz

`attribute` · `python`

```python
clock_hz: int = 96000000
```

Source: `src/helia_profiler/fixture_psram.py:49`

### helia_profiler.FixturePsramWeights.sink_size

`attribute` · `python`

```python
sink_size: int
```

Source: `src/helia_profiler/fixture_psram.py:68`

## helia_profiler.FixtureBuild

`class` · `python`

```python
FixtureBuild(
    fixture_identity: str,
    app_dir: Path,
    binary: FixtureFile | None,
    generated_sources: tuple[FixtureFile, ...],
    expected: FixtureFile | None,
    built: bool,
    runtime_manifest: FixtureFile | None,
    flat_binary: FixtureFile | None,
    dependency_lock: FixtureFile | None,
    link_map: FixtureFile | None,
    timing_scope: FixtureTimingScope,
    engine: EngineType,
    build_identity: str | None,
    iterations: int,
    warmups: int,
    intent_identity: str,
    target: FixtureTarget,
    toolchain: ToolchainInfo | None,
    provider_provenance: str = 'manifest-declared; independently audit the pinned build/source record',
    planned_memory: MemoryPlan | None = None,
    planned_memory_reason: str | None = 'not_recorded',
    outputs: tuple[FixtureFile, ...] = (),
    capabilities: tuple[tuple[str, FixtureCapability], ...] = (),
    aot_arena_scan: tuple[tuple[int, int], ...] = (),
    aot_outputs: tuple[FixtureFile, ...] = (),
    engine_source: EngineSource | None = None,
    dependency_state: FixtureFile | None = None,
    state_policy: FixtureStatePolicy | None = None,
    source_initializer_present: bool = False,
    psram_weights: FixturePsramWeights | None = None,
    arena_location: Placement | None = None,
    weights_location: Placement | None = None,
    model: FixtureFile | None = None,
    inputs: tuple[FixtureIO, ...] = (),
    output_tensors: tuple[FixtureTensor, ...] = (),
    arena_capacity: int | None = None,
    clock_profile: str | None = None,
    cpu_hz: int | None = None,
    timer_hz: int | None = None,
    power_sync: FixturePowerSync | None = None,
    observe_ram: bool = False,
    kernel_source: FixtureKernelSource | None = None,
    runtime_source: FixtureRuntimeSource | None = None,
) -> None
```

`dataclass`

**API tier:** `experimental`

Source: `src/helia_profiler/_fixture_build.py:233`

### helia_profiler.FixtureBuild.fixture_identity

`attribute` · `python`

```python
fixture_identity: str
```

Source: `src/helia_profiler/_fixture_build.py:235`

### helia_profiler.FixtureBuild.app_dir

`attribute` · `python`

```python
app_dir: Path
```

Source: `src/helia_profiler/_fixture_build.py:236`

### helia_profiler.FixtureBuild.binary

`attribute` · `python`

```python
binary: FixtureFile | None
```

Source: `src/helia_profiler/_fixture_build.py:237`

### helia_profiler.FixtureBuild.generated_sources

`attribute` · `python`

```python
generated_sources: tuple[FixtureFile, ...]
```

Source: `src/helia_profiler/_fixture_build.py:238`

### helia_profiler.FixtureBuild.expected

`attribute` · `python`

```python
expected: FixtureFile | None
```

Source: `src/helia_profiler/_fixture_build.py:239`

### helia_profiler.FixtureBuild.built

`attribute` · `python`

```python
built: bool
```

Source: `src/helia_profiler/_fixture_build.py:240`

### helia_profiler.FixtureBuild.runtime_manifest

`attribute` · `python`

```python
runtime_manifest: FixtureFile | None
```

Source: `src/helia_profiler/_fixture_build.py:241`

### helia_profiler.FixtureBuild.flat_binary

`attribute` · `python`

```python
flat_binary: FixtureFile | None
```

Source: `src/helia_profiler/_fixture_build.py:242`

### helia_profiler.FixtureBuild.dependency_lock

`attribute` · `python`

```python
dependency_lock: FixtureFile | None
```

Source: `src/helia_profiler/_fixture_build.py:243`

### helia_profiler.FixtureBuild.link_map

`attribute` · `python`

```python
link_map: FixtureFile | None
```

Source: `src/helia_profiler/_fixture_build.py:244`

### helia_profiler.FixtureBuild.timing_scope

`attribute` · `python`

```python
timing_scope: FixtureTimingScope
```

Source: `src/helia_profiler/_fixture_build.py:245`

### helia_profiler.FixtureBuild.engine

`attribute` · `python`

```python
engine: EngineType
```

Source: `src/helia_profiler/_fixture_build.py:246`

### helia_profiler.FixtureBuild.build_identity

`attribute` · `python`

```python
build_identity: str | None
```

Source: `src/helia_profiler/_fixture_build.py:247`

### helia_profiler.FixtureBuild.iterations

`attribute` · `python`

```python
iterations: int
```

Source: `src/helia_profiler/_fixture_build.py:248`

### helia_profiler.FixtureBuild.warmups

`attribute` · `python`

```python
warmups: int
```

Source: `src/helia_profiler/_fixture_build.py:249`

### helia_profiler.FixtureBuild.intent_identity

`attribute` · `python`

```python
intent_identity: str
```

Source: `src/helia_profiler/_fixture_build.py:250`

### helia_profiler.FixtureBuild.target

`attribute` · `python`

```python
target: FixtureTarget
```

Source: `src/helia_profiler/_fixture_build.py:251`

### helia_profiler.FixtureBuild.toolchain

`attribute` · `python`

```python
toolchain: ToolchainInfo | None
```

Source: `src/helia_profiler/_fixture_build.py:252`

### helia_profiler.FixtureBuild.provider_provenance

`attribute` · `python`

```python
provider_provenance: str = 'manifest-declared; independently audit the pinned build/source record'
```

Source: `src/helia_profiler/_fixture_build.py:253`

### helia_profiler.FixtureBuild.planned_memory

`attribute` · `python`

```python
planned_memory: MemoryPlan | None = None
```

Source: `src/helia_profiler/_fixture_build.py:256`

### helia_profiler.FixtureBuild.planned_memory_reason

`attribute` · `python`

```python
planned_memory_reason: str | None = 'not_recorded'
```

Source: `src/helia_profiler/_fixture_build.py:257`

### helia_profiler.FixtureBuild.outputs

`attribute` · `python`

```python
outputs: tuple[FixtureFile, ...] = ()
```

Source: `src/helia_profiler/_fixture_build.py:259`

### helia_profiler.FixtureBuild.capabilities

`attribute` · `python`

```python
capabilities: tuple[tuple[str, FixtureCapability], ...] = ()
```

Source: `src/helia_profiler/_fixture_build.py:261`

### helia_profiler.FixtureBuild.aot_arena_scan

`attribute` · `python`

```python
aot_arena_scan: tuple[tuple[int, int], ...] = ()
```

Source: `src/helia_profiler/_fixture_build.py:263`

### helia_profiler.FixtureBuild.aot_outputs

`attribute` · `python`

```python
aot_outputs: tuple[FixtureFile, ...] = ()
```

Source: `src/helia_profiler/_fixture_build.py:266`

### helia_profiler.FixtureBuild.engine_source

`attribute` · `python`

```python
engine_source: EngineSource | None = None
```

Source: `src/helia_profiler/_fixture_build.py:268`

### helia_profiler.FixtureBuild.dependency_state

`attribute` · `python`

```python
dependency_state: FixtureFile | None = None
```

Source: `src/helia_profiler/_fixture_build.py:270`

### helia_profiler.FixtureBuild.state_policy

`attribute` · `python`

```python
state_policy: FixtureStatePolicy | None = None
```

Source: `src/helia_profiler/_fixture_build.py:271`

### helia_profiler.FixtureBuild.source_initializer_present

`attribute` · `python`

```python
source_initializer_present: bool = False
```

Source: `src/helia_profiler/_fixture_build.py:272`

### helia_profiler.FixtureBuild.psram_weights

`attribute` · `python`

```python
psram_weights: FixturePsramWeights | None = None
```

Source: `src/helia_profiler/_fixture_build.py:274`

### helia_profiler.FixtureBuild.arena_location

`attribute` · `python`

```python
arena_location: Placement | None = None
```

Source: `src/helia_profiler/_fixture_build.py:276`

### helia_profiler.FixtureBuild.weights_location

`attribute` · `python`

```python
weights_location: Placement | None = None
```

Source: `src/helia_profiler/_fixture_build.py:277`

### helia_profiler.FixtureBuild.model

`attribute` · `python`

```python
model: FixtureFile | None = None
```

Source: `src/helia_profiler/_fixture_build.py:279`

### helia_profiler.FixtureBuild.inputs

`attribute` · `python`

```python
inputs: tuple[FixtureIO, ...] = ()
```

Source: `src/helia_profiler/_fixture_build.py:280`

### helia_profiler.FixtureBuild.output_tensors

`attribute` · `python`

```python
output_tensors: tuple[FixtureTensor, ...] = ()
```

Source: `src/helia_profiler/_fixture_build.py:281`

### helia_profiler.FixtureBuild.arena_capacity

`attribute` · `python`

```python
arena_capacity: int | None = None
```

Source: `src/helia_profiler/_fixture_build.py:282`

### helia_profiler.FixtureBuild.clock_profile

`attribute` · `python`

```python
clock_profile: str | None = None
```

Source: `src/helia_profiler/_fixture_build.py:283`

### helia_profiler.FixtureBuild.cpu_hz

`attribute` · `python`

```python
cpu_hz: int | None = None
```

Source: `src/helia_profiler/_fixture_build.py:284`

### helia_profiler.FixtureBuild.timer_hz

`attribute` · `python`

```python
timer_hz: int | None = None
```

Source: `src/helia_profiler/_fixture_build.py:285`

### helia_profiler.FixtureBuild.power_sync

`attribute` · `python`

```python
power_sync: FixturePowerSync | None = None
```

Source: `src/helia_profiler/_fixture_build.py:286`

### helia_profiler.FixtureBuild.observe_ram

`attribute` · `python`

```python
observe_ram: bool = False
```

Source: `src/helia_profiler/_fixture_build.py:287`

### helia_profiler.FixtureBuild.kernel_source

`attribute` · `python`

```python
kernel_source: FixtureKernelSource | None = None
```

Source: `src/helia_profiler/_fixture_build.py:290`

### helia_profiler.FixtureBuild.runtime_source

`attribute` · `python`

```python
runtime_source: FixtureRuntimeSource | None = None
```

Source: `src/helia_profiler/_fixture_build.py:292`

## helia_profiler.ToolchainInfo

`class` · `python`

```python
ToolchainInfo(compiler: str = '', compiler_version: str = '', cmake_version: str = '') -> None
```

`dataclass`

Build toolchain versions (populated by stage 4).

**API tier:** `experimental`

Source: `src/helia_profiler/results/models.py:254`

### helia_profiler.ToolchainInfo.compiler

`attribute` · `python`

```python
compiler: str = ''
```

Source: `src/helia_profiler/results/models.py:258`

### helia_profiler.ToolchainInfo.compiler_version

`attribute` · `python`

```python
compiler_version: str = ''
```

Source: `src/helia_profiler/results/models.py:259`

### helia_profiler.ToolchainInfo.cmake_version

`attribute` · `python`

```python
cmake_version: str = ''
```

Source: `src/helia_profiler/results/models.py:260`

## helia_profiler.MemoryPlan

`class` · `python`

```python
MemoryPlan(
    engine: EngineType,
    regions: tuple[MemoryRegionUsage, ...] = (),
    model_weight_bytes: int = 0,
    has_overflow: bool = False,
) -> None
```

`dataclass`

Engine-agnostic memory plan for a single profiling run.

Produced by the ``plan_memory`` stage by combining engine-specific
knowledge (AOT arena regions, TFLM single-arena size, weight placement)
with the SoC's physical memory layout.  Consumed by the report and by
the firmware template generator for placement macros / linker hints.

**API tier:** `experimental`

Source: `src/helia_profiler/results/models.py:441`

### helia_profiler.MemoryPlan.engine

`attribute` · `python`

```python
engine: EngineType
```

Source: `src/helia_profiler/results/models.py:451`

### helia_profiler.MemoryPlan.regions

`attribute` · `python`

```python
regions: tuple[MemoryRegionUsage, ...] = ()
```

Source: `src/helia_profiler/results/models.py:452`

### helia_profiler.MemoryPlan.model_weight_bytes

`attribute` · `python`

```python
model_weight_bytes: int = 0
```

Source: `src/helia_profiler/results/models.py:454`

### helia_profiler.MemoryPlan.has_overflow

`attribute` · `python`

```python
has_overflow: bool = False
```

Source: `src/helia_profiler/results/models.py:458`

### helia_profiler.MemoryPlan.region

`method` · `python`

```python
region(name: str | MemoryRegion) -> MemoryRegionUsage | None
```

Source: `src/helia_profiler/results/models.py:464`
