# helia_profiler.compiled_build

Build once, run later: a frozen plan's compiled firmware, bound to its plan and saved so any host can verify, relocate and capture from it, how model state carries across a run's invocations, and the build and capture records a compiled build and a run result carry. The hpx freeze, hpx build, hpx validate build and hpx run commands drive the same flow from the command line.

Every name on this page is imported from `helia_profiler`.

**API tier:** `experimental`

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__`.

## helia_profiler.Lifecycle

`class` · `python`

```python
Lifecycle()
```

State handling across invocations.

``CARRY`` initializes one instance and keeps its internal state through
warmups and timed invocations, restoring only the fixed inputs each time.
``SINGLE`` runs one measured invocation per initialized instance, so no
state carries between invocations. Where an instance ends is the engine's
:class:`InstanceBoundary`: for a ``boot`` engine ``SINGLE`` is one timed
invocation and no warmups per boot; for an ``invocation`` engine, such as
ExecuTorch, every invocation is its own instance, so several measurements
are several instances, never one carried state. The fixture firmware
refuses a timed span of zero ticks of its 32.768 kHz timer, so a
``SINGLE`` invocation shorter than about 30.5 µs can fail as
``TIMING_BOUND``.

**API tier:** `experimental`

Source: `src/helia_profiler/lifecycle.py:17`

### helia_profiler.Lifecycle.CARRY

`constant` · `python`

```python
CARRY = 'carry'
```

Source: `src/helia_profiler/lifecycle.py:33`

### helia_profiler.Lifecycle.SINGLE

`constant` · `python`

```python
SINGLE = 'single'
```

Source: `src/helia_profiler/lifecycle.py:34`

## helia_profiler.FixtureKernelSource

`class` · `python`

```python
FixtureKernelSource(name: str, url: str | None, version: str | None, commit: str | None) -> None
```

`dataclass`

The kernel library a build linked: ``ns-cmsis-nn`` or ``arm-cmsis-nn``.

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_sources.py:19`

### helia_profiler.FixtureKernelSource.name

`attribute` · `python`

```python
name: str
```

Source: `src/helia_profiler/fixture_sources.py:23`

### helia_profiler.FixtureKernelSource.url

`attribute` · `python`

```python
url: str | None
```

Source: `src/helia_profiler/fixture_sources.py:24`

### helia_profiler.FixtureKernelSource.version

`attribute` · `python`

```python
version: str | None
```

Source: `src/helia_profiler/fixture_sources.py:26`

### helia_profiler.FixtureKernelSource.commit

`attribute` · `python`

```python
commit: str | None
```

Source: `src/helia_profiler/fixture_sources.py:27`

## helia_profiler.FixturePowerSync

`class` · `python`

```python
FixturePowerSync(sync_gpio_pin: int, state_gpio_pin: int, go_gpio_pin: int, protocol_version: int = 2) -> None
```

`dataclass`

Explicit firmware GPIO wiring; constructing this never opens hardware.

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_power.py:20`

### helia_profiler.FixturePowerSync.sync_gpio_pin

`attribute` · `python`

```python
sync_gpio_pin: int
```

Source: `src/helia_profiler/fixture_power.py:24`

### helia_profiler.FixturePowerSync.state_gpio_pin

`attribute` · `python`

```python
state_gpio_pin: int
```

Source: `src/helia_profiler/fixture_power.py:25`

### helia_profiler.FixturePowerSync.go_gpio_pin

`attribute` · `python`

```python
go_gpio_pin: int
```

Source: `src/helia_profiler/fixture_power.py:26`

### helia_profiler.FixturePowerSync.protocol_version

`attribute` · `python`

```python
protocol_version: int = 2
```

Source: `src/helia_profiler/fixture_power.py:27`

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

## helia_profiler.hpx_cache_root

`function` · `python`

```python
hpx_cache_root() -> Path
```

Return the root directory for hpx's persistent caches.

Resolved at call time so environment overrides apply per-invocation;
callers must not cache the result at import time.

**API tier:** `experimental`

Source: `src/helia_profiler/hostenv/cache_dirs.py:26`

## helia_profiler.FixtureRuntimeSource

`class` · `python`

```python
FixtureRuntimeSource(name: str, url: str, version: str | None, commit: str, archive_sha256: str) -> None
```

`dataclass`

The prepared runtime archive a build linked: ``helia-rt`` or ``tflite-micro``.

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_sources.py:30`

