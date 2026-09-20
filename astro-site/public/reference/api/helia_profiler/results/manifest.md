# helia_profiler.results.manifest

The on-disk index of a run directory: the artifacts it holds, the issues raised against it, and whether its numbers may be used.

Every name on this page is imported from `helia_profiler`.

**API tier:** `experimental`

Generated from the `src/helia_profiler` tree `17bfdf5c68d6c73c3d22f7afb063ac9cd722152f`.

## helia_profiler.RunStatus

`class` · `python`

```python
RunStatus()
```

Publication status of a result bundle.

**API tier:** `experimental`

Source: `src/helia_profiler/results/manifest.py:18`

### helia_profiler.RunStatus.COMPLETE

`constant` · `python`

```python
COMPLETE = 'complete'
```

Source: `src/helia_profiler/results/manifest.py:21`

### helia_profiler.RunStatus.INCOMPLETE

`constant` · `python`

```python
INCOMPLETE = 'incomplete'
```

Source: `src/helia_profiler/results/manifest.py:22`

### helia_profiler.RunStatus.FAILED

`constant` · `python`

```python
FAILED = 'failed'
```

Source: `src/helia_profiler/results/manifest.py:23`

## helia_profiler.ResultValidity

`class` · `python`

```python
ResultValidity()
```

Whether measurements in a completed bundle are authoritative.

**API tier:** `experimental`

Source: `src/helia_profiler/results/manifest.py:26`

### helia_profiler.ResultValidity.VALID

`constant` · `python`

```python
VALID = 'valid'
```

Source: `src/helia_profiler/results/manifest.py:29`

### helia_profiler.ResultValidity.DEGRADED

`constant` · `python`

```python
DEGRADED = 'degraded'
```

Source: `src/helia_profiler/results/manifest.py:30`

### helia_profiler.ResultValidity.INVALID

`constant` · `python`

```python
INVALID = 'invalid'
```

Source: `src/helia_profiler/results/manifest.py:31`

## helia_profiler.ResultIssue

`class` · `python`

```python
ResultIssue(
    code: str,
    severity: str,
    message: str,
    context: dict[str, Any] = dict(),
    extra: dict[str, Any] = dict(),
) -> None
```

`dataclass`

One stable machine-readable issue with optional open context.

**API tier:** `experimental`

Source: `src/helia_profiler/results/manifest.py:34`

### helia_profiler.ResultIssue.code

`attribute` · `python`

```python
code: str
```

Source: `src/helia_profiler/results/manifest.py:38`

### helia_profiler.ResultIssue.severity

`attribute` · `python`

```python
severity: str
```

Source: `src/helia_profiler/results/manifest.py:39`

### helia_profiler.ResultIssue.message

`attribute` · `python`

```python
message: str
```

Source: `src/helia_profiler/results/manifest.py:40`

### helia_profiler.ResultIssue.context

`attribute` · `python`

```python
context: dict[str, Any] = field(default_factory=dict)
```

Source: `src/helia_profiler/results/manifest.py:41`

### helia_profiler.ResultIssue.extra

`attribute` · `python`

```python
extra: dict[str, Any] = field(default_factory=dict, repr=False)
```

Source: `src/helia_profiler/results/manifest.py:42`

### helia_profiler.ResultIssue.from_dict

`method` · `python`

```python
from_dict(data: dict[str, Any]) -> Self
```

`classmethod`

Source: `src/helia_profiler/results/manifest.py:54`

### helia_profiler.ResultIssue.to_dict

`method` · `python`

```python
to_dict() -> dict[str, Any]
```

Source: `src/helia_profiler/results/manifest.py:58`

## helia_profiler.ResultArtifact

`class` · `python`

```python
ResultArtifact(
    path: str,
    media_type: str,
    size_bytes: int,
    sha256: str,
    role: str | None = None,
    name: str | None = None,
    schema: str | None = None,
    schema_version: int | None = None,
    producer: str | None = None,
    optional: bool | None = None,
    extra: dict[str, Any] = dict(),
) -> None
```

`dataclass`

One content-addressed file in a result bundle.

**API tier:** `experimental`

Source: `src/helia_profiler/results/manifest.py:62`

### helia_profiler.ResultArtifact.path

`attribute` · `python`

```python
path: str
```

Source: `src/helia_profiler/results/manifest.py:66`

### helia_profiler.ResultArtifact.media_type

`attribute` · `python`

```python
media_type: str
```

Source: `src/helia_profiler/results/manifest.py:67`

### helia_profiler.ResultArtifact.size_bytes

`attribute` · `python`

```python
size_bytes: int
```

Source: `src/helia_profiler/results/manifest.py:68`

### helia_profiler.ResultArtifact.sha256

`attribute` · `python`

```python
sha256: str
```

Source: `src/helia_profiler/results/manifest.py:69`

### helia_profiler.ResultArtifact.role

`attribute` · `python`

```python
role: str | None = None
```

Source: `src/helia_profiler/results/manifest.py:70`

### helia_profiler.ResultArtifact.name

`attribute` · `python`

```python
name: str | None = None
```

Source: `src/helia_profiler/results/manifest.py:71`

### helia_profiler.ResultArtifact.schema

`attribute` · `python`

```python
schema: str | None = None
```

Source: `src/helia_profiler/results/manifest.py:72`

### helia_profiler.ResultArtifact.schema_version

`attribute` · `python`

```python
schema_version: int | None = None
```

Source: `src/helia_profiler/results/manifest.py:73`

### helia_profiler.ResultArtifact.producer

