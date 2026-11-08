# helia_profiler.frozen_run

Replayable run plans: every resolved setting, the model's hash and the toolchain and runtime identities, saved so another host can rebuild the same run. Retained platform snapshots identify the catalog selection and effective profiler settings.

Every name on this page is imported from `helia_profiler`.

**API tier:** `experimental`

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__`.

## helia_profiler.CatalogSource

`class` · `python`

```python
CatalogSource(label: str, schema_version: int, sha256: str, distribution_version: str | None = None) -> None
```

`dataclass`

Identity of an explicit source included in a catalog.

**API tier:** `experimental`

Source: `src/helia_profiler/platform/provenance.py:20`

### helia_profiler.CatalogSource.label

`attribute` · `python`

```python
label: str
```

Source: `src/helia_profiler/platform/provenance.py:24`

### helia_profiler.CatalogSource.schema_version

`attribute` · `python`

```python
schema_version: int
```

Source: `src/helia_profiler/platform/provenance.py:25`

### helia_profiler.CatalogSource.sha256

`attribute` · `python`

```python
sha256: str
```

Source: `src/helia_profiler/platform/provenance.py:26`

### helia_profiler.CatalogSource.distribution_version

`attribute` · `python`

```python
distribution_version: str | None = None
```

Source: `src/helia_profiler/platform/provenance.py:27`

## helia_profiler.CatalogProvenance

`class` · `python`

```python
CatalogProvenance(
    package_version: str,
    schema_version: int,
    catalog_digest: str,
    sources: tuple[CatalogSource, ...] = (),
) -> None
```

`dataclass`

Catalog package, schema and data identities recorded at resolution.

**API tier:** `experimental`

Source: `src/helia_profiler/platform/provenance.py:30`

### helia_profiler.CatalogProvenance.package_version

`attribute` · `python`

```python
package_version: str
```

Source: `src/helia_profiler/platform/provenance.py:34`

### helia_profiler.CatalogProvenance.schema_version

`attribute` · `python`

```python
schema_version: int
```

Source: `src/helia_profiler/platform/provenance.py:35`

### helia_profiler.CatalogProvenance.catalog_digest

`attribute` · `python`

```python
catalog_digest: str
```

Source: `src/helia_profiler/platform/provenance.py:36`

### helia_profiler.CatalogProvenance.sources

`attribute` · `python`

```python
sources: tuple[CatalogSource, ...] = ()
```

Source: `src/helia_profiler/platform/provenance.py:37`

## helia_profiler.TargetSelection

`class` · `python`

```python
TargetSelection(
    origin: Literal['catalog', 'hpx-custom', 'sdk-descriptor'],
    catalog: CatalogProvenance | None,
    target_id: str | None = None,
    core: str | None = None,
    boot: str | None = None,
    implementation: str | None = None,
    addons: tuple[str, ...] = (),
    compatibility: tuple[str, ...] = (),
) -> None
```

`dataclass`

Requested target resolution and named HPX compatibility choices.

**API tier:** `experimental`

Source: `src/helia_profiler/platform/provenance.py:40`

### helia_profiler.TargetSelection.origin

`attribute` · `python`

```python
origin: Literal['catalog', 'hpx-custom', 'sdk-descriptor']
```

Source: `src/helia_profiler/platform/provenance.py:44`

### helia_profiler.TargetSelection.catalog

`attribute` · `python`

```python
catalog: CatalogProvenance | None
```

Source: `src/helia_profiler/platform/provenance.py:45`

### helia_profiler.TargetSelection.target_id

`attribute` · `python`

```python
target_id: str | None = None
```

Source: `src/helia_profiler/platform/provenance.py:46`

### helia_profiler.TargetSelection.core

`attribute` · `python`

```python
core: str | None = None
```

Source: `src/helia_profiler/platform/provenance.py:47`

### helia_profiler.TargetSelection.boot

`attribute` · `python`

```python
boot: str | None = None
```

Source: `src/helia_profiler/platform/provenance.py:48`

### helia_profiler.TargetSelection.implementation

`attribute` · `python`

```python
implementation: str | None = None
```

Source: `src/helia_profiler/platform/provenance.py:49`

### helia_profiler.TargetSelection.addons

`attribute` · `python`

```python
addons: tuple[str, ...] = ()
```

Source: `src/helia_profiler/platform/provenance.py:50`

### helia_profiler.TargetSelection.compatibility

`attribute` · `python`

```python
compatibility: tuple[str, ...] = ()
```

Source: `src/helia_profiler/platform/provenance.py:51`

## helia_profiler.RegionExtent

`class` · `python`

```python
RegionExtent(region: MemoryRegion, extent: MemoryRange) -> None
```

`dataclass`

A retained application address range for one linker region.

**API tier:** `experimental`

Source: `src/helia_profiler/platform/provenance.py:54`

### helia_profiler.RegionExtent.region

`attribute` · `python`

```python
region: MemoryRegion
```

Source: `src/helia_profiler/platform/provenance.py:58`

### helia_profiler.RegionExtent.extent

`attribute` · `python`

```python
extent: MemoryRange
```

Source: `src/helia_profiler/platform/provenance.py:59`

## helia_profiler.PlatformSnapshot

`class` · `python`

```python
PlatformSnapshot(
    board: BoardDef,
    soc: SocDef,
    selection: TargetSelection,
    link_family: LinkFamily,
    linker_profile: str,
    app_regions: tuple[RegionExtent, ...],
    load_address: int | None,
    expected_linker: LinkerInput | None = None,
) -> None
```

`dataclass`

Effective HPX platform views and the catalog selection that supplied their facts.

**API tier:** `experimental`

Source: `src/helia_profiler/platform/provenance.py:62`

### helia_profiler.PlatformSnapshot.board

`attribute` · `python`

```python
board: BoardDef
```

Source: `src/helia_profiler/platform/provenance.py:66`

### helia_profiler.PlatformSnapshot.soc

`attribute` · `python`

```python
soc: SocDef
```

Source: `src/helia_profiler/platform/provenance.py:67`

### helia_profiler.PlatformSnapshot.selection

`attribute` · `python`

```python
selection: TargetSelection
```

Source: `src/helia_profiler/platform/provenance.py:68`

### helia_profiler.PlatformSnapshot.link_family

`attribute` · `python`

```python
link_family: LinkFamily
```

Source: `src/helia_profiler/platform/provenance.py:69`

### helia_profiler.PlatformSnapshot.linker_profile

`attribute` · `python`

```python
linker_profile: str
```

Source: `src/helia_profiler/platform/provenance.py:70`

### helia_profiler.PlatformSnapshot.app_regions

`attribute` · `python`

```python
app_regions: tuple[RegionExtent, ...]
```

Source: `src/helia_profiler/platform/provenance.py:71`

### helia_profiler.PlatformSnapshot.load_address

`attribute` · `python`

```python
load_address: int | None
```

Source: `src/helia_profiler/platform/provenance.py:72`

### helia_profiler.PlatformSnapshot.expected_linker

`attribute` · `python`

```python
expected_linker: LinkerInput | None = None
```

Source: `src/helia_profiler/platform/provenance.py:73`

### helia_profiler.PlatformSnapshot.to_dict

`method` · `python`

```python
to_dict() -> dict[str, Any]
```

Source: `src/helia_profiler/platform/provenance.py:86`

### helia_profiler.PlatformSnapshot.from_dict

`method` · `python`

```python
from_dict(value: object) -> PlatformSnapshot
```

`classmethod`

Source: `src/helia_profiler/platform/provenance.py:92`

### helia_profiler.PlatformSnapshot.verify_current

`method` · `python`

```python
verify_current() -> None
```

Source: `src/helia_profiler/platform/provenance.py:101`

## helia_profiler.FrozenModel

`class` · `python`

```python
FrozenModel(path: str, sha256: str, size_bytes: int) -> None
```

`dataclass`

The model a plan was frozen against, identified by content.

**API tier:** `experimental`

Source: `src/helia_profiler/runs/plan.py:106`

### helia_profiler.FrozenModel.path

`attribute` · `python`

```python
path: str
```

Source: `src/helia_profiler/runs/plan.py:110`

### helia_profiler.FrozenModel.sha256

`attribute` · `python`

```python
sha256: str
```

Source: `src/helia_profiler/runs/plan.py:111`

### helia_profiler.FrozenModel.size_bytes

`attribute` · `python`

```python
size_bytes: int
```

Source: `src/helia_profiler/runs/plan.py:112`

## helia_profiler.ToolchainIdentity

`class` · `python`

```python
ToolchainIdentity(name: str, version: str | None, unavailable_reason: str | None = None) -> None
```

`dataclass`

The compiler a plan expects: name and exact version, never a path.

``version`` holds the version-bearing lines of ``<compiler> --version``;
it is ``None`` when the compiler could not be run, with the reason in
``unavailable_reason``.

**API tier:** `experimental`

Source: `src/helia_profiler/runs/plan.py:118`

### helia_profiler.ToolchainIdentity.name

`attribute` · `python`

```python
name: str
```

Source: `src/helia_profiler/runs/plan.py:127`

### helia_profiler.ToolchainIdentity.version

`attribute` · `python`

```python
version: str | None
```

Source: `src/helia_profiler/runs/plan.py:128`

### helia_profiler.ToolchainIdentity.unavailable_reason

`attribute` · `python`

```python
unavailable_reason: str | None = None
```

Source: `src/helia_profiler/runs/plan.py:129`

### helia_profiler.ToolchainIdentity.lines

`attribute` · `python`

```python
lines: tuple[str, ...]
```

The version-bearing lines ``version`` joins, or none when it is unknown.

Source: `src/helia_profiler/runs/plan.py:132`

## helia_profiler.FrozenRuntime

`class` · `python`

```python
FrozenRuntime(
    name: str,
    version: str | None,
    source_commit: str | None,
    kernels_module: str | None = None,
    kernels_commit: str | None = None,
    unavailable_reason: str | None = None,
) -> None
```

`dataclass`

The engine and kernel sources a profile build uses.

Commits are NSX module or source revisions from the compatibility
baseline or the installed package. ``kernels_commit`` is ``None`` when the
engine links no kernel library or its provider is overridden.

**API tier:** `experimental`

Source: `src/helia_profiler/runs/plan.py:137`

### helia_profiler.FrozenRuntime.name

`attribute` · `python`

```python
name: str
```

Source: `src/helia_profiler/runs/plan.py:146`

### helia_profiler.FrozenRuntime.version

`attribute` · `python`

```python
version: str | None
```

Source: `src/helia_profiler/runs/plan.py:147`

### helia_profiler.FrozenRuntime.source_commit

`attribute` · `python`

```python
source_commit: str | None
```

Source: `src/helia_profiler/runs/plan.py:148`

### helia_profiler.FrozenRuntime.kernels_module

`attribute` · `python`

```python
kernels_module: str | None = None
```

Source: `src/helia_profiler/runs/plan.py:149`

### helia_profiler.FrozenRuntime.kernels_commit

`attribute` · `python`

```python
kernels_commit: str | None = None
```

Source: `src/helia_profiler/runs/plan.py:150`

### helia_profiler.FrozenRuntime.unavailable_reason

`attribute` · `python`

```python
unavailable_reason: str | None = None
```

Source: `src/helia_profiler/runs/plan.py:151`

## helia_profiler.LocalInput

`class` · `python`

```python
LocalInput(setting: str, path: str, kind: str, sha256: str | None = None) -> None
```

`dataclass`

A local file or directory the build reads instead of a pinned source.

Files are identified by content. A directory is recorded by path only, so
a plan with one is not portable to another host.

**API tier:** `experimental`

Source: `src/helia_profiler/runs/plan.py:154`

### helia_profiler.LocalInput.setting

`attribute` · `python`

```python
setting: str
```

Source: `src/helia_profiler/runs/plan.py:162`

### helia_profiler.LocalInput.path

`attribute` · `python`

```python
path: str
```

Source: `src/helia_profiler/runs/plan.py:163`

### helia_profiler.LocalInput.kind

`attribute` · `python`

```python
kind: str
```

Source: `src/helia_profiler/runs/plan.py:164`

### helia_profiler.LocalInput.sha256

`attribute` · `python`

```python
sha256: str | None = None
```

Source: `src/helia_profiler/runs/plan.py:165`

## helia_profiler.FrozenRun

`class` · `python`

```python
FrozenRun(
    settings: Mapping[str, Any],
    model: FrozenModel,
    baseline_id: str,
    baseline_fingerprint: str,
    qualification: str,
    overrides: tuple[str, ...],
    local_inputs: tuple[LocalInput, ...],
    environment: Mapping[str, str],
    toolchain: ToolchainIdentity,
    runtime: FrozenRuntime | None,
    hpx_version: str,
    platform: PlatformSnapshot | None = None,
) -> None
```

`dataclass`

A resolved, replayable run plan. Build it with :meth:`Session.freeze`.

**API tier:** `experimental`

Source: `src/helia_profiler/runs/plan.py:176`

### helia_profiler.FrozenRun.settings

`attribute` · `python`

```python
settings: Mapping[str, Any]
```

Source: `src/helia_profiler/runs/plan.py:180`

### helia_profiler.FrozenRun.model

`attribute` · `python`

```python
model: FrozenModel
```

Source: `src/helia_profiler/runs/plan.py:181`

### helia_profiler.FrozenRun.baseline_id

`attribute` · `python`

```python
baseline_id: str
```

Source: `src/helia_profiler/runs/plan.py:182`

### helia_profiler.FrozenRun.baseline_fingerprint

`attribute` · `python`

```python
baseline_fingerprint: str
```

Source: `src/helia_profiler/runs/plan.py:183`

### helia_profiler.FrozenRun.qualification

`attribute` · `python`

```python
qualification: str
```

Source: `src/helia_profiler/runs/plan.py:184`

### helia_profiler.FrozenRun.overrides

`attribute` · `python`

```python
overrides: tuple[str, ...]
```

Source: `src/helia_profiler/runs/plan.py:185`

### helia_profiler.FrozenRun.local_inputs

`attribute` · `python`

```python
local_inputs: tuple[LocalInput, ...]
```

Source: `src/helia_profiler/runs/plan.py:186`

### helia_profiler.FrozenRun.environment

`attribute` · `python`

```python
environment: Mapping[str, str]
```

Source: `src/helia_profiler/runs/plan.py:187`

### helia_profiler.FrozenRun.toolchain

`attribute` · `python`

```python
toolchain: ToolchainIdentity
```

Source: `src/helia_profiler/runs/plan.py:188`

### helia_profiler.FrozenRun.runtime

`attribute` · `python`

```python
runtime: FrozenRuntime | None
```

Source: `src/helia_profiler/runs/plan.py:189`

### helia_profiler.FrozenRun.hpx_version

`attribute` · `python`

```python
hpx_version: str
```

Source: `src/helia_profiler/runs/plan.py:190`

### helia_profiler.FrozenRun.platform

`attribute` · `python`

```python
platform: PlatformSnapshot | None = None
```

Source: `src/helia_profiler/runs/plan.py:191`

### helia_profiler.FrozenRun.intent_hash

`attribute` · `python`

```python
intent_hash: str
```

The ``hpx.intent`` hash of what this plan asks for; the HPX release is not part of it.

Source: `src/helia_profiler/runs/plan.py:202`

### helia_profiler.FrozenRun.portable

`attribute` · `python`

```python
portable: bool
```

Whether every input is pinned or content-identified, so another host can rebuild it.

A compiler environment variable makes a plan host-specific, since the
files it may name are not tracked, and so do a dependency selector
that names a branch, tag or version rather than a commit and a custom
heliaRT release, whose archive is not content-identified.

Source: `src/helia_profiler/runs/plan.py:207`

### helia_profiler.FrozenRun.to_dict

`method` · `python`

```python
to_dict() -> dict[str, Any]
```

Serialize a complete plan, refusing missing recorded platform input.

Source: `src/helia_profiler/runs/plan.py:221`

### helia_profiler.FrozenRun.from_dict

`method` · `python`

```python
from_dict(data: Mapping[str, Any]) -> FrozenRun
```

`classmethod`

Source: `src/helia_profiler/runs/plan.py:245`

### helia_profiler.FrozenRun.save

`method` · `python`

```python
save(path: str | Path) -> Path
```

Write the plan as JSON, refusing its source model path or aliases.

Source: `src/helia_profiler/runs/plan.py:306`

### helia_profiler.FrozenRun.load

`method` · `python`

```python
load(path: str | Path) -> FrozenRun
```

`classmethod`

Read a plan written by :meth:`save`, verifying its intent hash.

Source: `src/helia_profiler/runs/plan.py:324`

### helia_profiler.FrozenRun.config

`method` · `python`

```python
config(*, model: str | Path | None = None, host: Mapping[str, Any] | None = None) -> ProfileConfig
```

The :class:`ProfileConfig` this plan describes, on this host.

*host* supplies host-local settings for the replaying host, for
example ``{"target": {"jlink_serial": "..."}, "output": {"dir": "..."}}``;
any other key is refused. Refuses when this HPX carries a different
baseline, when the model (the recorded path, or *model* when it moved)
has a different hash, when a local input differs or is missing, or
when this host would build something else: a source-path environment
variable set, unset or changed, another compiler version, or another
engine runtime. Only the model can move; other local files must be
at their recorded paths.

Source: `src/helia_profiler/runs/plan.py:338`

### helia_profiler.FrozenRun.build

`method` · `python`

```python
build(fixture: TypedFixture, **options: Any = {}) -> CompiledBuild
```

Compile this plan for *fixture*'s pinned inputs; see :func:`~helia_profiler.build`.

Source: `src/helia_profiler/runs/plan.py:387`
