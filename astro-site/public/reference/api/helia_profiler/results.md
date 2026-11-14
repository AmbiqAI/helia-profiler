# helia_profiler.results

What a profiling run produces: the run result, its per-layer and PMU detail, and the firmware and run metadata that say how it was measured.

Every name on this page is imported from `helia_profiler`.

**API tier:** `stable`, `experimental`

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__`.

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
layer corresponds to, when the op label carries one. ``id`` is
the firmware's execution position, which on AOT engines is NOT the
original index — helia-aot skips ops without renumbering.

**API tier:** `stable`

Source: `src/helia_profiler/results/models.py:51`

### helia_profiler.LayerResult.id

`attribute` · `python`

```python
id: int | str
```

Source: `src/helia_profiler/results/models.py:61`

### helia_profiler.LayerResult.op

`attribute` · `python`

```python
op: str
```

Source: `src/helia_profiler/results/models.py:62`

### helia_profiler.LayerResult.counters

`attribute` · `python`

```python
counters: dict[str, float] = field(default_factory=dict)
```

Source: `src/helia_profiler/results/models.py:63`

### helia_profiler.LayerResult.cycles

`attribute` · `python`

```python
cycles: float | None = None
```

Source: `src/helia_profiler/results/models.py:64`

### helia_profiler.LayerResult.overflow

`attribute` · `python`

```python
overflow: bool = False
```

Source: `src/helia_profiler/results/models.py:65`

### helia_profiler.LayerResult.source_index

`attribute` · `python`

```python
source_index: int | None = None
```

Source: `src/helia_profiler/results/models.py:66`

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

Source: `src/helia_profiler/results/models.py:87`

### helia_profiler.PresetResult.name

`attribute` · `python`

```python
name: str
```

Source: `src/helia_profiler/results/models.py:91`

### helia_profiler.PresetResult.header

`attribute` · `python`

```python
header: list[str] = field(default_factory=list)
```

Source: `src/helia_profiler/results/models.py:92`

### helia_profiler.PresetResult.iterations

`attribute` · `python`

```python
iterations: list[list[LayerResult]] = field(default_factory=list)
```

Source: `src/helia_profiler/results/models.py:93`

### helia_profiler.PresetResult.layers

`attribute` · `python`

```python
layers: list[LayerResult] = field(default_factory=list)
```

Source: `src/helia_profiler/results/models.py:94`

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
    measured_clock_hz: int | None = None,
    dcache: str | None = None,
    per_layer_profiling: bool | None = None,
) -> None
```

`dataclass`

Metadata reported by the profiler firmware at startup.

All fields are optional because older firmware versions may not report
every field.

**API tier:** `stable`

Source: `src/helia_profiler/results/models.py:118`

### helia_profiler.FirmwareMeta.model_size

`attribute` · `python`

```python
model_size: int | None = None
```

Source: `src/helia_profiler/results/models.py:126`

### helia_profiler.FirmwareMeta.arena_size

`attribute` · `python`

```python
arena_size: int | None = None
```

Source: `src/helia_profiler/results/models.py:127`

### helia_profiler.FirmwareMeta.allocated_arena

`attribute` · `python`

```python
allocated_arena: int | None = None
```

Source: `src/helia_profiler/results/models.py:128`

### helia_profiler.FirmwareMeta.input_size

`attribute` · `python`

```python
input_size: int | None = None
```

Source: `src/helia_profiler/results/models.py:129`

### helia_profiler.FirmwareMeta.output_size

`attribute` · `python`

```python
output_size: int | None = None
```

Source: `src/helia_profiler/results/models.py:130`

### helia_profiler.FirmwareMeta.num_tensors

`attribute` · `python`

```python
num_tensors: int | None = None
```

Source: `src/helia_profiler/results/models.py:131`

### helia_profiler.FirmwareMeta.num_inputs

`attribute` · `python`

```python
num_inputs: int | None = None
```

Source: `src/helia_profiler/results/models.py:132`