`attribute` · `python`

```python
producer: str | None = None
```

Source: `src/helia_profiler/results/manifest.py:74`

### helia_profiler.ResultArtifact.optional

`attribute` · `python`

```python
optional: bool | None = None
```

Source: `src/helia_profiler/results/manifest.py:75`

### helia_profiler.ResultArtifact.extra

`attribute` · `python`

```python
extra: dict[str, Any] = field(default_factory=dict, repr=False)
```

Source: `src/helia_profiler/results/manifest.py:76`

### helia_profiler.ResultArtifact.from_dict

`method` · `python`

```python
from_dict(data: dict[str, Any]) -> Self
```

`classmethod`

Source: `src/helia_profiler/results/manifest.py:110`

### helia_profiler.ResultArtifact.to_dict

`method` · `python`

```python
to_dict() -> dict[str, Any]
```

Source: `src/helia_profiler/results/manifest.py:114`

## helia_profiler.ResultManifest

`class` · `python`

```python
ResultManifest(
    schema: str,
    schema_version: int,
    run_id: str,
    timestamp: str,
    hpx_version: str,
    status: RunStatus,
    validity: ResultValidity,
    issues: tuple[ResultIssue, ...],
    provenance: dict[str, Any],
    comparability: dict[str, Any],
    artifacts: tuple[ResultArtifact, ...],
    bundle_type: str | None = None,
    extensions: dict[str, Any] = dict(),
    extra: dict[str, Any] = dict(),
) -> None
```

`dataclass`

Stable result envelope with open provenance and extension payloads.

**API tier:** `experimental`

Source: `src/helia_profiler/results/manifest.py:118`

### helia_profiler.ResultManifest.schema

`attribute` · `python`

```python
schema: str
```

Source: `src/helia_profiler/results/manifest.py:122`

### helia_profiler.ResultManifest.schema_version

`attribute` · `python`

```python
schema_version: int
```

Source: `src/helia_profiler/results/manifest.py:123`

### helia_profiler.ResultManifest.run_id

`attribute` · `python`

```python
run_id: str
```

Source: `src/helia_profiler/results/manifest.py:124`

### helia_profiler.ResultManifest.timestamp

`attribute` · `python`

```python
timestamp: str
```

Source: `src/helia_profiler/results/manifest.py:125`

### helia_profiler.ResultManifest.hpx_version

`attribute` · `python`

```python
hpx_version: str
```

Source: `src/helia_profiler/results/manifest.py:126`

### helia_profiler.ResultManifest.status

`attribute` · `python`

```python
status: RunStatus
```

Source: `src/helia_profiler/results/manifest.py:127`

### helia_profiler.ResultManifest.validity

`attribute` · `python`

```python
validity: ResultValidity
```

Source: `src/helia_profiler/results/manifest.py:128`

### helia_profiler.ResultManifest.issues

`attribute` · `python`

```python
issues: tuple[ResultIssue, ...]
```

Source: `src/helia_profiler/results/manifest.py:129`

### helia_profiler.ResultManifest.provenance

`attribute` · `python`

```python
provenance: dict[str, Any]
```

Source: `src/helia_profiler/results/manifest.py:130`

### helia_profiler.ResultManifest.comparability

`attribute` · `python`

```python
comparability: dict[str, Any]
```

Source: `src/helia_profiler/results/manifest.py:131`

### helia_profiler.ResultManifest.artifacts

`attribute` · `python`

```python
artifacts: tuple[ResultArtifact, ...]
```

Source: `src/helia_profiler/results/manifest.py:132`

### helia_profiler.ResultManifest.bundle_type

`attribute` · `python`

```python
bundle_type: str | None = None
```

Source: `src/helia_profiler/results/manifest.py:133`

### helia_profiler.ResultManifest.extensions

`attribute` · `python`

```python
extensions: dict[str, Any] = field(default_factory=dict)
```

Source: `src/helia_profiler/results/manifest.py:134`

### helia_profiler.ResultManifest.extra

`attribute` · `python`

```python
extra: dict[str, Any] = field(default_factory=dict, repr=False)
```

Source: `src/helia_profiler/results/manifest.py:135`

### helia_profiler.ResultManifest.from_dict

`method` · `python`

```python
from_dict(data: dict[str, Any]) -> Self
```

`classmethod`

Source: `src/helia_profiler/results/manifest.py:168`

### helia_profiler.ResultManifest.load

`method` · `python`

```python
load(path: str | Path) -> Self
```

`classmethod`

Load a manifest while preserving unknown fields.

Source: `src/helia_profiler/results/manifest.py:184`

### helia_profiler.ResultManifest.to_dict

`method` · `python`

```python
to_dict() -> dict[str, Any]
```

Source: `src/helia_profiler/results/manifest.py:196`

### helia_profiler.ResultManifest.write

`method` · `python`

```python
write(path: str | Path) -> Path
```

Write the manifest without discarding unknown fields.

Source: `src/helia_profiler/results/manifest.py:199`

### helia_profiler.ResultManifest.verify

`method` · `python`

```python
verify(bundle_dir: str | Path) -> None
```

Verify all declared artifact paths, sizes, and SHA-256 digests.

Source: `src/helia_profiler/results/manifest.py:211`

## helia_profiler.load_result_manifest

`function` · `python`

```python
load_result_manifest(path: str | Path, *, verify: bool = False) -> ResultManifest
```

Load a result manifest and optionally verify its sibling artifacts.

**API tier:** `experimental`

Source: `src/helia_profiler/results/manifest.py:243`
