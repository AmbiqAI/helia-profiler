# helia_profiler.fixture_capture

What a run captures from the board: the capture result, its timing and memory, and the power and PSRAM observations a capture can carry.

Every name on this page is imported from `helia_profiler`.

**API tier:** `experimental`

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__`.

## helia_profiler.FixturePowerSync

`class` · `python`

```python
FixturePowerSync(sync_gpio_pin: int, state_gpio_pin: int, go_gpio_pin: int, protocol_version: int = 2) -> None
```

`dataclass`

Explicit firmware GPIO wiring; constructing this never opens hardware.

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_power.py:25`

### helia_profiler.FixturePowerSync.sync_gpio_pin

`attribute` · `python`

```python
sync_gpio_pin: int
```

Source: `src/helia_profiler/fixture_power.py:29`

### helia_profiler.FixturePowerSync.state_gpio_pin

`attribute` · `python`

```python
state_gpio_pin: int
```

Source: `src/helia_profiler/fixture_power.py:30`

### helia_profiler.FixturePowerSync.go_gpio_pin

`attribute` · `python`

```python
go_gpio_pin: int
```

Source: `src/helia_profiler/fixture_power.py:31`

### helia_profiler.FixturePowerSync.protocol_version

`attribute` · `python`

```python
protocol_version: int = 2
```

Source: `src/helia_profiler/fixture_power.py:32`

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

Source: `src/helia_profiler/fixture_power.py:105`

### helia_profiler.FixtureBatchCompletion.protocol_version

`attribute` · `python`

```python
protocol_version: int
```

Source: `src/helia_profiler/fixture_power.py:109`

### helia_profiler.FixtureBatchCompletion.counter_domain

`attribute` · `python`

```python
counter_domain: str
```

Source: `src/helia_profiler/fixture_power.py:110`

### helia_profiler.FixtureBatchCompletion.sample_rate

`attribute` · `python`

```python
sample_rate: int
```

Source: `src/helia_profiler/fixture_power.py:111`

### helia_profiler.FixtureBatchCompletion.ready_sample

`attribute` · `python`

```python
ready_sample: int
```

Source: `src/helia_profiler/fixture_power.py:112`

### helia_profiler.FixtureBatchCompletion.go_ack_sample

`attribute` · `python`

```python
go_ack_sample: int
```

Source: `src/helia_profiler/fixture_power.py:113`

### helia_profiler.FixtureBatchCompletion.gate_rise_sample

`attribute` · `python`

```python
gate_rise_sample: int
```

Source: `src/helia_profiler/fixture_power.py:114`

### helia_profiler.FixtureBatchCompletion.gate_fall_sample

`attribute` · `python`

```python
gate_fall_sample: int
```

Source: `src/helia_profiler/fixture_power.py:115`

### helia_profiler.FixtureBatchCompletion.done_sample

`attribute` · `python`

```python
done_sample: int
```

Source: `src/helia_profiler/fixture_power.py:116`

### helia_profiler.FixtureBatchCompletion.raw

`attribute` · `python`

```python
raw: FixtureFile
```

Source: `src/helia_profiler/fixture_power.py:117`

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

Source: `src/helia_profiler/fixture_power.py:120`

### helia_profiler.FixtureRailEnergy.iterations

`attribute` · `python`

```python
iterations: int
```

Source: `src/helia_profiler/fixture_power.py:122`

### helia_profiler.FixtureRailEnergy.scope

`attribute` · `python`

```python
scope: str
```

Source: `src/helia_profiler/fixture_power.py:123`

### helia_profiler.FixtureRailEnergy.rail

`attribute` · `python`

```python
rail: str
```

Source: `src/helia_profiler/fixture_power.py:124`

### helia_profiler.FixtureRailEnergy.instrument_serial

`attribute` · `python`

```python
instrument_serial: str
```

Source: `src/helia_profiler/fixture_power.py:125`

### helia_profiler.FixtureRailEnergy.subtraction

`attribute` · `python`

```python
subtraction: str
```

Source: `src/helia_profiler/fixture_power.py:126`

### helia_profiler.FixtureRailEnergy.duration_s

`attribute` · `python`

```python
duration_s: float
```

Source: `src/helia_profiler/fixture_power.py:127`

### helia_profiler.FixtureRailEnergy.mean_power_w

`attribute` · `python`

```python
mean_power_w: float
```

Source: `src/helia_profiler/fixture_power.py:128`

### helia_profiler.FixtureRailEnergy.mean_current_a

`attribute` · `python`

```python
mean_current_a: float
```

Source: `src/helia_profiler/fixture_power.py:129`

### helia_profiler.FixtureRailEnergy.energy_j

`attribute` · `python`

```python
energy_j: float
```

Source: `src/helia_profiler/fixture_power.py:130`

### helia_profiler.FixtureRailEnergy.energy_per_iteration_j

`attribute` · `python`

```python
energy_per_iteration_j: float
```

Source: `src/helia_profiler/fixture_power.py:131`

### helia_profiler.FixtureRailEnergy.raw

`attribute` · `python`

```python
raw: FixtureFile
```

Source: `src/helia_profiler/fixture_power.py:132`

### helia_profiler.FixtureRailEnergy.completion

`attribute` · `python`

```python
completion: FixtureBatchCompletion | None = None
```

Source: `src/helia_profiler/fixture_power.py:133`

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