### helia_profiler.FixtureRuntimeSource.name

`attribute` · `python`

```python
name: str
```

Source: `src/helia_profiler/fixture_sources.py:34`

### helia_profiler.FixtureRuntimeSource.url

`attribute` · `python`

```python
url: str
```

Source: `src/helia_profiler/fixture_sources.py:35`

### helia_profiler.FixtureRuntimeSource.version

`attribute` · `python`

```python
version: str | None
```

Source: `src/helia_profiler/fixture_sources.py:38`

### helia_profiler.FixtureRuntimeSource.commit

`attribute` · `python`

```python
commit: str
```

Source: `src/helia_profiler/fixture_sources.py:39`

### helia_profiler.FixtureRuntimeSource.archive_sha256

`attribute` · `python`

```python
archive_sha256: str
```

Source: `src/helia_profiler/fixture_sources.py:40`

## helia_profiler.CompiledBuild

`class` · `python`

```python
CompiledBuild(plan: FrozenRun, build: FixtureBuild, lifecycle: Lifecycle, binding: str) -> None
```

`dataclass`

A frozen plan and the firmware compiled from it, verifiable on any host.

``binding`` ties the build to the plan and lifecycle it was compiled
from; :func:`build` computes it and :meth:`verify` recomputes it.

**API tier:** `experimental`

Source: `src/helia_profiler/compiled_build.py:39`

### helia_profiler.CompiledBuild.plan

`attribute` · `python`

```python
plan: FrozenRun
```

Source: `src/helia_profiler/compiled_build.py:47`

### helia_profiler.CompiledBuild.build

`attribute` · `python`

```python
build: FixtureBuild
```

Source: `src/helia_profiler/compiled_build.py:48`

### helia_profiler.CompiledBuild.lifecycle

`attribute` · `python`

```python
lifecycle: Lifecycle
```

Source: `src/helia_profiler/compiled_build.py:49`

### helia_profiler.CompiledBuild.binding

`attribute` · `python`

```python
binding: str
```

Source: `src/helia_profiler/compiled_build.py:50`

### helia_profiler.CompiledBuild.identity

`attribute` · `python`

```python
identity: RunIdentity
```

The build's identity, with its plan's ``intent_hash``; ``observation_hash`` is ``None`` until it runs.

Source: `src/helia_profiler/compiled_build.py:53`

### helia_profiler.CompiledBuild.verify

`method` · `python`

```python
verify() -> None
```

Re-check the build against its pinned files and its plan; raise on any mismatch.

The build's files must match their hashes, the plan its intent hash,
and the binding the plan, lifecycle and build. The facts the build
records must be the ones the plan states: engine, board, model,
toolchain and its version, arena and weights placement, arena size,
clock profile and the lifecycle's invocation counts.

The runtime version and commit and the kernel commit are compared
wherever both the plan and the build state one; a build or plan
that states none of a fact is not compared on it. heliaRT and
heliaAOT compare with the plan's pins. LiteRT compares with this HPX's
prepared LiteRT record, because the plan pins the modules an ordinary
profile builds from source, which a fixture build does not link.

**Raises**

| Name | Description |
| --- | --- |
| ConfigError | Naming the first fact that does not match. |

Source: `src/helia_profiler/compiled_build.py:66`

### helia_profiler.CompiledBuild.save

`method` · `python`

```python
save(path: str | Path, *, roots: Mapping[str, Path]) -> Path
```

Write the build as JSON with paths relative to *roots* and return its path.

Source: `src/helia_profiler/compiled_build.py:180`

### helia_profiler.CompiledBuild.load

`method` · `python`

```python
load(path: str | Path, *, roots: Mapping[str, Path]) -> CompiledBuild
```

`classmethod`

Read and verify a build written by :meth:`save`, resolving paths under *roots*.

Source: `src/helia_profiler/compiled_build.py:221`

### helia_profiler.CompiledBuild.run

`method` · `python`

```python
run(
    *,
    guard: FixtureCaptureGuard,
    jlink_serial: str,
    evidence_dir: Path,
    settle_seconds: float,
    expect: Expect | None = None,
    expected_duration_s: float | None = None,
) -> RunResult
```

Flash the recorded image, capture one run and compare outputs when *expect* is given.

A failed capture comes back with ``capture.state == "failure"`` and no
verification; errors raised while capturing propagate.

