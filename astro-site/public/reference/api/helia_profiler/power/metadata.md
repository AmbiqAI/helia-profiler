# helia_profiler.power.metadata

The provenance of a power number: how it was observed, what the measurement boundary included, and whether the capture holds together.

Every name on this page is imported from `helia_profiler`.

**API tier:** `experimental`

Generated from the `src/helia_profiler` tree `872d67ad4e9083b1eacd7d6d3859148437b96b59`.

## helia_profiler.MeasurementScope

`class` · `python`

```python
MeasurementScope()
```

What the published power numbers actually measured.

**API tier:** `experimental`

Source: `src/helia_profiler/power/metadata.py:38`

### helia_profiler.MeasurementScope.GPIO_GATED_CLEAN_WINDOW

`constant` · `python`

```python
GPIO_GATED_CLEAN_WINDOW = 'gpio_gated_clean_window'
```

Source: `src/helia_profiler/power/metadata.py:42`

### helia_profiler.MeasurementScope.FREE_FORM_CAPTURE

`constant` · `python`

```python
FREE_FORM_CAPTURE = 'free_form_capture'
```

Source: `src/helia_profiler/power/metadata.py:44`

### helia_profiler.MeasurementScope.ON_DEVICE_GATED_INFERENCE

`constant` · `python`

```python
ON_DEVICE_GATED_INFERENCE = 'on_device_gated_inference'
```

Source: `src/helia_profiler/power/metadata.py:46`

### helia_profiler.MeasurementScope.WHOLE_CAPTURE_WINDOW

`constant` · `python`

```python
WHOLE_CAPTURE_WINDOW = 'whole_capture_window'
```

Source: `src/helia_profiler/power/metadata.py:48`

## helia_profiler.ObservationMode

`class` · `python`

```python
ObservationMode()
```

