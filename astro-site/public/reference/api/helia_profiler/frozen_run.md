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

Source: `src/helia_profiler/frozen_run.py:43`

### helia_profiler.FrozenModel.path

`attribute` · `python`

```python
path: str
```

Source: `src/helia_profiler/frozen_run.py:47`

### helia_profiler.FrozenModel.sha256

`attribute` · `python`

```python
sha256: str
```

Source: `src/helia_profiler/frozen_run.py:48`

### helia_profiler.FrozenModel.size_bytes

`attribute` · `python`

```python
size_bytes: int
```

Source: `src/helia_profiler/frozen_run.py:49`

## helia_profiler.ToolchainIdentity

`class` · `python`

```python
ToolchainIdentity(name: str, version: str | None, unavailable_reason: str | None = None) -> None
```

`dataclass`

The compiler a plan expects: name and exact version, never a path.

``version`` is ``None`` when the compiler could not be probed at freeze
time; ``unavailable_reason`` then says why.

**API tier:** `experimental`

Source: `src/helia_profiler/frozen_run.py:52`

### helia_profiler.ToolchainIdentity.name

`attribute` · `python`

```python
name: str
```

Source: `src/helia_profiler/frozen_run.py:60`

### helia_profiler.ToolchainIdentity.version

`attribute` · `python`

```python
version: str | None
```

Source: `src/helia_profiler/frozen_run.py:61`

### helia_profiler.ToolchainIdentity.unavailable_reason

`attribute` · `python`

```python
unavailable_reason: str | None = None
```

Source: `src/helia_profiler/frozen_run.py:62`

## helia_profiler.FrozenRuntime

`class` · `python`

```python
FrozenRuntime(
    name: str,
    version: str | None,
    source_commit: str | None,
    kernels_commit: str | None = None,
    archive_sha256: str | None = None,
    unavailable_reason: str | None = None,
) -> None
```

`dataclass`

The runtime record the engine resolves to, or why there is none.

**API tier:** `experimental`

Source: `src/helia_profiler/frozen_run.py:65`

### helia_profiler.FrozenRuntime.name

`attribute` · `python`

```python
name: str
```

Source: `src/helia_profiler/frozen_run.py:69`

### helia_profiler.FrozenRuntime.version

`attribute` · `python`

```python
version: str | None
```

Source: `src/helia_profiler/frozen_run.py:70`

### helia_profiler.FrozenRuntime.source_commit

`attribute` · `python`

```python
source_commit: str | None
```

Source: `src/helia_profiler/frozen_run.py:71`

### helia_profiler.FrozenRuntime.kernels_commit

`attribute` · `python`

```python
kernels_commit: str | None = None
```

Source: `src/helia_profiler/frozen_run.py:72`

### helia_profiler.FrozenRuntime.archive_sha256

`attribute` · `python`

```python
archive_sha256: str | None = None
```

Source: `src/helia_profiler/frozen_run.py:73`

### helia_profiler.FrozenRuntime.unavailable_reason

`attribute` · `python`

```python
unavailable_reason: str | None = None
```

Source: `src/helia_profiler/frozen_run.py:74`

## helia_profiler.FrozenRun

`class` · `python`

```python
FrozenRun(
    settings: Mapping[str, Any],
    model: FrozenModel,
    baseline_id: str,
    baseline_fingerprint: str,
    toolchain: ToolchainIdentity,
    runtime: FrozenRuntime | None,
    hpx_version: str,
) -> None
```

`dataclass`

A resolved, replayable run plan. Build it with :meth:`Session.freeze`.

**API tier:** `experimental`

Source: `src/helia_profiler/frozen_run.py:77`

### helia_profiler.FrozenRun.settings

`attribute` · `python`

```python
settings: Mapping[str, Any]
```

Source: `src/helia_profiler/frozen_run.py:81`

### helia_profiler.FrozenRun.model

`attribute` · `python`

```python
model: FrozenModel
```

Source: `src/helia_profiler/frozen_run.py:82`

### helia_profiler.FrozenRun.baseline_id

`attribute` · `python`

```python
baseline_id: str
```

Source: `src/helia_profiler/frozen_run.py:83`

### helia_profiler.FrozenRun.baseline_fingerprint

`attribute` · `python`

```python
baseline_fingerprint: str
```

Source: `src/helia_profiler/frozen_run.py:84`

### helia_profiler.FrozenRun.toolchain

`attribute` · `python`

```python
toolchain: ToolchainIdentity
```

Source: `src/helia_profiler/frozen_run.py:85`

### helia_profiler.FrozenRun.runtime

`attribute` · `python`

```python
runtime: FrozenRuntime | None
```

Source: `src/helia_profiler/frozen_run.py:86`

### helia_profiler.FrozenRun.hpx_version

`attribute` · `python`

```python
hpx_version: str
```

Source: `src/helia_profiler/frozen_run.py:87`

### helia_profiler.FrozenRun.intent_hash

`attribute` · `python`

```python
intent_hash: str
```

SHA-256 of what this plan asks for, excluding host-local settings and the HPX release.

Source: `src/helia_profiler/frozen_run.py:95`

### helia_profiler.FrozenRun.to_dict

`method` · `python`

```python
to_dict() -> dict[str, Any]
```

Source: `src/helia_profiler/frozen_run.py:99`

### helia_profiler.FrozenRun.from_dict

`method` · `python`

```python
from_dict(data: Mapping[str, Any]) -> FrozenRun
```

`classmethod`

Source: `src/helia_profiler/frozen_run.py:112`

### helia_profiler.FrozenRun.save

`method` · `python`

```python
save(path: str | Path) -> Path
```

Write the plan as JSON and return its path.

Source: `src/helia_profiler/frozen_run.py:138`

### helia_profiler.FrozenRun.load

`method` · `python`

```python
load(path: str | Path) -> FrozenRun
```

`classmethod`

Read a plan written by :meth:`save`, verifying its intent hash.

Source: `src/helia_profiler/frozen_run.py:147`

### helia_profiler.FrozenRun.config

`method` · `python`

```python
config(*, model: str | Path | None = None) -> ProfileConfig
```

The :class:`ProfileConfig` this plan describes, on this host.

Refuses when this HPX carries a different compatibility baseline, or
when the model file (the recorded path, or *model* when the file has
moved) no longer has the recorded hash.

Source: `src/helia_profiler/frozen_run.py:161`
