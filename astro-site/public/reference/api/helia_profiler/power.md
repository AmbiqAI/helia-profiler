# helia_profiler.power

What a power capture produces: the measured result, the observations behind it, and the on-device summary the firmware reports.

Every name on this page is imported from `helia_profiler`.

**API tier:** `stable`, `experimental`

Generated from the `src/helia_profiler` tree `872d67ad4e9083b1eacd7d6d3859148437b96b59`.

## helia_profiler.PowerMode

`class` · `python`

```python
PowerMode()
```

Power measurement mode.

**API tier:** `stable`

Source: `src/helia_profiler/power/base.py:12`

### helia_profiler.PowerMode.EXTERNAL

`constant` · `python`

```python
EXTERNAL = 'external'
```

Source: `src/helia_profiler/power/base.py:15`

### helia_profiler.PowerMode.INTERNAL

`constant` · `python`

```python
INTERNAL = 'internal'
```

Source: `src/helia_profiler/power/base.py:16`

## helia_profiler.PowerObservation

`class` · `python`

```python
PowerObservation(
    mode: ObservationMode,
    result: PowerResult,
    gate_rise_observed: bool,
    gate_fall_observed: bool,
    deadline_s: float,
    integrity: PowerIntegrity,
) -> None
```

`dataclass`

Host instrument observation, independent of firmware terminal status.

**API tier:** `experimental`

Source: `src/helia_profiler/results/artifacts.py:66`

### helia_profiler.PowerObservation.mode

`attribute` · `python`

```python
mode: ObservationMode
```

Source: `src/helia_profiler/results/artifacts.py:70`

### helia_profiler.PowerObservation.result

`attribute` · `python`

```python
result: PowerResult
```

Source: `src/helia_profiler/results/artifacts.py:71`

### helia_profiler.PowerObservation.gate_rise_observed

`attribute` · `python`

```python
gate_rise_observed: bool
```

Source: `src/helia_profiler/results/artifacts.py:72`

### helia_profiler.PowerObservation.gate_fall_observed

`attribute` · `python`

```python
gate_fall_observed: bool
```

Source: `src/helia_profiler/results/artifacts.py:73`

### helia_profiler.PowerObservation.deadline_s

`attribute` · `python`

```python
deadline_s: float
```

Source: `src/helia_profiler/results/artifacts.py:74`

### helia_profiler.PowerObservation.integrity

`attribute` · `python`

```python
integrity: PowerIntegrity
```

Source: `src/helia_profiler/results/artifacts.py:75`

## helia_profiler.PowerResult

`class` · `python`

```python
PowerResult(
    summary: PowerSummary,
    samples: list[PowerSample] = list(),
    gated_windows: list[GatedPowerWindow] = list(),
    per_layer: dict[str, Any] | None = None,
    metadata: PowerMetadata = PowerMetadata(),
) -> None
```

`dataclass`

Complete result of a power capture.