How the observation was made -- the single vocabulary (#154).

**API tier:** `experimental`

Source: `src/helia_profiler/power/metadata.py:51`

### helia_profiler.ObservationMode.GPIO_GATED

`constant` · `python`

```python
GPIO_GATED = 'gpio_gated'
```

Source: `src/helia_profiler/power/metadata.py:54`

### helia_profiler.ObservationMode.FREE_FORM

`constant` · `python`

```python
FREE_FORM = 'free_form'
```

Source: `src/helia_profiler/power/metadata.py:55`

### helia_profiler.ObservationMode.ON_DEVICE

`constant` · `python`

```python
ON_DEVICE = 'on_device'
```

Source: `src/helia_profiler/power/metadata.py:56`

## helia_profiler.PowerIntegrity

`class` · `python`

```python
PowerIntegrity()
```

Whether the observation is valid for efficiency metrics.

**API tier:** `experimental`

Source: `src/helia_profiler/power/metadata.py:59`

### helia_profiler.PowerIntegrity.VALID

`constant` · `python`

```python
VALID = 'valid'
```

Source: `src/helia_profiler/power/metadata.py:62`

### helia_profiler.PowerIntegrity.DEGRADED

`constant` · `python`

```python
DEGRADED = 'degraded'
```

Source: `src/helia_profiler/power/metadata.py:63`

### helia_profiler.PowerIntegrity.INVALID

`constant` · `python`

```python
INVALID = 'invalid'
```

Source: `src/helia_profiler/power/metadata.py:64`

## helia_profiler.PowerMetadata

`class` · `python`

```python
PowerMetadata(
    driver: str | None = None,
    device: str | None = None,
    io_voltage: float | None = None,
    gating_method: str | None = None,
    sync_input_index: int | None = None,
    stats_rate_hz: int | None = None,
    stats_scnt: int | None = None,
    window_count: int | None = None,
    gpi_poll_count: int | None = None,
    stat_packets: int | None = None,
    early_stopped: bool | None = None,
    capture_window_s: float | None = None,
    capture_safety_bound_s: float | None = None,
    short_gate_pulses_ignored: int | None = None,
    clean_infer_count: int | None = None,
    inference_count: int | None = None,
    source: str | None = None,
    measurement_scope: MeasurementScope | str | None = None,
    observation_mode: ObservationMode | None = None,
    integrity: PowerIntegrity | None = None,
    gate_rise_observed: bool | None = None,
    gate_fall_observed: bool | None = None,
    observation_deadline_s: float | None = None,
    power_firmware: str | None = None,
    power_plan: dict[str, Any] | None = None,
    sync: SyncHandshakeMetadata | None = None,
    sync_timing_s: GateTransitionTiming | None = None,
    gate_failure: GateFailure | None = None,
    gate_duration_integrity: GateDurationIntegrity | None = None,
    window_clock_ceiling: WindowClockCeiling | None = None,
    target_lifecycle: 'TargetLifecyclePlan | None' = None,
    short_gate_pulse_diagnostics: dict[str, Any] | None = None,
    whole_capture_summary: dict[str, Any] | None = None,
    fullrate_xcheck: dict[str, Any] | None = None,
    gating_diagnostics: dict[str, Any] | None = None,
    gated_vs_whole_current_ok: bool | None = None,
) -> None
```

`dataclass`

Everything the capture layer tells the rest of HPX about one power run.

Mutable by design — enriched progressively, like ``RunMetadata``. The
serialized view (:meth:`to_metadata_dict`) emits every non-``None`` field
under its historical key name, flattening the typed diagnostics through
their ``to_metadata()`` methods.

**API tier:** `experimental`

Source: `src/helia_profiler/power/metadata.py:81`

### helia_profiler.PowerMetadata.driver

`attribute` · `python`

```python
driver: str | None = None
```

Source: `src/helia_profiler/power/metadata.py:92`

### helia_profiler.PowerMetadata.device

`attribute` · `python`

```python
device: str | None = None
```

Source: `src/helia_profiler/power/metadata.py:93`

### helia_profiler.PowerMetadata.io_voltage

`attribute` · `python`

```python
io_voltage: float | None = None
```

Source: `src/helia_profiler/power/metadata.py:94`

### helia_profiler.PowerMetadata.gating_method

`attribute` · `python`

```python
gating_method: str | None = None
```

Source: `src/helia_profiler/power/metadata.py:95`

### helia_profiler.PowerMetadata.sync_input_index

`attribute` · `python`

```python
sync_input_index: int | None = None
```

Source: `src/helia_profiler/power/metadata.py:96`

### helia_profiler.PowerMetadata.stats_rate_hz

`attribute` · `python`

```python
stats_rate_hz: int | None = None
```

Source: `src/helia_profiler/power/metadata.py:97`

### helia_profiler.PowerMetadata.stats_scnt

`attribute` · `python`

```python
stats_scnt: int | None = None
```

Source: `src/helia_profiler/power/metadata.py:98`

### helia_profiler.PowerMetadata.window_count

`attribute` · `python`

```python
window_count: int | None = None
```

Source: `src/helia_profiler/power/metadata.py:101`

### helia_profiler.PowerMetadata.gpi_poll_count

`attribute` · `python`

```python
gpi_poll_count: int | None = None
```

Source: `src/helia_profiler/power/metadata.py:102`

### helia_profiler.PowerMetadata.stat_packets

`attribute` · `python`

```python
stat_packets: int | None = None
```

Source: `src/helia_profiler/power/metadata.py:103`

### helia_profiler.PowerMetadata.early_stopped

`attribute` · `python`

```python
early_stopped: bool | None = None
```

Source: `src/helia_profiler/power/metadata.py:104`

### helia_profiler.PowerMetadata.capture_window_s

`attribute` · `python`

```python
capture_window_s: float | None = None
```

Source: `src/helia_profiler/power/metadata.py:105`

### helia_profiler.PowerMetadata.capture_safety_bound_s

`attribute` · `python`

```python
capture_safety_bound_s: float | None = None
```

Source: `src/helia_profiler/power/metadata.py:106`

### helia_profiler.PowerMetadata.short_gate_pulses_ignored

`attribute` · `python`

```python
short_gate_pulses_ignored: int | None = None
```

Source: `src/helia_profiler/power/metadata.py:107`

### helia_profiler.PowerMetadata.clean_infer_count

`attribute` · `python`

```python
clean_infer_count: int | None = None
```

Source: `src/helia_profiler/power/metadata.py:108`

### helia_profiler.PowerMetadata.inference_count

`attribute` · `python`

```python
inference_count: int | None = None
```

Source: `src/helia_profiler/power/metadata.py:109`

### helia_profiler.PowerMetadata.source

`attribute` · `python`

```python
source: str | None = None
```

Source: `src/helia_profiler/power/metadata.py:110`

### helia_profiler.PowerMetadata.measurement_scope

`attribute` · `python`

```python
measurement_scope: MeasurementScope | str | None = None
```

Source: `src/helia_profiler/power/metadata.py:119`

### helia_profiler.PowerMetadata.observation_mode

`attribute` · `python`

```python
observation_mode: ObservationMode | None = None
```

Source: `src/helia_profiler/power/metadata.py:120`

### helia_profiler.PowerMetadata.integrity

`attribute` · `python`

```python
integrity: PowerIntegrity | None = None
```

Source: `src/helia_profiler/power/metadata.py:121`

### helia_profiler.PowerMetadata.gate_rise_observed

`attribute` · `python`

```python
gate_rise_observed: bool | None = None
```

Source: `src/helia_profiler/power/metadata.py:122`

### helia_profiler.PowerMetadata.gate_fall_observed

`attribute` · `python`

```python
gate_fall_observed: bool | None = None
```

Source: `src/helia_profiler/power/metadata.py:123`

### helia_profiler.PowerMetadata.observation_deadline_s

`attribute` · `python`

```python
observation_deadline_s: float | None = None
```

Source: `src/helia_profiler/power/metadata.py:124`

### helia_profiler.PowerMetadata.power_firmware

`attribute` · `python`

```python
power_firmware: str | None = None
```

Source: `src/helia_profiler/power/metadata.py:127`

### helia_profiler.PowerMetadata.power_plan

`attribute` · `python`

```python
power_plan: dict[str, Any] | None = None
```

Source: `src/helia_profiler/power/metadata.py:128`

### helia_profiler.PowerMetadata.sync

`attribute` · `python`

```python
sync: SyncHandshakeMetadata | None = None
```

Source: `src/helia_profiler/power/metadata.py:131`

### helia_profiler.PowerMetadata.sync_timing_s

`attribute` · `python`

```python
sync_timing_s: GateTransitionTiming | None = None
```

Source: `src/helia_profiler/power/metadata.py:133`

### helia_profiler.PowerMetadata.gate_failure

`attribute` · `python`

```python
gate_failure: GateFailure | None = None
```

Source: `src/helia_profiler/power/metadata.py:134`

### helia_profiler.PowerMetadata.gate_duration_integrity

`attribute` · `python`

```python
gate_duration_integrity: GateDurationIntegrity | None = None
```

Source: `src/helia_profiler/power/metadata.py:135`

### helia_profiler.PowerMetadata.window_clock_ceiling

`attribute` · `python`

```python
window_clock_ceiling: WindowClockCeiling | None = None
```

Source: `src/helia_profiler/power/metadata.py:136`

### helia_profiler.PowerMetadata.target_lifecycle

`attribute` · `python`

```python
target_lifecycle: 'TargetLifecyclePlan | None' = None
```

Source: `src/helia_profiler/power/metadata.py:137`

### helia_profiler.PowerMetadata.short_gate_pulse_diagnostics

`attribute` · `python`

```python
short_gate_pulse_diagnostics: dict[str, Any] | None = None
```

Source: `src/helia_profiler/power/metadata.py:140`

### helia_profiler.PowerMetadata.whole_capture_summary

`attribute` · `python`

```python
whole_capture_summary: dict[str, Any] | None = None
```

Source: `src/helia_profiler/power/metadata.py:141`

### helia_profiler.PowerMetadata.fullrate_xcheck

`attribute` · `python`

```python
fullrate_xcheck: dict[str, Any] | None = None
```

Source: `src/helia_profiler/power/metadata.py:142`

### helia_profiler.PowerMetadata.gating_diagnostics

`attribute` · `python`

```python
gating_diagnostics: dict[str, Any] | None = None
```

Source: `src/helia_profiler/power/metadata.py:144`

### helia_profiler.PowerMetadata.gated_vs_whole_current_ok

`attribute` · `python`

```python
gated_vs_whole_current_ok: bool | None = None
```

Source: `src/helia_profiler/power/metadata.py:145`

### helia_profiler.PowerMetadata.to_metadata_dict

`method` · `python`

```python
to_metadata_dict() -> dict[str, Any]
```

Flat dict view, byte-compatible with the pre-#154 metadata bag.

Emits every non-``None`` field under its historical key; typed
diagnostics flatten through their own ``to_metadata()``. ``False``
is a recorded value and is emitted; ``None`` means "never set" and
is omitted.

Source: `src/helia_profiler/power/metadata.py:162`

### helia_profiler.PowerMetadata.set_observation

`method` · `python`

```python
set_observation(
    *,
    observation_mode: ObservationMode,
    integrity: PowerIntegrity | str,
    gate_rise_observed: bool,
    gate_fall_observed: bool,
    observation_deadline_s: float,
) -> None
```

Enrich metadata with the observation classification at publication.

Source: `src/helia_profiler/power/metadata.py:181`