### helia_profiler.FirmwareMeta.num_outputs

`attribute` · `python`

```python
num_outputs: int | None = None
```

Source: `src/helia_profiler/results/models.py:133`

### helia_profiler.FirmwareMeta.num_presets

`attribute` · `python`

```python
num_presets: int | None = None
```

Source: `src/helia_profiler/results/models.py:134`

### helia_profiler.FirmwareMeta.system_clock_hz

`attribute` · `python`

```python
system_clock_hz: int | None = None
```

Source: `src/helia_profiler/results/models.py:136`

### helia_profiler.FirmwareMeta.profiled_infer_count

`attribute` · `python`

```python
profiled_infer_count: int | None = None
```

Source: `src/helia_profiler/results/models.py:137`

### helia_profiler.FirmwareMeta.profiled_infer_total_us

`attribute` · `python`

```python
profiled_infer_total_us: int | None = None
```

Source: `src/helia_profiler/results/models.py:138`

### helia_profiler.FirmwareMeta.profiled_infer_avg_us

`attribute` · `python`

```python
profiled_infer_avg_us: int | None = None
```

Source: `src/helia_profiler/results/models.py:139`

### helia_profiler.FirmwareMeta.clean_infer_count

`attribute` · `python`

```python
clean_infer_count: int | None = None
```

Source: `src/helia_profiler/results/models.py:144`

### helia_profiler.FirmwareMeta.clean_infer_total_cycles

`attribute` · `python`

```python
clean_infer_total_cycles: int | None = None
```

Source: `src/helia_profiler/results/models.py:145`

### helia_profiler.FirmwareMeta.clean_infer_avg_cycles

`attribute` · `python`

```python
clean_infer_avg_cycles: int | None = None
```

Source: `src/helia_profiler/results/models.py:146`

### helia_profiler.FirmwareMeta.clean_infer_avg_us

`attribute` · `python`

```python
clean_infer_avg_us: int | None = None
```

Source: `src/helia_profiler/results/models.py:147`

### helia_profiler.FirmwareMeta.clean_stalled_iters

`attribute` · `python`

```python
clean_stalled_iters: int | None = None
```

Source: `src/helia_profiler/results/models.py:152`

### helia_profiler.FirmwareMeta.clean_partial_iters

`attribute` · `python`

```python
clean_partial_iters: int | None = None
```

Source: `src/helia_profiler/results/models.py:157`

### helia_profiler.FirmwareMeta.clean_ref_cycles

`attribute` · `python`

```python
clean_ref_cycles: int | None = None
```

Source: `src/helia_profiler/results/models.py:167`

### helia_profiler.FirmwareMeta.clean_dwt_rate_cyc

`attribute` · `python`

```python
clean_dwt_rate_cyc: int | None = None
```

Source: `src/helia_profiler/results/models.py:173`

### helia_profiler.FirmwareMeta.clean_dwt_rate_us

`attribute` · `python`

```python
clean_dwt_rate_us: int | None = None
```

Source: `src/helia_profiler/results/models.py:174`

### helia_profiler.FirmwareMeta.clean_attach_wait_us

`attribute` · `python`

```python
clean_attach_wait_us: int | None = None
```

Source: `src/helia_profiler/results/models.py:179`

### helia_profiler.FirmwareMeta.psram

`attribute` · `python`

```python
psram: PsramInfo | None = None
```

Source: `src/helia_profiler/results/models.py:180`

### helia_profiler.FirmwareMeta.presets

`attribute` · `python`

```python
presets: tuple[str, ...] = ()
```

Source: `src/helia_profiler/results/models.py:181`

### helia_profiler.FirmwareMeta.measured_clock_hz

`attribute` · `python`

```python
measured_clock_hz: int | None = None
```

Source: `src/helia_profiler/results/models.py:183`

### helia_profiler.FirmwareMeta.dcache

`attribute` · `python`

```python
dcache: str | None = None
```