**Raises**

| Name | Description |
| --- | --- |
| ConfigError | The build does not verify, or *expect* cannot be compared. |
| CaptureError | The device reports counts other than the build's. |

Source: `src/helia_profiler/compiled_build.py:257`

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

## helia_profiler.FixturePsramObservation

`class` · `python`

```python
FixturePsramObservation(base_address: int, size_bytes: int, clock_hz: int, active_addresses: tuple[int, ...]) -> None
```

`dataclass`

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_psram.py:72`

### helia_profiler.FixturePsramObservation.base_address

`attribute` · `python`

```python
base_address: int
```

Source: `src/helia_profiler/fixture_psram.py:74`

### helia_profiler.FixturePsramObservation.size_bytes

`attribute` · `python`

```python
size_bytes: int
```

Source: `src/helia_profiler/fixture_psram.py:75`

### helia_profiler.FixturePsramObservation.clock_hz

`attribute` · `python`

```python
clock_hz: int
```

Source: `src/helia_profiler/fixture_psram.py:76`

### helia_profiler.FixturePsramObservation.active_addresses

`attribute` · `python`

```python
active_addresses: tuple[int, ...]
```

Source: `src/helia_profiler/fixture_psram.py:78`

## helia_profiler.EngineSource

`class` · `python`

```python
EngineSource(package: str, version: str, commit: str | None = None) -> None
```

`dataclass`

Installed engine package: its version and, for a VCS install, the commit.

**API tier:** `experimental`

Source: `src/helia_profiler/engines/base.py:84`

### helia_profiler.EngineSource.package

`attribute` · `python`

```python
package: str
```

Source: `src/helia_profiler/engines/base.py:88`

### helia_profiler.EngineSource.version

`attribute` · `python`

```python
version: str
```

Source: `src/helia_profiler/engines/base.py:89`

### helia_profiler.EngineSource.commit

`attribute` · `python`

```python
commit: str | None = None
```

Source: `src/helia_profiler/engines/base.py:90`

## helia_profiler.FixtureBatchCompletion

`class` · `python`

```python
FixtureBatchCompletion(
    protocol_version: int,
    counter_domain: str,
    sample_rate: int,
    ready_sample: int,
    go_ack_sample: int,
    gate_rise_sample: int,
    gate_fall_sample: int,
    done_sample: int,
    raw: FixtureFile,
) -> None
```

`dataclass`

Observed protocol-v2 transitions on the instrument's native sample counter.

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_power.py:100`

### helia_profiler.FixtureBatchCompletion.protocol_version

`attribute` · `python`

```python
protocol_version: int
```

Source: `src/helia_profiler/fixture_power.py:104`

### helia_profiler.FixtureBatchCompletion.counter_domain

`attribute` · `python`

```python
counter_domain: str
```

Source: `src/helia_profiler/fixture_power.py:105`

### helia_profiler.FixtureBatchCompletion.sample_rate

`attribute` · `python`

```python
sample_rate: int
```

Source: `src/helia_profiler/fixture_power.py:106`

### helia_profiler.FixtureBatchCompletion.ready_sample

`attribute` · `python`

```python
ready_sample: int
```

Source: `src/helia_profiler/fixture_power.py:107`

### helia_profiler.FixtureBatchCompletion.go_ack_sample

`attribute` · `python`

```python
go_ack_sample: int
```

Source: `src/helia_profiler/fixture_power.py:108`

### helia_profiler.FixtureBatchCompletion.gate_rise_sample

`attribute` · `python`

```python
gate_rise_sample: int
```

Source: `src/helia_profiler/fixture_power.py:109`

### helia_profiler.FixtureBatchCompletion.gate_fall_sample

`attribute` · `python`

```python
gate_fall_sample: int
```

Source: `src/helia_profiler/fixture_power.py:110`

### helia_profiler.FixtureBatchCompletion.done_sample

`attribute` · `python`

```python
done_sample: int
```

Source: `src/helia_profiler/fixture_power.py:111`

### helia_profiler.FixtureBatchCompletion.raw

`attribute` · `python`

```python
raw: FixtureFile
```

Source: `src/helia_profiler/fixture_power.py:112`

## helia_profiler.FixtureTiming

`class` · `python`

