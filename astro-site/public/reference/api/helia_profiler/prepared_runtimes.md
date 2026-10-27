# helia_profiler.prepared_runtimes

The runtimes a build links and how they are identified: prepared upstream archives, their verified manifests, and the kernel, runtime and engine sources a build records.

Every name on this page is imported from `helia_profiler`.

**API tier:** `experimental`

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__`.

## helia_profiler.ResolverPlan

`class` · `python`

```python
ResolverPlan(mode: str, registrations: tuple[str, ...]) -> None
```

`dataclass`

Concrete registration plan for generated firmware.

**API tier:** `experimental`

Source: `src/helia_profiler/firmware/op_resolver.py:14`

### helia_profiler.ResolverPlan.mode

`attribute` · `python`

```python
mode: str
```

Source: `src/helia_profiler/firmware/op_resolver.py:18`

### helia_profiler.ResolverPlan.registrations

`attribute` · `python`

```python
registrations: tuple[str, ...]
```

Source: `src/helia_profiler/firmware/op_resolver.py:19`

### helia_profiler.ResolverPlan.max_ops

`attribute` · `python`

```python
max_ops: int
```

Source: `src/helia_profiler/firmware/op_resolver.py:22`

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

## helia_profiler.RuntimeProvider

`class` · `python`

```python
RuntimeProvider(name: str, url: str, revision: str) -> None
```

`dataclass`

**API tier:** `experimental`

Source: `src/helia_profiler/runtime/archive.py:40`

### helia_profiler.RuntimeProvider.name

`attribute` · `python`

```python
name: str
```

Source: `src/helia_profiler/runtime/archive.py:42`

### helia_profiler.RuntimeProvider.url

`attribute` · `python`

```python
url: str
```

Source: `src/helia_profiler/runtime/archive.py:43`

### helia_profiler.RuntimeProvider.revision

`attribute` · `python`

```python
revision: str
```

Source: `src/helia_profiler/runtime/archive.py:44`

## helia_profiler.RuntimeABI

`class` · `python`

```python
RuntimeABI(toolchain: str, cpu: str, float_abi: str, short_enums: bool) -> None
```

`dataclass`

Manifest-declared ABI; archive members are not independently inspected.

**API tier:** `experimental`

Source: `src/helia_profiler/runtime/archive.py:47`

### helia_profiler.RuntimeABI.toolchain

`attribute` · `python`

```python
toolchain: str
```

Source: `src/helia_profiler/runtime/archive.py:51`

### helia_profiler.RuntimeABI.cpu

`attribute` · `python`

```python
cpu: str
```

Source: `src/helia_profiler/runtime/archive.py:52`

### helia_profiler.RuntimeABI.float_abi

`attribute` · `python`

```python
float_abi: str
```

Source: `src/helia_profiler/runtime/archive.py:53`

### helia_profiler.RuntimeABI.short_enums

`attribute` · `python`

```python
short_enums: bool
```

Source: `src/helia_profiler/runtime/archive.py:54`

## helia_profiler.RuntimeHeader

`class` · `python`

```python
RuntimeHeader(name: str, sha256: str) -> None
```

`dataclass`

**API tier:** `experimental`

Source: `src/helia_profiler/runtime/archive.py:57`

### helia_profiler.RuntimeHeader.name

`attribute` · `python`

```python
name: str
```

Source: `src/helia_profiler/runtime/archive.py:59`

### helia_profiler.RuntimeHeader.sha256

`attribute` · `python`

```python
sha256: str
```

Source: `src/helia_profiler/runtime/archive.py:60`

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

## helia_profiler.PreparedRuntimeManifest

`class` · `python`

```python
PreparedRuntimeManifest(
    schema_version: int,
    archive_sha256: str,
    providers: tuple[RuntimeProvider, ...],
    abi: RuntimeABI,
    headers: tuple[RuntimeHeader, ...],
    include_dirs: tuple[str, ...],
    stack: str = 'upstream',
    consumer_defines: tuple[str, ...] = _UPSTREAM_DEFINES,
    kernel_dir: str | None = None,
) -> None
```

`dataclass`

**API tier:** `experimental`

Source: `src/helia_profiler/runtime/archive.py:89`

### helia_profiler.PreparedRuntimeManifest.schema_version

`attribute` · `python`

```python
schema_version: int
```

Source: `src/helia_profiler/runtime/archive.py:91`

### helia_profiler.PreparedRuntimeManifest.archive_sha256

`attribute` · `python`

```python
archive_sha256: str
```

Source: `src/helia_profiler/runtime/archive.py:92`

### helia_profiler.PreparedRuntimeManifest.providers

`attribute` · `python`

```python
providers: tuple[RuntimeProvider, ...]
```

Source: `src/helia_profiler/runtime/archive.py:93`

### helia_profiler.PreparedRuntimeManifest.abi

`attribute` · `python`

```python
abi: RuntimeABI
```

Source: `src/helia_profiler/runtime/archive.py:94`

### helia_profiler.PreparedRuntimeManifest.headers

`attribute` · `python`

```python
headers: tuple[RuntimeHeader, ...]
```

Source: `src/helia_profiler/runtime/archive.py:95`

### helia_profiler.PreparedRuntimeManifest.include_dirs

`attribute` · `python`

```python
include_dirs: tuple[str, ...]
```

Source: `src/helia_profiler/runtime/archive.py:96`

### helia_profiler.PreparedRuntimeManifest.stack

`attribute` · `python`

```python
stack: str = 'upstream'
```

Source: `src/helia_profiler/runtime/archive.py:97`

### helia_profiler.PreparedRuntimeManifest.consumer_defines

`attribute` · `python`

```python
consumer_defines: tuple[str, ...] = _UPSTREAM_DEFINES
```

Source: `src/helia_profiler/runtime/archive.py:99`

### helia_profiler.PreparedRuntimeManifest.kernel_dir

`attribute` · `python`

```python
kernel_dir: str | None = None
```

Source: `src/helia_profiler/runtime/archive.py:100`

## helia_profiler.VerifiedPreparedRuntime

`class` · `python`

```python
VerifiedPreparedRuntime(archive: FixtureFile, header_root: Path, manifest: FixtureFile, record: RuntimeManifest) -> None
```

`dataclass`

**API tier:** `experimental`

Source: `src/helia_profiler/runtime/archive.py:143`

### helia_profiler.VerifiedPreparedRuntime.archive

`attribute` · `python`

```python
archive: FixtureFile
```

Source: `src/helia_profiler/runtime/archive.py:145`

### helia_profiler.VerifiedPreparedRuntime.header_root

`attribute` · `python`

```python
header_root: Path
```

Source: `src/helia_profiler/runtime/archive.py:146`

### helia_profiler.VerifiedPreparedRuntime.manifest

`attribute` · `python`

```python
manifest: FixtureFile
```

Source: `src/helia_profiler/runtime/archive.py:147`

### helia_profiler.VerifiedPreparedRuntime.record

`attribute` · `python`

```python
record: RuntimeManifest
```

Source: `src/helia_profiler/runtime/archive.py:148`

### helia_profiler.VerifiedPreparedRuntime.header_path

`method` · `python`

```python
header_path(name: str) -> Path
```

Source: `src/helia_profiler/runtime/archive.py:150`

## helia_profiler.PreparedUpstreamRuntime

`class` · `python`

```python
PreparedUpstreamRuntime(archive: FixtureFile, header_root: Path, manifest: FixtureFile) -> None
```

`dataclass`

Explicit prebuilt provider with a pinned, vendored header closure.

Manifest schema 1 declares the upstream TFLM + CMSIS-NN stack; schema 2
declares its ``stack`` (``helia-rt``) and the consumer defines and kernel
directory it was built with.

**API tier:** `experimental`

Source: `src/helia_profiler/runtime/archive.py:154`

### helia_profiler.PreparedUpstreamRuntime.archive

`attribute` · `python`

```python
archive: FixtureFile
```

Source: `src/helia_profiler/runtime/archive.py:163`

### helia_profiler.PreparedUpstreamRuntime.header_root

`attribute` · `python`

```python
header_root: Path
```

Source: `src/helia_profiler/runtime/archive.py:164`

### helia_profiler.PreparedUpstreamRuntime.manifest

`attribute` · `python`

```python
manifest: FixtureFile
```

Source: `src/helia_profiler/runtime/archive.py:165`

### helia_profiler.PreparedUpstreamRuntime.verify

`method` · `python`

```python
verify() -> VerifiedPreparedRuntime
```

Source: `src/helia_profiler/runtime/archive.py:167`