Source: `src/helia_profiler/results/models.py:185`

### helia_profiler.FirmwareMeta.per_layer_profiling

`attribute` · `python`

```python
per_layer_profiling: bool | None = None
```

Source: `src/helia_profiler/results/models.py:187`

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
what the device actually said.

Source: `src/helia_profiler/results/models.py:190`

## helia_profiler.Conditions

`class` · `python`

```python
Conditions(
    target: str,
    cpu_hz: int | None = None,
    weights_memory: str | None = None,
    weights_cache: str = 'on',
    external_memory_clock: int | None = None,
    application_load: str = 'none',
) -> None
```

`dataclass`

The board conditions a run's measurements refer to.

Derived once, when the report is written, from the frozen configuration
and the capture; readers take the recorded facts rather than deriving
them again. The field names match heliaOptimus's ``Conditions`` so a
calibration table written from a run prices a plan made under the same
conditions.

**API tier:** `experimental`

Source: `src/helia_profiler/results/models.py:205`

### helia_profiler.Conditions.target

`attribute` · `python`

```python
target: str
```

heliaAOT's canonical name for the board profiled.

Source: `src/helia_profiler/results/models.py:230`

### helia_profiler.Conditions.cpu_hz

`attribute` · `python`

```python
cpu_hz: int | None = None
```

The configured core clock. The on-device measurement
jitters from run to run and stays in ``firmware.measured_clock_hz``.

Source: `src/helia_profiler/results/models.py:231`

### helia_profiler.Conditions.weights_memory

`attribute` · `python`

```python
weights_memory: str | None = None
```

The memory hpx placed the model's constants in, as a
lowercase heliaAOT memory type (``dtcm``, ``sram``, ``mram``,
``psram``); ``None`` when hpx set no placement rule for them.

Source: `src/helia_profiler/results/models.py:232`

### helia_profiler.Conditions.weights_cache

`attribute` · `python`

```python
weights_cache: str = 'on'
```

The reported data-cache policy, ``on`` or ``off``.
Missing cache metadata in older firmware retains the ``on`` baseline.

Source: `src/helia_profiler/results/models.py:233`

### helia_profiler.Conditions.external_memory_clock

`attribute` · `python`

```python
external_memory_clock: int | None = None
```

The PSRAM interface clock in MHz; ``None``
when the run used no PSRAM.

Source: `src/helia_profiler/results/models.py:234`

### helia_profiler.Conditions.application_load

`attribute` · `python`

```python
application_load: str = 'none'
```

What else the core ran during the capture; ``none``
for every run this release makes.

Source: `src/helia_profiler/results/models.py:235`

### helia_profiler.Conditions.to_dict

`method` · `python`

```python
to_dict() -> dict[str, Any]
```

JSON-ready form; ``external_memory_clock`` is absent without PSRAM.

Source: `src/helia_profiler/results/models.py:237`

### helia_profiler.Conditions.from_dict

`method` · `python`

```python
from_dict(data: Mapping[str, Any]) -> Conditions
```

`classmethod`

Read a recorded ``conditions`` block.

**Raises**

| Name | Description |
| --- | --- |
| ValueError | when a field is missing or has the wrong type. |

Source: `src/helia_profiler/results/models.py:250`

## helia_profiler.PmuResult

`class` · `python`

```python
PmuResult(
    meta: FirmwareMeta,
    presets: dict[str, PresetResult] = dict(),
    layers: list[LayerResult] = list(),
    overflow_detected: bool = False,
    groups: dict[str, list[LayerResult]] = dict(),
    raw_output: tuple[str, ...] = (),
    transfer_evidence: tuple[TransferEvidence, ...] = (),
) -> None
```

`dataclass`

Complete PMU profiling result across all presets.

**API tier:** `stable`

Source: `src/helia_profiler/results/models.py:283`

### helia_profiler.PmuResult.meta

`attribute` · `python`

```python
meta: FirmwareMeta
```

Source: `src/helia_profiler/results/models.py:287`

