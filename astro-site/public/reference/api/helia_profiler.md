# helia_profiler

The entry points of the heliaPROFILER Python API: one call, one session, and the progress stream they report through.

Every name on this page is imported from `helia_profiler`.

**API tier:** `stable`

Generated from the `src/helia_profiler` tree `17bfdf5c68d6c73c3d22f7afb063ac9cd722152f`.

**Re-exports**

| Name | Source |
| --- | --- |
| [ProfileConfig](/helia-profiler/reference/api/helia_profiler/config/#helia_profiler.ProfileConfig) | `helia_profiler.ProfileConfig` |
| [ModelConfig](/helia-profiler/reference/api/helia_profiler/config/#helia_profiler.ModelConfig) | `helia_profiler.ModelConfig` |
| [EngineConfig](/helia-profiler/reference/api/helia_profiler/config/#helia_profiler.EngineConfig) | `helia_profiler.EngineConfig` |
| [TargetConfig](/helia-profiler/reference/api/helia_profiler/config/#helia_profiler.TargetConfig) | `helia_profiler.TargetConfig` |
| [ProfilingConfig](/helia-profiler/reference/api/helia_profiler/config/#helia_profiler.ProfilingConfig) | `helia_profiler.ProfilingConfig` |
| [OutputConfig](/helia-profiler/reference/api/helia_profiler/config/#helia_profiler.OutputConfig) | `helia_profiler.OutputConfig` |
| [BuildConfig](/helia-profiler/reference/api/helia_profiler/config/#helia_profiler.BuildConfig) | `helia_profiler.BuildConfig` |
| [HeartbeatConfig](/helia-profiler/reference/api/helia_profiler/config/#helia_profiler.HeartbeatConfig) | `helia_profiler.HeartbeatConfig` |
| [TimeoutsConfig](/helia-profiler/reference/api/helia_profiler/config/#helia_profiler.TimeoutsConfig) | `helia_profiler.TimeoutsConfig` |
| [ClockSelection](/helia-profiler/reference/api/helia_profiler/config/#helia_profiler.ClockSelection) | `helia_profiler.ClockSelection` |
| [OutputFormat](/helia-profiler/reference/api/helia_profiler/config/#helia_profiler.OutputFormat) | `helia_profiler.OutputFormat` |
| [PowerConfig](/helia-profiler/reference/api/helia_profiler/config/power/#helia_profiler.PowerConfig) | `helia_profiler.PowerConfig` |
| [EngineType](/helia-profiler/reference/api/helia_profiler/vocab/#helia_profiler.EngineType) | `helia_profiler.EngineType` |
| [Toolchain](/helia-profiler/reference/api/helia_profiler/vocab/#helia_profiler.Toolchain) | `helia_profiler.Toolchain` |
| [Transport](/helia-profiler/reference/api/helia_profiler/vocab/#helia_profiler.Transport) | `helia_profiler.Transport` |
| [Placement](/helia-profiler/reference/api/helia_profiler/vocab/#helia_profiler.Placement) | `helia_profiler.Placement` |
| [ResetStrategy](/helia-profiler/reference/api/helia_profiler/vocab/#helia_profiler.ResetStrategy) | `helia_profiler.ResetStrategy` |
| [CompatibilityBaseline](/helia-profiler/reference/api/helia_profiler/deps/#helia_profiler.CompatibilityBaseline) | `helia_profiler.CompatibilityBaseline` |
| [CompatibilityResolution](/helia-profiler/reference/api/helia_profiler/deps/#helia_profiler.CompatibilityResolution) | `helia_profiler.CompatibilityResolution` |
| [QualificationState](/helia-profiler/reference/api/helia_profiler/deps/#helia_profiler.QualificationState) | `helia_profiler.QualificationState` |
| [load_compatibility_baseline](/helia-profiler/reference/api/helia_profiler/deps/#helia_profiler.load_compatibility_baseline) | `helia_profiler.load_compatibility_baseline` |
| [DependencyLockProvenance](/helia-profiler/reference/api/helia_profiler/deps/#helia_profiler.DependencyLockProvenance) | `helia_profiler.DependencyLockProvenance` |
| [read_dependency_lock_provenance](/helia-profiler/reference/api/helia_profiler/deps/#helia_profiler.read_dependency_lock_provenance) | `helia_profiler.read_dependency_lock_provenance` |
| [ProfileResult](/helia-profiler/reference/api/helia_profiler/results/#helia_profiler.ProfileResult) | `helia_profiler.ProfileResult` |
| [PmuResult](/helia-profiler/reference/api/helia_profiler/results/#helia_profiler.PmuResult) | `helia_profiler.PmuResult` |
| [PresetResult](/helia-profiler/reference/api/helia_profiler/results/#helia_profiler.PresetResult) | `helia_profiler.PresetResult` |
| [LayerResult](/helia-profiler/reference/api/helia_profiler/results/#helia_profiler.LayerResult) | `helia_profiler.LayerResult` |
| [FirmwareMeta](/helia-profiler/reference/api/helia_profiler/results/#helia_profiler.FirmwareMeta) | `helia_profiler.FirmwareMeta` |
| [RunMetadata](/helia-profiler/reference/api/helia_profiler/results/#helia_profiler.RunMetadata) | `helia_profiler.RunMetadata` |
| [ResultManifest](/helia-profiler/reference/api/helia_profiler/results/manifest/#helia_profiler.ResultManifest) | `helia_profiler.ResultManifest` |
| [ResultArtifact](/helia-profiler/reference/api/helia_profiler/results/manifest/#helia_profiler.ResultArtifact) | `helia_profiler.ResultArtifact` |
| [ResultIssue](/helia-profiler/reference/api/helia_profiler/results/manifest/#helia_profiler.ResultIssue) | `helia_profiler.ResultIssue` |
| [ResultValidity](/helia-profiler/reference/api/helia_profiler/results/manifest/#helia_profiler.ResultValidity) | `helia_profiler.ResultValidity` |
| [RunStatus](/helia-profiler/reference/api/helia_profiler/results/manifest/#helia_profiler.RunStatus) | `helia_profiler.RunStatus` |
| [load_result_manifest](/helia-profiler/reference/api/helia_profiler/results/manifest/#helia_profiler.load_result_manifest) | `helia_profiler.load_result_manifest` |
| [PowerMode](/helia-profiler/reference/api/helia_profiler/power/#helia_profiler.PowerMode) | `helia_profiler.PowerMode` |
| [PowerResult](/helia-profiler/reference/api/helia_profiler/power/#helia_profiler.PowerResult) | `helia_profiler.PowerResult` |
| [PowerObservation](/helia-profiler/reference/api/helia_profiler/power/#helia_profiler.PowerObservation) | `helia_profiler.PowerObservation` |
| [PowerTerminalRecord](/helia-profiler/reference/api/helia_profiler/power/#helia_profiler.PowerTerminalRecord) | `helia_profiler.PowerTerminalRecord` |
| [OnDevicePowerSummary](/helia-profiler/reference/api/helia_profiler/power/#helia_profiler.OnDevicePowerSummary) | `helia_profiler.OnDevicePowerSummary` |
| [PowerMetadata](/helia-profiler/reference/api/helia_profiler/power/metadata/#helia_profiler.PowerMetadata) | `helia_profiler.PowerMetadata` |
| [MeasurementScope](/helia-profiler/reference/api/helia_profiler/power/metadata/#helia_profiler.MeasurementScope) | `helia_profiler.MeasurementScope` |
| [ObservationMode](/helia-profiler/reference/api/helia_profiler/power/metadata/#helia_profiler.ObservationMode) | `helia_profiler.ObservationMode` |
| [PowerIntegrity](/helia-profiler/reference/api/helia_profiler/power/metadata/#helia_profiler.PowerIntegrity) | `helia_profiler.PowerIntegrity` |
| [CompareResult](/helia-profiler/reference/api/helia_profiler/evaluation/#helia_profiler.CompareResult) | `helia_profiler.CompareResult` |
| [assess_comparability](/helia-profiler/reference/api/helia_profiler/evaluation/#helia_profiler.assess_comparability) | `helia_profiler.assess_comparability` |
| [ComparabilityAssessment](/helia-profiler/reference/api/helia_profiler/evaluation/#helia_profiler.ComparabilityAssessment) | `helia_profiler.ComparabilityAssessment` |
| [ComparabilityIssue](/helia-profiler/reference/api/helia_profiler/evaluation/#helia_profiler.ComparabilityIssue) | `helia_profiler.ComparabilityIssue` |
| [ComparabilitySeverity](/helia-profiler/reference/api/helia_profiler/evaluation/#helia_profiler.ComparabilitySeverity) | `helia_profiler.ComparabilitySeverity` |
| [ComparisonProfile](/helia-profiler/reference/api/helia_profiler/evaluation/#helia_profiler.ComparisonProfile) | `helia_profiler.ComparisonProfile` |
| [evaluate_comparison_profile](/helia-profiler/reference/api/helia_profiler/evaluation/#helia_profiler.evaluate_comparison_profile) | `helia_profiler.evaluate_comparison_profile` |
| [ComparisonVerdict](/helia-profiler/reference/api/helia_profiler/evaluation/#helia_profiler.ComparisonVerdict) | `helia_profiler.ComparisonVerdict` |
| [MetricPolicy](/helia-profiler/reference/api/helia_profiler/evaluation/#helia_profiler.MetricPolicy) | `helia_profiler.MetricPolicy` |
| [MetricDirection](/helia-profiler/reference/api/helia_profiler/evaluation/#helia_profiler.MetricDirection) | `helia_profiler.MetricDirection` |
| [MetricVerdict](/helia-profiler/reference/api/helia_profiler/evaluation/#helia_profiler.MetricVerdict) | `helia_profiler.MetricVerdict` |
| [MissingMetricPolicy](/helia-profiler/reference/api/helia_profiler/evaluation/#helia_profiler.MissingMetricPolicy) | `helia_profiler.MissingMetricPolicy` |
| [VerdictStatus](/helia-profiler/reference/api/helia_profiler/evaluation/#helia_profiler.VerdictStatus) | `helia_profiler.VerdictStatus` |
| [evaluate_run](/helia-profiler/reference/api/helia_profiler/evaluation/#helia_profiler.evaluate_run) | `helia_profiler.evaluate_run` |
| [RunEvaluation](/helia-profiler/reference/api/helia_profiler/evaluation/#helia_profiler.RunEvaluation) | `helia_profiler.RunEvaluation` |
| [ModelAnalysis](/helia-profiler/reference/api/helia_profiler/modelcost/#helia_profiler.ModelAnalysis) | `helia_profiler.ModelAnalysis` |
| [SupportBundleOptions](/helia-profiler/reference/api/helia_profiler/diagnostics/#helia_profiler.SupportBundleOptions) | `helia_profiler.SupportBundleOptions` |
| [collect_support_bundle](/helia-profiler/reference/api/helia_profiler/diagnostics/#helia_profiler.collect_support_bundle) | `helia_profiler.collect_support_bundle` |
| [write_support_bundle](/helia-profiler/reference/api/helia_profiler/diagnostics/#helia_profiler.write_support_bundle) | `helia_profiler.write_support_bundle` |
| [verify_support_bundle](/helia-profiler/reference/api/helia_profiler/diagnostics/#helia_profiler.verify_support_bundle) | `helia_profiler.verify_support_bundle` |
| [SupportBundleManifest](/helia-profiler/reference/api/helia_profiler/diagnostics/#helia_profiler.SupportBundleManifest) | `helia_profiler.SupportBundleManifest` |
| [SupportBundleSection](/helia-profiler/reference/api/helia_profiler/diagnostics/#helia_profiler.SupportBundleSection) | `helia_profiler.SupportBundleSection` |
| [HpxError](/helia-profiler/reference/api/helia_profiler/errors/#helia_profiler.HpxError) | `helia_profiler.HpxError` |
| [ConfigError](/helia-profiler/reference/api/helia_profiler/errors/#helia_profiler.ConfigError) | `helia_profiler.ConfigError` |
| [PlatformError](/helia-profiler/reference/api/helia_profiler/errors/#helia_profiler.PlatformError) | `helia_profiler.PlatformError` |
| [EngineError](/helia-profiler/reference/api/helia_profiler/errors/#helia_profiler.EngineError) | `helia_profiler.EngineError` |
| [FirmwareError](/helia-profiler/reference/api/helia_profiler/errors/#helia_profiler.FirmwareError) | `helia_profiler.FirmwareError` |
| [BuildError](/helia-profiler/reference/api/helia_profiler/errors/#helia_profiler.BuildError) | `helia_profiler.BuildError` |
| [DependencyError](/helia-profiler/reference/api/helia_profiler/errors/#helia_profiler.DependencyError) | `helia_profiler.DependencyError` |
| [VersionError](/helia-profiler/reference/api/helia_profiler/errors/#helia_profiler.VersionError) | `helia_profiler.VersionError` |
| [LockError](/helia-profiler/reference/api/helia_profiler/errors/#helia_profiler.LockError) | `helia_profiler.LockError` |
| [NetworkError](/helia-profiler/reference/api/helia_profiler/errors/#helia_profiler.NetworkError) | `helia_profiler.NetworkError` |
| [CaptureError](/helia-profiler/reference/api/helia_profiler/errors/#helia_profiler.CaptureError) | `helia_profiler.CaptureError` |
| [DeterministicCaptureError](/helia-profiler/reference/api/helia_profiler/errors/#helia_profiler.DeterministicCaptureError) | `helia_profiler.DeterministicCaptureError` |
| [PowerError](/helia-profiler/reference/api/helia_profiler/errors/#helia_profiler.PowerError) | `helia_profiler.PowerError` |
| [ReportError](/helia-profiler/reference/api/helia_profiler/errors/#helia_profiler.ReportError) | `helia_profiler.ReportError` |

## helia_profiler.__version__

`attribute` · `python`

```python
__version__ = '0.1.6'
```

**API tier:** `stable`

Source: `src/helia_profiler/_version.py:1`

## helia_profiler.profile

`function` · `python`

```python
profile(config: ProfileConfig, *, progress_sink: ProgressSink | None = None) -> ProfileResult
```

Run a full profiling session.

This is the main programmatic entry point.  It builds the default pipeline,
executes all stages, and returns a typed :class:`ProfileResult`.

Raises :class:`HpxError` (or a subclass) on failure.

**API tier:** `stable`

Source: `src/helia_profiler/api.py:24`

## helia_profiler.ProgressUpdate

`class` · `python`

```python
ProgressUpdate(
    message: str,
    kind: Literal['status', 'checkpoint'] = 'status',
    completed: int | None = None,
    total: int | None = None,
    unit: str | None = None,
    eta_s: float | None = None,
    min_verbosity: int = 0,
) -> None
```

`dataclass`

User-meaningful progress within a pipeline stage.

**API tier:** `stable`

Source: `src/helia_profiler/pipeline.py:56`

### helia_profiler.ProgressUpdate.message

`attribute` · `python`

```python
message: str
```

Source: `src/helia_profiler/pipeline.py:60`

### helia_profiler.ProgressUpdate.kind

`attribute` · `python`

```python
kind: Literal['status', 'checkpoint'] = 'status'
```

Source: `src/helia_profiler/pipeline.py:61`

### helia_profiler.ProgressUpdate.completed

`attribute` · `python`

```python
completed: int | None = None
```

Source: `src/helia_profiler/pipeline.py:62`

### helia_profiler.ProgressUpdate.total

`attribute` · `python`

```python
total: int | None = None
```

Source: `src/helia_profiler/pipeline.py:63`

### helia_profiler.ProgressUpdate.unit

`attribute` · `python`

```python
unit: str | None = None
```

Source: `src/helia_profiler/pipeline.py:64`

### helia_profiler.ProgressUpdate.eta_s

`attribute` · `python`

```python
eta_s: float | None = None
```

Source: `src/helia_profiler/pipeline.py:65`

### helia_profiler.ProgressUpdate.min_verbosity

`attribute` · `python`

```python
min_verbosity: int = 0
```

Source: `src/helia_profiler/pipeline.py:66`

## helia_profiler.Session

`class` · `python`

```python
Session(
    yaml_path: Path | None = None,
    _base: Mapping[str, Any] = lambda : ...(),
    _overrides: Mapping[str, Any] = lambda : ...(),
) -> None
```

`dataclass`

Immutable, branchable configuration for interactive HPX operations.

Sessions retain unresolved configuration overrides so YAML values and
board-derived defaults are resolved by the same validation path as the
CLI. Every ``with_*`` method returns an independent session.

**API tier:** `stable`

Source: `src/helia_profiler/session.py:94`

### helia_profiler.Session.yaml_path

`attribute` · `python`

```python
yaml_path: Path | None = None
```

Source: `src/helia_profiler/session.py:103`

### helia_profiler.Session.from_yaml

`method` · `python`

```python
from_yaml(path: str | Path) -> Self
```

`classmethod`

Create a session from an immutable snapshot of an HPX YAML config.

Source: `src/helia_profiler/session.py:117`

### helia_profiler.Session.from_dict

`method` · `python`

```python
from_dict(intent: Mapping[str, Any]) -> Self
```

`classmethod`

Create a session from unresolved configuration intent.

Source: `src/helia_profiler/session.py:141`

### helia_profiler.Session.load

`method` · `python`

```python
load(path: str | Path) -> Self
```

`classmethod`

Load a versioned unresolved-intent snapshot from JSON.

Source: `src/helia_profiler/session.py:148`

### helia_profiler.Session.intent_dict

`method` · `python`

```python
intent_dict() -> dict[str, Any]
```

Return JSON-safe unresolved intent without expanding defaults.

Source: `src/helia_profiler/session.py:174`

### helia_profiler.Session.resolved_dict

`method` · `python`

```python
resolved_dict(model: str | Path | None = None) -> dict[str, Any]
```

Return the fully resolved and validated configuration snapshot.

Source: `src/helia_profiler/session.py:178`

### helia_profiler.Session.save

`method` · `python`

```python
save(path: str | Path) -> Path
```

Persist unresolved intent as a versioned JSON snapshot.

Source: `src/helia_profiler/session.py:184`

### helia_profiler.Session.with_overrides

`method` · `python`

```python
with_overrides(overrides: Mapping[str, Any]) -> Self
```

Return a session with advanced raw configuration overrides merged in.

Source: `src/helia_profiler/session.py:200`

### helia_profiler.Session.with_model

`method` · `python`

```python
with_model(path: str | Path, **options: Any = {}) -> Self
```

Source: `src/helia_profiler/session.py:204`

### helia_profiler.Session.with_engine

`method` · `python`

```python
with_engine(engine: Any, **options: Any = {}) -> Self
```

Source: `src/helia_profiler/session.py:207`

### helia_profiler.Session.with_target

`method` · `python`

```python
with_target(**options: Any = {}) -> Self
```

Source: `src/helia_profiler/session.py:210`

### helia_profiler.Session.with_profiling

`method` · `python`

```python
with_profiling(**options: Any = {}) -> Self
```

Source: `src/helia_profiler/session.py:213`

### helia_profiler.Session.with_power

`method` · `python`

```python
with_power(**options: Any = {}) -> Self
```

Source: `src/helia_profiler/session.py:216`

### helia_profiler.Session.with_output

`method` · `python`

```python
with_output(**options: Any = {}) -> Self
```

Source: `src/helia_profiler/session.py:219`

### helia_profiler.Session.with_build

`method` · `python`

```python
with_build(**options: Any = {}) -> Self
```

Source: `src/helia_profiler/session.py:222`

### helia_profiler.Session.with_timeouts

`method` · `python`

```python
with_timeouts(**options: Any = {}) -> Self
```

Source: `src/helia_profiler/session.py:225`

### helia_profiler.Session.with_options

`method` · `python`

```python
with_options(
    *,
    verbose: int | None = None,
    frozen: bool | None = None,
    work_dir: str | Path | None = None,
    clean: bool | None = None,
) -> Self
```

Return a session with top-level run options.

Source: `src/helia_profiler/session.py:228`

### helia_profiler.Session.resolve

`method` · `python`

```python
resolve(model: str | Path | None = None) -> ProfileConfig
```

Resolve and validate this session as a complete profile config.

Source: `src/helia_profiler/session.py:248`

### helia_profiler.Session.profile

`method` · `python`

```python
profile(
    model: str | Path | None = None,
    *,
    progress_sink: Callable[[ProgressUpdate], None] | None = None,
) -> ProfileResult
```

Run a profile using this session's resolved configuration.

Source: `src/helia_profiler/session.py:255`

### helia_profiler.Session.analyze

`method` · `python`

```python
analyze(model: str | Path | None = None) -> ModelAnalysis
```

Analyze the configured model without building or flashing firmware.

Source: `src/helia_profiler/session.py:266`

### helia_profiler.Session.compare

`method` · `python`

```python
compare(
    baseline: str | Path | ProfileResult,
    candidate: str | Path | ProfileResult,
    *,
    output_dir: str | Path | None = None,
    profile: str | Path | ComparisonProfile | None = None,
) -> CompareResult
```

Compare two completed profiles and optionally write diff artifacts.

Source: `src/helia_profiler/session.py:277`

### helia_profiler.Session.doctor

`method` · `python`

```python
doctor() -> DoctorResult
```

Return structured host dependency checks.

Source: `src/helia_profiler/session.py:305`

### helia_profiler.Session.show

`method` · `python`

```python
show(value: Any, *, console: Console | None = None) -> Any
```

Pretty-print a typed interactive value and return it unchanged.

Source: `src/helia_profiler/session.py:319`

### helia_profiler.Session.boards

`method` · `python`

```python
boards() -> tuple[BoardDef, ...]
```

Return boards visible to this session's platform registry.

Source: `src/helia_profiler/session.py:325`

### helia_profiler.Session.engines

`method` · `python`

```python
engines() -> tuple[EngineType, ...]
```

Return supported inference engine identifiers.

Source: `src/helia_profiler/session.py:332`

### helia_profiler.Session.counter_groups

`method` · `python`

```python
counter_groups() -> tuple[str, ...]
```

Return registered PMU counter group names.

Source: `src/helia_profiler/session.py:338`

### helia_profiler.Session.counters

`method` · `python`

```python
counters(group: str | None = None) -> tuple[PmuCounter, ...]
```

Return registered PMU counters, optionally filtered by group.

Source: `src/helia_profiler/session.py:344`

### helia_profiler.Session.probes

`method` · `python`

```python
probes() -> tuple[JLinkProbe, ...]
```

Return connected J-Link probes.

Source: `src/helia_profiler/session.py:350`

### helia_profiler.Session.inspect_probes

`method` · `python`

```python
inspect_probes(board: str | None = None) -> tuple[JLinkProbeMatch, ...]
```

Inspect the target core visible through each connected probe.

Source: `src/helia_profiler/session.py:356`

### helia_profiler.Session.match_probe

`method` · `python`

```python
match_probe(board: str | None = None, *, serial: str | None = None) -> str
```

Resolve the J-Link serial matching a board target.

Source: `src/helia_profiler/session.py:367`

### helia_profiler.Session.ports

`method` · `python`

```python
ports(*, include_all: bool = False) -> tuple[SerialPortInfo, ...]
```

Return host serial ports relevant to HPX transports.

Source: `src/helia_profiler/session.py:385`

### helia_profiler.Session.reset

`method` · `python`

```python
reset(board: str | None = None, *, serial: str | None = None, kind: Literal['debug', 'swpoi'] = 'debug') -> None
```

Reset the configured target through its J-Link probe.

Source: `src/helia_profiler/session.py:391`
