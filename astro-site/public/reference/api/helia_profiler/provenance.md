# helia_profiler.provenance

Recorded run metadata and linker provenance: expected source-bound layouts and per-executable verification, separate from measured results.

Every name on this page is imported from `helia_profiler`.

**API tier:** `stable`, `experimental`

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__`.

## helia_profiler.LinkerFile

`class` · `python`

```python
LinkerFile(path: str, sha256: str) -> None
```

`dataclass`

One relative SDK input, identified by its UTF-8 source with LF line endings.

**API tier:** `experimental`

Source: `src/helia_profiler/platform/linker/model.py:17`

### helia_profiler.LinkerFile.path

`attribute` · `python`

```python
path: str
```

Source: `src/helia_profiler/platform/linker/model.py:21`

### helia_profiler.LinkerFile.sha256

`attribute` · `python`

```python
sha256: str
```

Source: `src/helia_profiler/platform/linker/model.py:22`

## helia_profiler.LinkerRegion

`class` · `python`

```python
LinkerRegion(
    region: MemoryRegion,
    window: MemoryRange,
    app_window: MemoryRange,
    allowed: tuple[MemoryRange, ...] | None,
    window_provenance: str,
    fixed_stack_bytes: int = 0,
    section_attributable: bool = True,
) -> None
```

`dataclass`

One script region and its separately derived application intervals.

``allowed=None`` means hardware admission is unavailable. An empty tuple
means the inputs are known and leave no space. Fixed stack reservations
lie outside ``app_window`` and are accounted separately from app usage.

**API tier:** `experimental`

Source: `src/helia_profiler/platform/linker/model.py:31`

### helia_profiler.LinkerRegion.region

`attribute` · `python`

```python
region: MemoryRegion
```

Source: `src/helia_profiler/platform/linker/model.py:40`

### helia_profiler.LinkerRegion.window

`attribute` · `python`

```python
window: MemoryRange
```

Source: `src/helia_profiler/platform/linker/model.py:41`

### helia_profiler.LinkerRegion.app_window

`attribute` · `python`

```python
app_window: MemoryRange
```

Source: `src/helia_profiler/platform/linker/model.py:42`

### helia_profiler.LinkerRegion.allowed

`attribute` · `python`

```python
allowed: tuple[MemoryRange, ...] | None
```

Source: `src/helia_profiler/platform/linker/model.py:43`

### helia_profiler.LinkerRegion.window_provenance

`attribute` · `python`

```python
window_provenance: str
```

Source: `src/helia_profiler/platform/linker/model.py:44`

### helia_profiler.LinkerRegion.fixed_stack_bytes

`attribute` · `python`

```python
fixed_stack_bytes: int = 0
```

Source: `src/helia_profiler/platform/linker/model.py:45`

### helia_profiler.LinkerRegion.section_attributable

`attribute` · `python`

```python
section_attributable: bool = True
```

Source: `src/helia_profiler/platform/linker/model.py:46`

### helia_profiler.LinkerRegion.capacity

`attribute` · `python`

```python
capacity: int | None
```

Conservative contiguous admission capacity; ``None`` remains unavailable.

Source: `src/helia_profiler/platform/linker/model.py:69`

## helia_profiler.LinkerInput

`class` · `python`

```python
LinkerInput(
    sdk_commit: str,
    soc_name: str,
    link_family: LinkFamily,
    linker_profile: str,
    script: LinkerFile,
    files: tuple[LinkerFile, ...],
    definition_sha256: str,
    admission_source: Literal['catalog-and-linker', 'sdk-descriptor', 'unavailable'],
    regions: tuple[LinkerRegion, ...],
) -> None
```

`dataclass`

Expected SDK/script input and its source-backed effective region data.

**API tier:** `experimental`

Source: `src/helia_profiler/platform/linker/model.py:86`

### helia_profiler.LinkerInput.sdk_commit

`attribute` · `python`

```python
sdk_commit: str
```

Source: `src/helia_profiler/platform/linker/model.py:90`

### helia_profiler.LinkerInput.soc_name

`attribute` · `python`

```python
soc_name: str
```

Source: `src/helia_profiler/platform/linker/model.py:91`

### helia_profiler.LinkerInput.link_family

`attribute` · `python`

```python
link_family: LinkFamily
```

Source: `src/helia_profiler/platform/linker/model.py:92`

### helia_profiler.LinkerInput.linker_profile

`attribute` · `python`

```python
linker_profile: str
```

Source: `src/helia_profiler/platform/linker/model.py:93`

### helia_profiler.LinkerInput.script

`attribute` · `python`

```python
script: LinkerFile
```

Source: `src/helia_profiler/platform/linker/model.py:94`

### helia_profiler.LinkerInput.files

`attribute` · `python`

```python
files: tuple[LinkerFile, ...]
```

Source: `src/helia_profiler/platform/linker/model.py:95`

### helia_profiler.LinkerInput.definition_sha256

`attribute` · `python`

```python
definition_sha256: str
```

Source: `src/helia_profiler/platform/linker/model.py:96`

### helia_profiler.LinkerInput.admission_source

`attribute` · `python`

```python
admission_source: Literal['catalog-and-linker', 'sdk-descriptor', 'unavailable']
```

Source: `src/helia_profiler/platform/linker/model.py:97`

### helia_profiler.LinkerInput.regions

`attribute` · `python`

```python
regions: tuple[LinkerRegion, ...]
```

Source: `src/helia_profiler/platform/linker/model.py:98`

## helia_profiler.LinkerVerification

`class` · `python`

```python
LinkerVerification(
    role: Literal['profile', 'power'],
    target: str,
    layout: LinkerInput | None,
    reason: str | None = None,
) -> None
```

`dataclass`

Actual applicability decision for one executable at one build boundary.

**API tier:** `experimental`

Source: `src/helia_profiler/platform/linker/model.py:108`

### helia_profiler.LinkerVerification.role

`attribute` · `python`

```python
role: Literal['profile', 'power']
```

Source: `src/helia_profiler/platform/linker/model.py:112`

### helia_profiler.LinkerVerification.target

`attribute` · `python`

```python
target: str
```

Source: `src/helia_profiler/platform/linker/model.py:113`

### helia_profiler.LinkerVerification.layout

`attribute` · `python`

```python
layout: LinkerInput | None
```

Source: `src/helia_profiler/platform/linker/model.py:114`

### helia_profiler.LinkerVerification.reason

`attribute` · `python`

```python
reason: str | None = None
```

Source: `src/helia_profiler/platform/linker/model.py:115`

### helia_profiler.LinkerVerification.verified

`attribute` · `python`

```python
verified: bool
```

Whether actual SDK/script applicability was verified, independently of fit.

Source: `src/helia_profiler/platform/linker/model.py:127`

### helia_profiler.LinkerVerification.to_dict

`method` · `python`

```python
to_dict() -> dict[str, Any]
```

Source: `src/helia_profiler/platform/linker/model.py:131`

### helia_profiler.LinkerVerification.from_dict

`method` · `python`

```python
from_dict(value: object) -> LinkerVerification
```

`classmethod`

Source: `src/helia_profiler/platform/linker/model.py:137`

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
    identity: 'RunIdentity | None' = None,
    conditions: Conditions | None = None,
    *,
    platform_provenance: PlatformSnapshot | None = None,
    linker_verifications: tuple[LinkerVerification, ...] = (),
) -> None
```

