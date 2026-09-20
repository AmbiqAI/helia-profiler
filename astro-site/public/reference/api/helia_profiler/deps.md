# helia_profiler.deps

What this build of heliaPROFILER is qualified against: the board, engine and toolchain matrix, and the locked dependency revisions a run was produced with.

Every name on this page is imported from `helia_profiler`.

**API tier:** `stable`, `experimental`

Generated from [`src/helia_profiler` at `cc1c3ed`](https://github.com/AmbiqAI/helia-profiler/tree/cc1c3ed4a7908d4c5552f800e8b3790188b8bbd8/src/helia_profiler).

## helia_profiler.QualificationState

`class` · `python`

```python
QualificationState()
```

Compatibility state of a resolved profiling configuration.

**API tier:** `stable`

Source: `src/helia_profiler/deps/compatibility.py:44`

### helia_profiler.QualificationState.QUALIFIED

`constant` · `python`

```python
QUALIFIED = 'qualified'
```

Source: `src/helia_profiler/deps/compatibility.py:47`

### helia_profiler.QualificationState.QUALIFIED_WITH_ENGINE_OVERRIDE

`constant` · `python`

```python
QUALIFIED_WITH_ENGINE_OVERRIDE = 'qualified-with-engine-override'
```

Source: `src/helia_profiler/deps/compatibility.py:48`

### helia_profiler.QualificationState.DEVELOPMENT_OVERRIDES

`constant` · `python`

```python
DEVELOPMENT_OVERRIDES = 'development-overrides'
```

Source: `src/helia_profiler/deps/compatibility.py:49`

## helia_profiler.CompatibilityBaseline

`class` · `python`

```python
CompatibilityBaseline(
    schema: str,
    schema_version: int,
    baseline_id: str,
    neuralspotx_package: str,
    neuralspotx_version: str,
    neuralspotx_sha256: str,
    projects: tuple[CompatibilityProject, ...],
    modules: tuple[CompatibilityModule, ...],
    engines: tuple[CompatibilityEngine, ...],
) -> None
```

`dataclass`

Validated, immutable baseline loaded from HPX package data.

**API tier:** `stable`

Source: `src/helia_profiler/deps/compatibility.py:89`

### helia_profiler.CompatibilityBaseline.schema

`attribute` · `python`

```python
schema: str
```

Source: `src/helia_profiler/deps/compatibility.py:93`

### helia_profiler.CompatibilityBaseline.schema_version

`attribute` · `python`

```python
schema_version: int
```

Source: `src/helia_profiler/deps/compatibility.py:94`

### helia_profiler.CompatibilityBaseline.baseline_id

`attribute` · `python`

```python
baseline_id: str
```

Source: `src/helia_profiler/deps/compatibility.py:95`

### helia_profiler.CompatibilityBaseline.neuralspotx_package

`attribute` · `python`

```python
neuralspotx_package: str
```

Source: `src/helia_profiler/deps/compatibility.py:96`

### helia_profiler.CompatibilityBaseline.neuralspotx_version

`attribute` · `python`

```python
neuralspotx_version: str
```

Source: `src/helia_profiler/deps/compatibility.py:97`

### helia_profiler.CompatibilityBaseline.neuralspotx_sha256

`attribute` · `python`

```python
neuralspotx_sha256: str
```

Source: `src/helia_profiler/deps/compatibility.py:98`

### helia_profiler.CompatibilityBaseline.projects

`attribute` · `python`

```python
projects: tuple[CompatibilityProject, ...]
```

Source: `src/helia_profiler/deps/compatibility.py:99`

### helia_profiler.CompatibilityBaseline.modules

`attribute` · `python`

```python
modules: tuple[CompatibilityModule, ...]
```

Source: `src/helia_profiler/deps/compatibility.py:100`

### helia_profiler.CompatibilityBaseline.engines

`attribute` · `python`

```python
engines: tuple[CompatibilityEngine, ...]
```

Source: `src/helia_profiler/deps/compatibility.py:101`

### helia_profiler.CompatibilityBaseline.fingerprint

`attribute` · `python`

```python
fingerprint: str
```

Return the canonical SHA-256 identity reserved for Stage 5.

Source: `src/helia_profiler/deps/compatibility.py:131`

### helia_profiler.CompatibilityBaseline.project

`method` · `python`

```python
project(name: str) -> CompatibilityProject
```

Source: `src/helia_profiler/deps/compatibility.py:103`

### helia_profiler.CompatibilityBaseline.module

`method` · `python`

```python
module(name: str) -> CompatibilityModule
```

Source: `src/helia_profiler/deps/compatibility.py:112`

### helia_profiler.CompatibilityBaseline.engine

`method` · `python`

```python
engine(name: str) -> CompatibilityEngine
```

Source: `src/helia_profiler/deps/compatibility.py:121`

### helia_profiler.CompatibilityBaseline.to_dict

`method` · `python`

```python
to_dict() -> dict[str, Any]
```

Return a stable JSON-safe representation for reports and Stage 5.

Source: `src/helia_profiler/deps/compatibility.py:137`

## helia_profiler.DependencyLockProvenance

`class` · `python`

```python
DependencyLockProvenance(
    lock_path: Path,
    lock_sha256: str,
    registry_hash: str,
    requested_refs: tuple[DependencyRequest, ...],
    resolved: tuple[DependencyModule, ...],
    overrides: tuple[DependencyOverride, ...],
    qualification: QualificationState,
    baseline_fingerprint: str,
    workspace_fingerprint: str,
    lock_mode: DependencyLockMode,
    update_requested: bool,
) -> None
```

`dataclass`

Read-only lock provenance surface for later diagnostics collectors.

**API tier:** `experimental`

Source: `src/helia_profiler/results/dependencies.py:130`

### helia_profiler.DependencyLockProvenance.lock_path

`attribute` · `python`

```python
lock_path: Path
```

Source: `src/helia_profiler/results/dependencies.py:134`

### helia_profiler.DependencyLockProvenance.lock_sha256

`attribute` · `python`

```python
lock_sha256: str
```

Source: `src/helia_profiler/results/dependencies.py:135`

### helia_profiler.DependencyLockProvenance.registry_hash

`attribute` · `python`

```python
registry_hash: str
```

Source: `src/helia_profiler/results/dependencies.py:136`

### helia_profiler.DependencyLockProvenance.requested_refs

`attribute` · `python`

```python
requested_refs: tuple[DependencyRequest, ...]
```

Source: `src/helia_profiler/results/dependencies.py:137`

### helia_profiler.DependencyLockProvenance.resolved

`attribute` · `python`

```python
resolved: tuple[DependencyModule, ...]
```

Source: `src/helia_profiler/results/dependencies.py:138`

### helia_profiler.DependencyLockProvenance.overrides

`attribute` · `python`

```python
overrides: tuple[DependencyOverride, ...]
```

Source: `src/helia_profiler/results/dependencies.py:139`

### helia_profiler.DependencyLockProvenance.qualification

`attribute` · `python`

```python
qualification: QualificationState
```

Source: `src/helia_profiler/results/dependencies.py:140`

### helia_profiler.DependencyLockProvenance.baseline_fingerprint

`attribute` · `python`

```python
baseline_fingerprint: str
```

Source: `src/helia_profiler/results/dependencies.py:141`

### helia_profiler.DependencyLockProvenance.workspace_fingerprint

`attribute` · `python`

```python
workspace_fingerprint: str
```

Source: `src/helia_profiler/results/dependencies.py:142`

### helia_profiler.DependencyLockProvenance.lock_mode

`attribute` · `python`

```python
lock_mode: DependencyLockMode
```

Source: `src/helia_profiler/results/dependencies.py:143`

### helia_profiler.DependencyLockProvenance.update_requested

`attribute` · `python`

```python
update_requested: bool
```

Source: `src/helia_profiler/results/dependencies.py:144`

## helia_profiler.CompatibilityResolution

`class` · `python`

```python
CompatibilityResolution(
    baseline: CompatibilityBaseline,
    qualification: QualificationState,
    module_overrides: tuple[str, ...] = (),
    engine_overrides: tuple[str, ...] = (),
) -> None
```

`dataclass`

Resolved baseline plus explicit override classification.

**API tier:** `stable`

Source: `src/helia_profiler/deps/compatibility.py:172`

### helia_profiler.CompatibilityResolution.baseline

`attribute` · `python`

```python
baseline: CompatibilityBaseline
```

Source: `src/helia_profiler/deps/compatibility.py:176`

### helia_profiler.CompatibilityResolution.qualification

`attribute` · `python`

```python
qualification: QualificationState
```

Source: `src/helia_profiler/deps/compatibility.py:177`

### helia_profiler.CompatibilityResolution.module_overrides

`attribute` · `python`

```python
module_overrides: tuple[str, ...] = ()
```

Source: `src/helia_profiler/deps/compatibility.py:188`

### helia_profiler.CompatibilityResolution.engine_overrides

`attribute` · `python`

```python
engine_overrides: tuple[str, ...] = ()
```

Source: `src/helia_profiler/deps/compatibility.py:189`

### helia_profiler.CompatibilityResolution.fingerprint

`attribute` · `python`

```python
fingerprint: str
```

Source: `src/helia_profiler/deps/compatibility.py:192`

### helia_profiler.CompatibilityResolution.to_dict

`method` · `python`

```python
to_dict() -> dict[str, Any]
```

Return structured result provenance without lossy enum conversion.

Source: `src/helia_profiler/deps/compatibility.py:195`

## helia_profiler.load_compatibility_baseline

`function` · `python`

```python
load_compatibility_baseline(path: Path | None = None) -> CompatibilityBaseline
```

Load and strictly validate an HPX compatibility baseline.

**API tier:** `stable`

Source: `src/helia_profiler/deps/compatibility.py:206`

## helia_profiler.read_dependency_lock_provenance

`function` · `python`

```python
read_dependency_lock_provenance(app_or_workspace_path: str | Path) -> DependencyLockProvenance
```

Read typed lock provenance without mutating an app or workspace.

*app_or_workspace_path* may name ``profiler_app``, its ``nsx.lock`` or
``hpx-dependencies.json``, or the parent fingerprint workspace containing
``profiler_app``. The exact on-disk lock digest is verified against the
recorded run state before a surface is returned.

**API tier:** `experimental`

Source: `src/helia_profiler/deps/dependencies.py:525`