```python
FixtureTiming(
    ticks: int,
    iterations: int,
    warmups: int,
    timer_hz: int,
    settle_ticks: int,
    cpu_hz: int,
    timing_scope: FixtureTimingScope,
) -> None
```

`dataclass`

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_capture.py:110`

### helia_profiler.FixtureTiming.ticks

`attribute` · `python`

```python
ticks: int
```

Source: `src/helia_profiler/fixture_capture.py:112`

### helia_profiler.FixtureTiming.iterations

`attribute` · `python`

```python
iterations: int
```

Source: `src/helia_profiler/fixture_capture.py:113`

### helia_profiler.FixtureTiming.warmups

`attribute` · `python`

```python
warmups: int
```

Source: `src/helia_profiler/fixture_capture.py:114`

### helia_profiler.FixtureTiming.timer_hz

`attribute` · `python`

```python
timer_hz: int
```

Source: `src/helia_profiler/fixture_capture.py:115`

### helia_profiler.FixtureTiming.settle_ticks

`attribute` · `python`

```python
settle_ticks: int
```

Source: `src/helia_profiler/fixture_capture.py:116`

### helia_profiler.FixtureTiming.cpu_hz

`attribute` · `python`

```python
cpu_hz: int
```

Source: `src/helia_profiler/fixture_capture.py:117`

### helia_profiler.FixtureTiming.timing_scope

`attribute` · `python`

```python
timing_scope: FixtureTimingScope
```

Source: `src/helia_profiler/fixture_capture.py:118`

## helia_profiler.FixtureRailEnergy

`class` · `python`

```python
FixtureRailEnergy(
    iterations: int,
    scope: str,
    rail: str,
    instrument_serial: str,
    subtraction: str,
    duration_s: float,
    mean_power_w: float,
    mean_current_a: float,
    energy_j: float,
    energy_per_iteration_j: float,
    raw: FixtureFile,
    completion: FixtureBatchCompletion | None = None,
) -> None
```

`dataclass`

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_power.py:115`

### helia_profiler.FixtureRailEnergy.iterations

`attribute` · `python`

```python
iterations: int
```

Source: `src/helia_profiler/fixture_power.py:117`

### helia_profiler.FixtureRailEnergy.scope

`attribute` · `python`

```python
scope: str
```

Source: `src/helia_profiler/fixture_power.py:118`

### helia_profiler.FixtureRailEnergy.rail

`attribute` · `python`

```python
rail: str
```

Source: `src/helia_profiler/fixture_power.py:119`

### helia_profiler.FixtureRailEnergy.instrument_serial

`attribute` · `python`

```python
instrument_serial: str
```

Source: `src/helia_profiler/fixture_power.py:120`

### helia_profiler.FixtureRailEnergy.subtraction

`attribute` · `python`

```python
subtraction: str
```

Source: `src/helia_profiler/fixture_power.py:121`

### helia_profiler.FixtureRailEnergy.duration_s

`attribute` · `python`

```python
duration_s: float
```

Source: `src/helia_profiler/fixture_power.py:122`

### helia_profiler.FixtureRailEnergy.mean_power_w

`attribute` · `python`

```python
mean_power_w: float
```

Source: `src/helia_profiler/fixture_power.py:123`

### helia_profiler.FixtureRailEnergy.mean_current_a

`attribute` · `python`

```python
mean_current_a: float
```

Source: `src/helia_profiler/fixture_power.py:124`

### helia_profiler.FixtureRailEnergy.energy_j

`attribute` · `python`

```python
energy_j: float
```

Source: `src/helia_profiler/fixture_power.py:125`

### helia_profiler.FixtureRailEnergy.energy_per_iteration_j

`attribute` · `python`

```python
energy_per_iteration_j: float
```

Source: `src/helia_profiler/fixture_power.py:126`

### helia_profiler.FixtureRailEnergy.raw

`attribute` · `python`

```python
raw: FixtureFile
```

Source: `src/helia_profiler/fixture_power.py:127`

### helia_profiler.FixtureRailEnergy.completion

`attribute` · `python`

```python
completion: FixtureBatchCompletion | None = None
```

Source: `src/helia_profiler/fixture_power.py:128`

## helia_profiler.FixtureMemory

`class` · `python`

```python
FixtureMemory(capacity: int, after_io_access: int, after_warmup: int, after_invoke: int) -> None
```