`dataclass`

Accumulated run metadata — enriched by stages, consumed by reports.

**API tier:** `stable`

Source: `src/helia_profiler/results/models.py:397`

### helia_profiler.RunMetadata.hpx_version

`attribute` · `python`

```python
hpx_version: str = ''
```

Source: `src/helia_profiler/results/models.py:401`

### helia_profiler.RunMetadata.run_id

`attribute` · `python`

```python
run_id: str = ''
```

Source: `src/helia_profiler/results/models.py:402`

### helia_profiler.RunMetadata.timestamp

`attribute` · `python`

```python
timestamp: str = ''
```

Source: `src/helia_profiler/results/models.py:403`

### helia_profiler.RunMetadata.config_snapshot

`attribute` · `python`

```python
config_snapshot: dict[str, Any] = field(default_factory=dict)
```

Source: `src/helia_profiler/results/models.py:404`

### helia_profiler.RunMetadata.platform

`attribute` · `python`

```python
platform: PlatformInfo | None = None
```

Source: `src/helia_profiler/results/models.py:405`

### helia_profiler.RunMetadata.platform_provenance

`attribute` · `python`

```python
platform_provenance: PlatformSnapshot | None = field(default=None, kw_only=True)
```

Source: `src/helia_profiler/results/models.py:406`

### helia_profiler.RunMetadata.linker_verifications

`attribute` · `python`

```python
linker_verifications: tuple[LinkerVerification, ...] = field(default=(), kw_only=True)
```

Source: `src/helia_profiler/results/models.py:407`

### helia_profiler.RunMetadata.model

`attribute` · `python`

```python
model: ModelInfo | None = None
```

Source: `src/helia_profiler/results/models.py:408`

### helia_profiler.RunMetadata.toolchain

`attribute` · `python`

```python
toolchain: ToolchainInfo | None = None
```

Source: `src/helia_profiler/results/models.py:409`

### helia_profiler.RunMetadata.build_images

`attribute` · `python`

```python
build_images: tuple[BuildImage, ...] = ()
```

Source: `src/helia_profiler/results/models.py:412`

### helia_profiler.RunMetadata.engine

`attribute` · `python`

```python
engine: EngineInfo | None = None
```

Source: `src/helia_profiler/results/models.py:413`

### helia_profiler.RunMetadata.firmware

`attribute` · `python`

```python
firmware: FirmwareMeta | None = None
```

Source: `src/helia_profiler/results/models.py:414`

### helia_profiler.RunMetadata.memory_plan

`attribute` · `python`

```python
memory_plan: 'MemoryPlan | None' = None
```

Source: `src/helia_profiler/results/models.py:415`

### helia_profiler.RunMetadata.timing

`attribute` · `python`

```python
timing: TimingInfo | None = None
```

Source: `src/helia_profiler/results/models.py:416`

### helia_profiler.RunMetadata.compatibility

`attribute` · `python`

```python
compatibility: CompatibilityResolution | None = None
```

Source: `src/helia_profiler/results/models.py:417`

### helia_profiler.RunMetadata.dependencies

`attribute` · `python`

```python
dependencies: 'DependencyProvenance | None' = None
```

Source: `src/helia_profiler/results/models.py:418`

### helia_profiler.RunMetadata.identity

`attribute` · `python`

```python
identity: 'RunIdentity | None' = None
```

Source: `src/helia_profiler/results/models.py:421`

### helia_profiler.RunMetadata.conditions

`attribute` · `python`

```python
conditions: Conditions | None = None
```

Source: `src/helia_profiler/results/models.py:424`