### helia_profiler.PmuResult.presets

`attribute` · `python`

```python
presets: dict[str, PresetResult] = field(default_factory=dict)
```

Source: `src/helia_profiler/results/models.py:288`

### helia_profiler.PmuResult.layers

`attribute` · `python`

```python
layers: list[LayerResult] = field(default_factory=list)
```

Source: `src/helia_profiler/results/models.py:289`

### helia_profiler.PmuResult.overflow_detected

`attribute` · `python`

```python
overflow_detected: bool = False
```

Source: `src/helia_profiler/results/models.py:290`

### helia_profiler.PmuResult.groups

`attribute` · `python`

```python
groups: dict[str, list[LayerResult]] = field(default_factory=dict)
```

Source: `src/helia_profiler/results/models.py:295`

### helia_profiler.PmuResult.raw_output

`attribute` · `python`

```python
raw_output: tuple[str, ...] = ()
```

Source: `src/helia_profiler/results/models.py:297`

### helia_profiler.PmuResult.transfer_evidence

`attribute` · `python`

```python
transfer_evidence: tuple[TransferEvidence, ...] = ()
```

Source: `src/helia_profiler/results/models.py:298`

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
    evaluation: RunEvaluation | None = None,
) -> None
```

`dataclass`

Complete profiling result — the public return type of ``hpx.profile()``.

Carries PMU data, optional power data, run metadata, report paths, and the
stored report evaluation. ``evaluation=None`` means no evaluation was
supplied, not a valid verdict. The evaluation payload retains the
experimental :class:`RunEvaluation` contract.

This in-memory result does not define a saved-record schema.

**API tier:** `stable`

Source: `src/helia_profiler/results/models.py:723`

### helia_profiler.ProfileResult.pmu

`attribute` · `python`

```python
pmu: PmuResult
```

Source: `src/helia_profiler/results/models.py:735`

### helia_profiler.ProfileResult.power

`attribute` · `python`

```python
power: PowerResult | None = None
```

Source: `src/helia_profiler/results/models.py:736`

### helia_profiler.ProfileResult.power_observation

`attribute` · `python`

```python
power_observation: PowerObservation | None = None
```

Source: `src/helia_profiler/results/models.py:737`

### helia_profiler.ProfileResult.power_terminal

`attribute` · `python`

```python
power_terminal: PowerTerminalRecord | None = None
```

Source: `src/helia_profiler/results/models.py:738`

### helia_profiler.ProfileResult.on_device_power

`attribute` · `python`

```python
on_device_power: OnDevicePowerSummary | None = None
```

Source: `src/helia_profiler/results/models.py:739`

### helia_profiler.ProfileResult.metadata

`attribute` · `python`

```python
metadata: RunMetadata = field(default_factory=RunMetadata)
```

Source: `src/helia_profiler/results/models.py:740`

### helia_profiler.ProfileResult.report_paths

`attribute` · `python`

```python
report_paths: list[Path] = field(default_factory=list)
```

Source: `src/helia_profiler/results/models.py:741`

### helia_profiler.ProfileResult.evaluation

`attribute` · `python`

```python
evaluation: RunEvaluation | None = None
```

Source: `src/helia_profiler/results/models.py:742`

### helia_profiler.ProfileResult.layers

`attribute` · `python`

```python
layers: list[LayerResult]
```

Merged per-layer results across all PMU presets.

Source: `src/helia_profiler/results/models.py:745`

### helia_profiler.ProfileResult.total_cycles

`attribute` · `python`

```python
total_cycles: float | None
```

Source: `src/helia_profiler/results/models.py:750`

### helia_profiler.ProfileResult.layer_count

`attribute` · `python`

```python
layer_count: int
```

Source: `src/helia_profiler/results/models.py:754`

### helia_profiler.ProfileResult.overflow_detected

`attribute` · `python`

```python
overflow_detected: bool
```

Source: `src/helia_profiler/results/models.py:758`