`dataclass`

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_capture.py:121`

### helia_profiler.FixtureMemory.capacity

`attribute` · `python`

```python
capacity: int
```

Source: `src/helia_profiler/fixture_capture.py:123`

### helia_profiler.FixtureMemory.after_io_access

`attribute` · `python`

```python
after_io_access: int
```

Source: `src/helia_profiler/fixture_capture.py:124`

### helia_profiler.FixtureMemory.after_warmup

`attribute` · `python`

```python
after_warmup: int
```

Source: `src/helia_profiler/fixture_capture.py:125`

### helia_profiler.FixtureMemory.after_invoke

`attribute` · `python`

```python
after_invoke: int
```

Source: `src/helia_profiler/fixture_capture.py:126`

## helia_profiler.FixtureCaptureResult

`class` · `python`

```python
FixtureCaptureResult(
    state: str,
    timing_scope: FixtureTimingScope,
    output: FixtureFile | None = None,
    status: int | None = None,
    checksum: int | None = None,
    timing: FixtureTiming | None = None,
    memory: FixtureMemory | None = None,
    artifacts: tuple[FixtureFile, ...] = (),
    error: str | None = None,
    outputs: tuple[FixtureFile, ...] = (),
    arena_scan: tuple[tuple[int, int], ...] = (),
    psram_weights: FixturePsramObservation | None = None,
    rail_energy: FixtureRailEnergy | None = None,
) -> None
```

`dataclass`

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_capture.py:129`

### helia_profiler.FixtureCaptureResult.state

`attribute` · `python`

```python
state: str
```

Source: `src/helia_profiler/fixture_capture.py:131`

### helia_profiler.FixtureCaptureResult.timing_scope

`attribute` · `python`

```python
timing_scope: FixtureTimingScope
```

Source: `src/helia_profiler/fixture_capture.py:132`

### helia_profiler.FixtureCaptureResult.output

`attribute` · `python`

```python
output: FixtureFile | None = None
```

Source: `src/helia_profiler/fixture_capture.py:133`

### helia_profiler.FixtureCaptureResult.status

`attribute` · `python`

```python
status: int | None = None
```

Source: `src/helia_profiler/fixture_capture.py:134`

### helia_profiler.FixtureCaptureResult.checksum

`attribute` · `python`

```python
checksum: int | None = None
```

Source: `src/helia_profiler/fixture_capture.py:135`

### helia_profiler.FixtureCaptureResult.timing

`attribute` · `python`

```python
timing: FixtureTiming | None = None
```

Source: `src/helia_profiler/fixture_capture.py:136`

### helia_profiler.FixtureCaptureResult.memory

`attribute` · `python`

```python
memory: FixtureMemory | None = None
```

Source: `src/helia_profiler/fixture_capture.py:137`

### helia_profiler.FixtureCaptureResult.artifacts

`attribute` · `python`

```python
artifacts: tuple[FixtureFile, ...] = ()
```

Source: `src/helia_profiler/fixture_capture.py:138`

### helia_profiler.FixtureCaptureResult.error

`attribute` · `python`

```python
error: str | None = None
```

Source: `src/helia_profiler/fixture_capture.py:139`

### helia_profiler.FixtureCaptureResult.outputs

`attribute` · `python`

```python
outputs: tuple[FixtureFile, ...] = ()
```

Source: `src/helia_profiler/fixture_capture.py:141`

### helia_profiler.FixtureCaptureResult.arena_scan

`attribute` · `python`

```python
arena_scan: tuple[tuple[int, int], ...] = ()
```

Source: `src/helia_profiler/fixture_capture.py:144`

### helia_profiler.FixtureCaptureResult.psram_weights

`attribute` · `python`

```python
psram_weights: FixturePsramObservation | None = None
```

Source: `src/helia_profiler/fixture_capture.py:145`

### helia_profiler.FixtureCaptureResult.rail_energy

`attribute` · `python`

```python
rail_energy: FixtureRailEnergy | None = None
```

Source: `src/helia_profiler/fixture_capture.py:146`

## helia_profiler.ToolchainInfo

`class` · `python`

```python
ToolchainInfo(compiler: str = '', compiler_version: str = '', cmake_version: str = '') -> None
```

`dataclass`

Build toolchain versions (populated by stage 4).

**API tier:** `experimental`

Source: `src/helia_profiler/results/models.py:253`

### helia_profiler.ToolchainInfo.compiler

`attribute` · `python`

