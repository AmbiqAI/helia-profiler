# helia_profiler.diagnostics

Collecting a redacted support bundle from a run directory, and reading one back.

Every name on this page is imported from `helia_profiler`.

**API tier:** `experimental`

Generated from the `src/helia_profiler` tree `872d67ad4e9083b1eacd7d6d3859148437b96b59`.

## helia_profiler.SupportBundleSection

`class` · `python`

```python
SupportBundleSection(name: str, available: bool, reason: str | None = None) -> None
```

`dataclass`

One diagnostic section the collector attempted.

``available=False`` records *why* a section was skipped (missing
workspace, offline, optional tool absent, ...) rather than failing the
whole bundle — see the Concepts page ``guide/concepts/field-diagnostics``.

**API tier:** `experimental`

Source: `src/helia_profiler/results/support_bundle.py:27`

### helia_profiler.SupportBundleSection.name

`attribute` · `python`

```python
name: str
```

Source: `src/helia_profiler/results/support_bundle.py:36`

### helia_profiler.SupportBundleSection.available

`attribute` · `python`

```python
available: bool
```

Source: `src/helia_profiler/results/support_bundle.py:37`

### helia_profiler.SupportBundleSection.reason

`attribute` · `python`

```python
reason: str | None = None
```

Source: `src/helia_profiler/results/support_bundle.py:38`

### helia_profiler.SupportBundleSection.from_dict

`method` · `python`

```python
from_dict(data: dict[str, Any]) -> Self
```

`classmethod`

Source: `src/helia_profiler/results/support_bundle.py:48`

### helia_profiler.SupportBundleSection.to_dict

`method` · `python`

```python
to_dict() -> dict[str, Any]
```

Source: `src/helia_profiler/results/support_bundle.py:57`

## helia_profiler.SupportBundleOptions

`class` · `python`

```python
SupportBundleOptions(
    workspace: Path | None = None,
    config_path: Path | None = None,
    toolchain: Toolchain = Toolchain.ARM_NONE_EABI_GCC,
    transport: Transport = Transport.RTT,
    engine: EngineType = EngineType.HELIA_RT,
    include_probes: bool = True,
    include_ports: bool = True,
    raw_probe_ids: bool = False,
) -> None
```

`dataclass`

What ``hpx doctor --bundle`` should collect.

Every section beyond the always-available doctor checks and
compatibility baseline is optional and independently toggleable so a
bundle can be built entirely offline with no attached hardware.

**API tier:** `experimental`

Source: `src/helia_profiler/diagnostics/support_bundle.py:46`

### helia_profiler.SupportBundleOptions.workspace

`attribute` · `python`

```python
workspace: Path | None = None
```

Source: `src/helia_profiler/diagnostics/support_bundle.py:55`

### helia_profiler.SupportBundleOptions.config_path

`attribute` · `python`

```python
config_path: Path | None = None
```

Source: `src/helia_profiler/diagnostics/support_bundle.py:56`

### helia_profiler.SupportBundleOptions.toolchain

`attribute` · `python`

```python
toolchain: Toolchain = Toolchain.ARM_NONE_EABI_GCC
```

Source: `src/helia_profiler/diagnostics/support_bundle.py:57`

### helia_profiler.SupportBundleOptions.transport

`attribute` · `python`

```python
transport: Transport = Transport.RTT
```

Source: `src/helia_profiler/diagnostics/support_bundle.py:58`

### helia_profiler.SupportBundleOptions.engine

`attribute` · `python`

```python
engine: EngineType = EngineType.HELIA_RT
```

Source: `src/helia_profiler/diagnostics/support_bundle.py:59`

### helia_profiler.SupportBundleOptions.include_probes

`attribute` · `python`

```python
include_probes: bool = True
```

Source: `src/helia_profiler/diagnostics/support_bundle.py:60`

### helia_profiler.SupportBundleOptions.include_ports

`attribute` · `python`

```python
include_ports: bool = True
```

Source: `src/helia_profiler/diagnostics/support_bundle.py:61`

### helia_profiler.SupportBundleOptions.raw_probe_ids

`attribute` · `python`

```python
raw_probe_ids: bool = False
```

Source: `src/helia_profiler/diagnostics/support_bundle.py:62`

## helia_profiler.SupportBundleManifest

`class` · `python`

```python
SupportBundleManifest(
    schema: str,
    schema_version: int,
    hpx_version: str,
    generated_at: str,
    host: dict[str, Any],
    sections: tuple[SupportBundleSection, ...],
    redaction: dict[str, Any],
    artifacts: tuple[ResultArtifact, ...],
    extra: dict[str, Any] = dict(),
) -> None
```

`dataclass`

Stable envelope describing one support-bundle archive's contents.

**API tier:** `experimental`

Source: `src/helia_profiler/results/support_bundle.py:64`

### helia_profiler.SupportBundleManifest.schema

`attribute` · `python`

```python
schema: str
```

Source: `src/helia_profiler/results/support_bundle.py:68`