``metadata`` is the typed :class:`~helia_profiler.power.metadata.PowerMetadata`
(#154 Phase 2); the flat dict view is ``metadata.to_metadata_dict()``.
The result is frozen but its metadata is deliberately mutable: pipeline
stages enrich it after capture, like ``RunMetadata``.

**API tier:** `stable`

Source: `src/helia_profiler/power/base.py:73`

### helia_profiler.PowerResult.summary

`attribute` · `python`

```python
summary: PowerSummary
```

Source: `src/helia_profiler/power/base.py:83`

### helia_profiler.PowerResult.samples

`attribute` · `python`

```python
samples: list[PowerSample] = field(default_factory=list)
```

Source: `src/helia_profiler/power/base.py:84`

### helia_profiler.PowerResult.gated_windows

`attribute` · `python`

```python
gated_windows: list[GatedPowerWindow] = field(default_factory=list)
```

Source: `src/helia_profiler/power/base.py:85`

### helia_profiler.PowerResult.per_layer

`attribute` · `python`

```python
per_layer: dict[str, Any] | None = None
```

Source: `src/helia_profiler/power/base.py:86`

### helia_profiler.PowerResult.metadata

`attribute` · `python`

```python
metadata: PowerMetadata = field(default_factory=PowerMetadata)
```

Source: `src/helia_profiler/power/base.py:87`

## helia_profiler.PowerTerminalRecord

`class` · `python`

```python
PowerTerminalRecord(
    version: int,
    status: Literal['ok', 'error'],
    requested_count: int,
    completed_count: int,
    elapsed_us: int | None,
    final_phase: str,
    error_code: int,
    gate_asserted: bool,
    gate_lowered: bool,
) -> None
```

`dataclass`

Versioned firmware status emitted only after the power gate is low.

**API tier:** `experimental`

Source: `src/helia_profiler/results/artifacts.py:78`

### helia_profiler.PowerTerminalRecord.version

`attribute` · `python`

```python
version: int
```

Source: `src/helia_profiler/results/artifacts.py:82`

### helia_profiler.PowerTerminalRecord.status

`attribute` · `python`

```python
status: Literal['ok', 'error']
```

Source: `src/helia_profiler/results/artifacts.py:83`

### helia_profiler.PowerTerminalRecord.requested_count

`attribute` · `python`

```python
requested_count: int
```

Source: `src/helia_profiler/results/artifacts.py:84`

### helia_profiler.PowerTerminalRecord.completed_count

`attribute` · `python`

```python
completed_count: int
```

Source: `src/helia_profiler/results/artifacts.py:85`

### helia_profiler.PowerTerminalRecord.elapsed_us

`attribute` · `python`

```python
elapsed_us: int | None
```

Source: `src/helia_profiler/results/artifacts.py:86`

### helia_profiler.PowerTerminalRecord.final_phase

`attribute` · `python`

```python
final_phase: str
```

Source: `src/helia_profiler/results/artifacts.py:87`

### helia_profiler.PowerTerminalRecord.error_code

`attribute` · `python`

```python
error_code: int
```

Source: `src/helia_profiler/results/artifacts.py:88`

### helia_profiler.PowerTerminalRecord.gate_asserted

`attribute` · `python`

```python
gate_asserted: bool
```

Source: `src/helia_profiler/results/artifacts.py:89`

### helia_profiler.PowerTerminalRecord.gate_lowered

`attribute` · `python`

```python
gate_lowered: bool
```

Source: `src/helia_profiler/results/artifacts.py:90`

## helia_profiler.OnDevicePowerSummary

`class` · `python`

```python
OnDevicePowerSummary(
    source: str,
    scope: Literal['fixed_n_inference'],
    energy_nj: int,
    duration_us: int,
    inference_count: int,
    overflow: bool,
    charge_nc: int | None = None,
    bus_voltage_uv: int | None = None,
    calibration_id: str | None = None,
) -> None
```

`dataclass`

Integer-unit aggregate reported by a firmware-side power monitor.

**API tier:** `experimental`

Source: `src/helia_profiler/results/artifacts.py:109`

### helia_profiler.OnDevicePowerSummary.source

`attribute` · `python`

```python
source: str
```

Source: `src/helia_profiler/results/artifacts.py:113`

### helia_profiler.OnDevicePowerSummary.scope

`attribute` · `python`

```python
scope: Literal['fixed_n_inference']
```

Source: `src/helia_profiler/results/artifacts.py:114`

### helia_profiler.OnDevicePowerSummary.energy_nj

`attribute` · `python`

```python
energy_nj: int
```

Source: `src/helia_profiler/results/artifacts.py:115`

### helia_profiler.OnDevicePowerSummary.duration_us

`attribute` · `python`

```python
duration_us: int
```

Source: `src/helia_profiler/results/artifacts.py:116`

### helia_profiler.OnDevicePowerSummary.inference_count

`attribute` · `python`

```python
inference_count: int
```

Source: `src/helia_profiler/results/artifacts.py:117`

### helia_profiler.OnDevicePowerSummary.overflow

`attribute` · `python`

```python
overflow: bool
```

Source: `src/helia_profiler/results/artifacts.py:118`

### helia_profiler.OnDevicePowerSummary.charge_nc

`attribute` · `python`

```python
charge_nc: int | None = None
```

Source: `src/helia_profiler/results/artifacts.py:119`

### helia_profiler.OnDevicePowerSummary.bus_voltage_uv

`attribute` · `python`

```python
bus_voltage_uv: int | None = None
```

Source: `src/helia_profiler/results/artifacts.py:120`

### helia_profiler.OnDevicePowerSummary.calibration_id

`attribute` · `python`

```python
calibration_id: str | None = None
```

Source: `src/helia_profiler/results/artifacts.py:121`