```python
compiler: str = ''
```

Source: `src/helia_profiler/results/models.py:257`

### helia_profiler.ToolchainInfo.compiler_version

`attribute` · `python`

```python
compiler_version: str = ''
```

Source: `src/helia_profiler/results/models.py:258`

### helia_profiler.ToolchainInfo.cmake_version

`attribute` · `python`

```python
cmake_version: str = ''
```

Source: `src/helia_profiler/results/models.py:259`

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

Source: `src/helia_profiler/_fixture_build.py:288`

### helia_profiler.FixtureBuild.fixture_identity

`attribute` · `python`

```python
fixture_identity: str
```

Source: `src/helia_profiler/_fixture_build.py:290`

### helia_profiler.FixtureBuild.app_dir

`attribute` · `python`

```python
app_dir: Path
```

Source: `src/helia_profiler/_fixture_build.py:291`

### helia_profiler.FixtureBuild.binary

`attribute` · `python`

```python
binary: FixtureFile | None
```

Source: `src/helia_profiler/_fixture_build.py:292`

### helia_profiler.FixtureBuild.generated_sources

`attribute` · `python`

```python
generated_sources: tuple[FixtureFile, ...]
```

Source: `src/helia_profiler/_fixture_build.py:293`

### helia_profiler.FixtureBuild.expected

`attribute` · `python`

```python
expected: FixtureFile | None
```

Source: `src/helia_profiler/_fixture_build.py:294`

### helia_profiler.FixtureBuild.built

`attribute` · `python`

```python
built: bool
```

Source: `src/helia_profiler/_fixture_build.py:295`

### helia_profiler.FixtureBuild.runtime_manifest

`attribute` · `python`

```python
runtime_manifest: FixtureFile | None
```

Source: `src/helia_profiler/_fixture_build.py:296`

### helia_profiler.FixtureBuild.flat_binary

`attribute` · `python`

```python
flat_binary: FixtureFile | None
```

Source: `src/helia_profiler/_fixture_build.py:297`

### helia_profiler.FixtureBuild.dependency_lock

`attribute` · `python`

```python
dependency_lock: FixtureFile | None
```

Source: `src/helia_profiler/_fixture_build.py:298`

### helia_profiler.FixtureBuild.link_map

`attribute` · `python`

```python
link_map: FixtureFile | None
```

Source: `src/helia_profiler/_fixture_build.py:299`

### helia_profiler.FixtureBuild.timing_scope

`attribute` · `python`

```python
timing_scope: FixtureTimingScope
```

Source: `src/helia_profiler/_fixture_build.py:300`

### helia_profiler.FixtureBuild.engine

`attribute` · `python`

```python
engine: EngineType
```

Source: `src/helia_profiler/_fixture_build.py:301`

### helia_profiler.FixtureBuild.build_identity

`attribute` · `python`

```python
build_identity: str | None
```

Source: `src/helia_profiler/_fixture_build.py:302`

### helia_profiler.FixtureBuild.iterations

`attribute` · `python`

```python
iterations: int
```

Source: `src/helia_profiler/_fixture_build.py:303`

### helia_profiler.FixtureBuild.warmups

`attribute` · `python`

```python
warmups: int
```

Source: `src/helia_profiler/_fixture_build.py:304`

### helia_profiler.FixtureBuild.intent_identity

`attribute` · `python`

```python
intent_identity: str
```

Source: `src/helia_profiler/_fixture_build.py:305`

### helia_profiler.FixtureBuild.target

`attribute` · `python`

```python
target: FixtureTarget
```

Source: `src/helia_profiler/_fixture_build.py:306`

### helia_profiler.FixtureBuild.toolchain

`attribute` · `python`

```python
toolchain: ToolchainInfo | None
```

Source: `src/helia_profiler/_fixture_build.py:307`

### helia_profiler.FixtureBuild.provider_provenance

`attribute` · `python`

```python
provider_provenance: str = 'manifest-declared; independently audit the pinned build/source record'
```

Source: `src/helia_profiler/_fixture_build.py:308`

### helia_profiler.FixtureBuild.planned_memory

`attribute` · `python`

```python
planned_memory: MemoryPlan | None = None
```

Source: `src/helia_profiler/_fixture_build.py:311`

### helia_profiler.FixtureBuild.planned_memory_reason

`attribute` · `python`