### helia_profiler.SupportBundleManifest.schema_version

`attribute` · `python`

```python
schema_version: int
```

Source: `src/helia_profiler/results/support_bundle.py:69`

### helia_profiler.SupportBundleManifest.hpx_version

`attribute` · `python`

```python
hpx_version: str
```

Source: `src/helia_profiler/results/support_bundle.py:70`

### helia_profiler.SupportBundleManifest.generated_at

`attribute` · `python`

```python
generated_at: str
```

Source: `src/helia_profiler/results/support_bundle.py:71`

### helia_profiler.SupportBundleManifest.host

`attribute` · `python`

```python
host: dict[str, Any]
```

Source: `src/helia_profiler/results/support_bundle.py:72`

### helia_profiler.SupportBundleManifest.sections

`attribute` · `python`

```python
sections: tuple[SupportBundleSection, ...]
```

Source: `src/helia_profiler/results/support_bundle.py:73`

### helia_profiler.SupportBundleManifest.redaction

`attribute` · `python`

```python
redaction: dict[str, Any]
```

Source: `src/helia_profiler/results/support_bundle.py:74`

### helia_profiler.SupportBundleManifest.artifacts

`attribute` · `python`

```python
artifacts: tuple[ResultArtifact, ...]
```

Source: `src/helia_profiler/results/support_bundle.py:75`

### helia_profiler.SupportBundleManifest.extra

`attribute` · `python`

```python
extra: dict[str, Any] = field(default_factory=dict, repr=False)
```

Source: `src/helia_profiler/results/support_bundle.py:76`

### helia_profiler.SupportBundleManifest.section

`method` · `python`

```python
section(name: str) -> SupportBundleSection | None
```

Source: `src/helia_profiler/results/support_bundle.py:100`

### helia_profiler.SupportBundleManifest.to_dict

`method` · `python`

```python
to_dict() -> dict[str, Any]
```

Source: `src/helia_profiler/results/support_bundle.py:106`

### helia_profiler.SupportBundleManifest.from_dict

`method` · `python`

```python
from_dict(data: dict[str, Any]) -> Self
```

`classmethod`

Source: `src/helia_profiler/results/support_bundle.py:120`

### helia_profiler.SupportBundleManifest.load

`method` · `python`

```python
load(path: str | Path) -> Self
```

`classmethod`

Source: `src/helia_profiler/results/support_bundle.py:140`

### helia_profiler.SupportBundleManifest.verify

`method` · `python`

```python
verify(bundle_dir: str | Path) -> None
```

Verify every declared artifact path, size, and SHA-256 digest.

Rejects absolute paths and any path that escapes *bundle_dir* so a
hostile/corrupted manifest cannot be used to read or overwrite files
outside the extracted bundle (zip-slip style attacks).

Source: `src/helia_profiler/results/support_bundle.py:151`

## helia_profiler.collect_support_bundle

`function` · `python`

```python
collect_support_bundle(options: SupportBundleOptions = SupportBundleOptions()) -> SupportBundleCollection
```

Gather every diagnostic section, redact it, and build the manifest.

Never raises for a missing optional dependency, tool, or workspace —
every section catches its own typed failures and records a skip reason
instead. Only truly unexpected internal errors propagate.

**API tier:** `experimental`

Source: `src/helia_profiler/diagnostics/support_bundle.py:73`

## helia_profiler.write_support_bundle

`function` · `python`

```python
write_support_bundle(collection: SupportBundleCollection, output: Path) -> Path
```

Write *collection* as a deterministic ZIP archive and return its path.

*output* names the exact archive file when it ends in ``.zip``;
otherwise it is treated as a directory (created if needed) and the
filename is derived from :func:`content_fingerprint` plus the HPX
version, so identical inputs always produce the same file name and the
same member bytes for every entry except ``manifest.json`` (only its
``generated_at`` timestamp differs run to run).

Raises :class:`~helia_profiler.errors.ReportError` (not a raw
:class:`OSError`) if the destination cannot be created or written to
(permission denied, no space left, a path component that is itself a
file, ...), so CLI callers only ever need to catch ``HpxError``.

**API tier:** `experimental`

Source: `src/helia_profiler/diagnostics/support_bundle.py:505`

## helia_profiler.verify_support_bundle

`function` · `python`

```python
verify_support_bundle(path: Path) -> SupportBundleManifest
```

Verify a support-bundle archive's structure, contents, and digests.

Rejects absolute member paths (POSIX, and Windows drive-letter paths in
either ``C:\...`` or ``C:/...`` form), ``..``/empty path segments,
backslashes, NUL bytes, duplicate entries, and any file extension other
than ``.json``/exactly ``nsx.lock`` — defense in depth against a
malformed or hostile archive (zip-slip, disguised binary payloads) even
though this module only ever writes archives matching that shape
itself.

**API tier:** `experimental`

Source: `src/helia_profiler/diagnostics/support_bundle.py:578`
