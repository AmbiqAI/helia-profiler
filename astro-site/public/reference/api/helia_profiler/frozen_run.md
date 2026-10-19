# helia_profiler.frozen_run

Replayable run plans: every resolved setting, the model's hash and the toolchain and runtime identities, saved so another host can rebuild the same run.

Every name on this page is imported from `helia_profiler`.

**API tier:** `experimental`

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__`.

## helia_profiler.FrozenModel

`class` · `python`

```python
FrozenModel(path: str, sha256: str, size_bytes: int) -> None
```

`dataclass`

The model a plan was frozen against, identified by content.

**API tier:** `experimental`

Source: `src/helia_profiler/frozen_run.py:102`

### helia_profiler.FrozenModel.path

`attribute` · `python`

```python
path: str
```

Source: `src/helia_profiler/frozen_run.py:106`

### helia_profiler.FrozenModel.sha256

`attribute` · `python`

```python
sha256: str
```

Source: `src/helia_profiler/frozen_run.py:107`

### helia_profiler.FrozenModel.size_bytes

`attribute` · `python`

```python
size_bytes: int
```

Source: `src/helia_profiler/frozen_run.py:108`

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

Source: `src/helia_profiler/frozen_run.py:111`

### helia_profiler.ToolchainIdentity.name

`attribute` · `python`

```python
name: str
```

Source: `src/helia_profiler/frozen_run.py:120`

### helia_profiler.ToolchainIdentity.version

`attribute` · `python`

```python
version: str | None
```

Source: `src/helia_profiler/frozen_run.py:121`

### helia_profiler.ToolchainIdentity.unavailable_reason

`attribute` · `python`

```python
unavailable_reason: str | None = None
```

Source: `src/helia_profiler/frozen_run.py:122`

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

Source: `src/helia_profiler/frozen_run.py:125`

### helia_profiler.FrozenRuntime.name

`attribute` · `python`

```python
name: str
```

Source: `src/helia_profiler/frozen_run.py:134`

### helia_profiler.FrozenRuntime.version

`attribute` · `python`

```python
version: str | None
```

Source: `src/helia_profiler/frozen_run.py:135`

### helia_profiler.FrozenRuntime.source_commit

`attribute` · `python`

```python
source_commit: str | None
```

Source: `src/helia_profiler/frozen_run.py:136`

### helia_profiler.FrozenRuntime.kernels_module

`attribute` · `python`

```python
kernels_module: str | None = None
```

Source: `src/helia_profiler/frozen_run.py:137`

### helia_profiler.FrozenRuntime.kernels_commit

`attribute` · `python`

```python
kernels_commit: str | None = None
```

Source: `src/helia_profiler/frozen_run.py:138`

### helia_profiler.FrozenRuntime.unavailable_reason

`attribute` · `python`

```python
unavailable_reason: str | None = None
```

Source: `src/helia_profiler/frozen_run.py:139`

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

Source: `src/helia_profiler/frozen_run.py:142`

### helia_profiler.LocalInput.setting

`attribute` · `python`

```python
setting: str
```

Source: `src/helia_profiler/frozen_run.py:150`

### helia_profiler.LocalInput.path

`attribute` · `python`

```python
path: str
```

Source: `src/helia_profiler/frozen_run.py:151`

### helia_profiler.LocalInput.kind

`attribute` · `python`

```python
kind: str
```

Source: `src/helia_profiler/frozen_run.py:152`

### helia_profiler.LocalInput.sha256

`attribute` · `python`

```python
sha256: str | None = None
```

Source: `src/helia_profiler/frozen_run.py:153`

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
) -> None
```

`dataclass`

A resolved, replayable run plan. Build it with :meth:`Session.freeze`.

**API tier:** `experimental`

Source: `src/helia_profiler/frozen_run.py:164`

### helia_profiler.FrozenRun.settings

`attribute` · `python`

```python
settings: Mapping[str, Any]
```

Source: `src/helia_profiler/frozen_run.py:168`

### helia_profiler.FrozenRun.model

`attribute` · `python`

```python
model: FrozenModel
```

Source: `src/helia_profiler/frozen_run.py:169`

### helia_profiler.FrozenRun.baseline_id

`attribute` · `python`

```python
baseline_id: str
```

Source: `src/helia_profiler/frozen_run.py:170`

### helia_profiler.FrozenRun.baseline_fingerprint

`attribute` · `python`

```python
baseline_fingerprint: str
```

Source: `src/helia_profiler/frozen_run.py:171`

### helia_profiler.FrozenRun.qualification

`attribute` · `python`

```python
qualification: str
```

Source: `src/helia_profiler/frozen_run.py:172`

### helia_profiler.FrozenRun.overrides

`attribute` · `python`

```python
overrides: tuple[str, ...]
```

Source: `src/helia_profiler/frozen_run.py:173`

### helia_profiler.FrozenRun.local_inputs

`attribute` · `python`

```python
local_inputs: tuple[LocalInput, ...]
```

Source: `src/helia_profiler/frozen_run.py:174`

### helia_profiler.FrozenRun.environment

`attribute` · `python`

```python
environment: Mapping[str, str]
```

Source: `src/helia_profiler/frozen_run.py:175`

### helia_profiler.FrozenRun.toolchain

`attribute` · `python`

```python
toolchain: ToolchainIdentity
```

Source: `src/helia_profiler/frozen_run.py:176`

### helia_profiler.FrozenRun.runtime

`attribute` · `python`

```python
runtime: FrozenRuntime | None
```

Source: `src/helia_profiler/frozen_run.py:177`

### helia_profiler.FrozenRun.hpx_version

`attribute` · `python`

```python
hpx_version: str
```

Source: `src/helia_profiler/frozen_run.py:178`

### helia_profiler.FrozenRun.intent_hash

`attribute` · `python`

```python
intent_hash: str
```

SHA-256 of what this plan asks for; the HPX release is not part of it.

Source: `src/helia_profiler/frozen_run.py:189`

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

Source: `src/helia_profiler/frozen_run.py:194`

### helia_profiler.FrozenRun.to_dict

`method` · `python`

```python
to_dict() -> dict[str, Any]
```

Source: `src/helia_profiler/frozen_run.py:208`

### helia_profiler.FrozenRun.from_dict

`method` · `python`

```python
from_dict(data: Mapping[str, Any]) -> FrozenRun
```

`classmethod`

Source: `src/helia_profiler/frozen_run.py:225`

### helia_profiler.FrozenRun.save

`method` · `python`

```python
save(path: str | Path) -> Path
```

Write the plan as JSON and return its path.

Source: `src/helia_profiler/frozen_run.py:274`

### helia_profiler.FrozenRun.load

`method` · `python`

```python
load(path: str | Path) -> FrozenRun
```

`classmethod`

Read a plan written by :meth:`save`, verifying its intent hash.

Source: `src/helia_profiler/frozen_run.py:283`

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

Source: `src/helia_profiler/frozen_run.py:297`

### helia_profiler.FrozenRun.build

`method` · `python`

```python
build(fixture: TypedFixture, **options: Any = {}) -> CompiledBuild
```

Compile this plan for *fixture*'s pinned inputs; see :func:`~helia_profiler.build`.

Source: `src/helia_profiler/frozen_run.py:346`