```python
planned_memory_reason: str | None = 'not_recorded'
```

Source: `src/helia_profiler/_fixture_build.py:312`

### helia_profiler.FixtureBuild.outputs

`attribute` · `python`

```python
outputs: tuple[FixtureFile, ...] = ()
```

Source: `src/helia_profiler/_fixture_build.py:314`

### helia_profiler.FixtureBuild.capabilities

`attribute` · `python`

```python
capabilities: tuple[tuple[str, FixtureCapability], ...] = ()
```

Source: `src/helia_profiler/_fixture_build.py:316`

### helia_profiler.FixtureBuild.aot_arena_scan

`attribute` · `python`

```python
aot_arena_scan: tuple[tuple[int, int], ...] = ()
```

Source: `src/helia_profiler/_fixture_build.py:318`

### helia_profiler.FixtureBuild.aot_outputs

`attribute` · `python`

```python
aot_outputs: tuple[FixtureFile, ...] = ()
```

Source: `src/helia_profiler/_fixture_build.py:321`

### helia_profiler.FixtureBuild.engine_source

`attribute` · `python`

```python
engine_source: EngineSource | None = None
```

Source: `src/helia_profiler/_fixture_build.py:323`

### helia_profiler.FixtureBuild.dependency_state

`attribute` · `python`

```python
dependency_state: FixtureFile | None = None
```

Source: `src/helia_profiler/_fixture_build.py:325`

### helia_profiler.FixtureBuild.state_policy

`attribute` · `python`

```python
state_policy: FixtureStatePolicy | None = None
```

Source: `src/helia_profiler/_fixture_build.py:326`

### helia_profiler.FixtureBuild.source_initializer_present

`attribute` · `python`

```python
source_initializer_present: bool = False
```

Source: `src/helia_profiler/_fixture_build.py:327`

### helia_profiler.FixtureBuild.psram_weights

`attribute` · `python`

```python
psram_weights: FixturePsramWeights | None = None
```

Source: `src/helia_profiler/_fixture_build.py:329`

### helia_profiler.FixtureBuild.arena_location

`attribute` · `python`

```python
arena_location: Placement | None = None
```

Source: `src/helia_profiler/_fixture_build.py:331`

### helia_profiler.FixtureBuild.weights_location

`attribute` · `python`

```python
weights_location: Placement | None = None
```

Source: `src/helia_profiler/_fixture_build.py:332`

### helia_profiler.FixtureBuild.model

`attribute` · `python`

```python
model: FixtureFile | None = None
```

Source: `src/helia_profiler/_fixture_build.py:334`

### helia_profiler.FixtureBuild.inputs

`attribute` · `python`

```python
inputs: tuple[FixtureIO, ...] = ()
```

Source: `src/helia_profiler/_fixture_build.py:335`

### helia_profiler.FixtureBuild.output_tensors

`attribute` · `python`

```python
output_tensors: tuple[FixtureTensor, ...] = ()
```

Source: `src/helia_profiler/_fixture_build.py:336`

### helia_profiler.FixtureBuild.arena_capacity

`attribute` · `python`

```python
arena_capacity: int | None = None
```

Source: `src/helia_profiler/_fixture_build.py:337`

### helia_profiler.FixtureBuild.clock_profile

`attribute` · `python`

```python
clock_profile: str | None = None
```

Source: `src/helia_profiler/_fixture_build.py:338`

### helia_profiler.FixtureBuild.cpu_hz

`attribute` · `python`

```python
cpu_hz: int | None = None
```

Source: `src/helia_profiler/_fixture_build.py:339`

### helia_profiler.FixtureBuild.timer_hz

`attribute` · `python`

```python
timer_hz: int | None = None
```

Source: `src/helia_profiler/_fixture_build.py:340`

### helia_profiler.FixtureBuild.power_sync

`attribute` · `python`

```python
power_sync: FixturePowerSync | None = None
```

Source: `src/helia_profiler/_fixture_build.py:341`

### helia_profiler.FixtureBuild.observe_ram

`attribute` · `python`

```python
observe_ram: bool = False
```

Source: `src/helia_profiler/_fixture_build.py:342`

### helia_profiler.FixtureBuild.kernel_source

`attribute` · `python`

```python
kernel_source: FixtureKernelSource | None = None
```

Source: `src/helia_profiler/_fixture_build.py:345`

### helia_profiler.FixtureBuild.runtime_source

`attribute` · `python`

```python
runtime_source: FixtureRuntimeSource | None = None
```

Source: `src/helia_profiler/_fixture_build.py:347`

## helia_profiler.RunResult

`class` · `python`

```python
RunResult(
    compiled: CompiledBuild,
    capture: FixtureCaptureResult,
    identity: RunIdentity,
    verification: Verification | None,
) -> None
```

`dataclass`

One capture from a :class:`CompiledBuild`.

**API tier:** `experimental`

Source: `src/helia_profiler/compiled_build.py:320`

### helia_profiler.RunResult.compiled

`attribute` · `python`

```python
compiled: CompiledBuild
```

Source: `src/helia_profiler/compiled_build.py:324`

### helia_profiler.RunResult.capture

`attribute` · `python`

```python
capture: FixtureCaptureResult
```

Source: `src/helia_profiler/compiled_build.py:325`

### helia_profiler.RunResult.identity

`attribute` · `python`

```python
identity: RunIdentity
```

Source: `src/helia_profiler/compiled_build.py:326`

### helia_profiler.RunResult.verification

`attribute` · `python`

```python
verification: Verification | None
```

Source: `src/helia_profiler/compiled_build.py:327`

### helia_profiler.RunResult.succeeded

`attribute` · `python`

```python
succeeded: bool
```

True only for a ``success`` capture.

Source: `src/helia_profiler/compiled_build.py:330`

## helia_profiler.build

`function` · `python`

```python
build(
    plan: FrozenRun,
    fixture: TypedFixture,
    *,
    method: FixtureMethod,
    lifecycle: Lifecycle = Lifecycle.CARRY,
    runtime: PreparedUpstreamRuntime | None = None,
    model: str | Path | None = None,
    host: Mapping[str, object] | None = None,
    observe_aot_arenas: bool = False,
    observe_ram: bool = False,
) -> CompiledBuild
```

Compile *plan* for *fixture*'s pinned inputs and return the compiled build.

*lifecycle* must be one the plan's engine implements; it sets the timed
iterations and warmups the firmware runs. *host* supplies host-local
settings, such as ``{"work_dir": ...}``, as :meth:`FrozenRun.config` does.
heliaRT and LiteRT link a prepared runtime: without *runtime*, the one the
plan names is taken from the HPX cache (``hpx runtimes prepare``), never
downloaded or built. The result is verified before it is returned.

**API tier:** `experimental`

Source: `src/helia_profiler/compiled_build.py:335`

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

Source: `src/helia_profiler/results/models.py:442`

### helia_profiler.MemoryPlan.engine

`attribute` · `python`

```python
engine: EngineType
```

Source: `src/helia_profiler/results/models.py:452`

### helia_profiler.MemoryPlan.regions

`attribute` · `python`

```python
regions: tuple[MemoryRegionUsage, ...] = ()
```

Source: `src/helia_profiler/results/models.py:453`

### helia_profiler.MemoryPlan.model_weight_bytes

`attribute` · `python`

```python
model_weight_bytes: int = 0
```

Source: `src/helia_profiler/results/models.py:455`

### helia_profiler.MemoryPlan.has_overflow

`attribute` · `python`

```python
has_overflow: bool = False
```

Source: `src/helia_profiler/results/models.py:459`

### helia_profiler.MemoryPlan.region

`method` · `python`

```python
region(name: str | MemoryRegion) -> MemoryRegionUsage | None
```

Source: `src/helia_profiler/results/models.py:465`

## helia_profiler.load_build

`function` · `python`

```python
load_build(path: str | Path, *, roots: Mapping[str, Path]) -> CompiledBuild
```

Read and verify a compiled build written by :meth:`CompiledBuild.save`.

**API tier:** `experimental`

Source: `src/helia_profiler/compiled_build.py:494`

## helia_profiler.run_build

`function` · `python`

```python
run_build(
    compiled: CompiledBuild,
    *,
    guard: FixtureCaptureGuard,
    jlink_serial: str,
    evidence_dir: Path,
    settle_seconds: float,
    expect: Expect | None = None,
    expected_duration_s: float | None = None,
) -> RunResult
```

Flash *compiled*'s image, capture one run and compare outputs; see :meth:`CompiledBuild.run`.

**API tier:** `experimental`

Source: `src/helia_profiler/compiled_build.py:499`
