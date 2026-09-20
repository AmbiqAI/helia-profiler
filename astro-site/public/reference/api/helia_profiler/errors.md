# helia_profiler.errors

The exception hierarchy every heliaPROFILER failure is raised through, rooted at HpxError.

Every name on this page is imported from `helia_profiler`.

**API tier:** `stable`, `experimental`

Generated from the `src/helia_profiler` tree `17bfdf5c68d6c73c3d22f7afb063ac9cd722152f`.

## helia_profiler.HpxError

`class` · `python`

```python
HpxError(message: str, *, hint: str | None = None) -> None
```

Base exception for all heliaPROFILER errors.

**API tier:** `stable`

Source: `src/helia_profiler/errors.py:11`

### helia_profiler.HpxError.hint

`attribute` · `python`

```python
hint = hint
```

Source: `src/helia_profiler/errors.py:15`

## helia_profiler.ConfigError

`class` · `python`

```python
ConfigError()
```

Bad configuration — missing model path, invalid YAML, unknown board.

**API tier:** `stable`

Source: `src/helia_profiler/errors.py:29`

## helia_profiler.PlatformError

`class` · `python`

```python
PlatformError()
```

Unsupported board/SoC combination or missing platform capability.

**API tier:** `stable`

Source: `src/helia_profiler/errors.py:33`

## helia_profiler.EngineError

`class` · `python`

```python
EngineError()
```

Engine adapter failure — AOT compile error, missing static lib, etc.

**API tier:** `stable`

Source: `src/helia_profiler/errors.py:37`

## helia_profiler.FirmwareError

`class` · `python`

```python
FirmwareError()
```

Firmware generation failure — template rendering, file I/O.

**API tier:** `stable`

Source: `src/helia_profiler/errors.py:41`

## helia_profiler.BuildError

`class` · `python`

```python
BuildError(message: str, *, hint: str | None = None, returncode: int | None = None, details: str | None = None) -> None
```

NSX configure / build / flash / lock / sync failure.

Carries the underlying tool's diagnostic output (cmake / ninja /
SEGGER commander / git stderr, NSX exception message, etc.) in
:attr:`details`.  When the source is a real subprocess, the original
return code is also captured in :attr:`returncode`.

**API tier:** `stable`

Source: `src/helia_profiler/errors.py:45`

### helia_profiler.BuildError.returncode

`attribute` · `python`

```python
returncode = returncode
```

Source: `src/helia_profiler/errors.py:62`

### helia_profiler.BuildError.details

`attribute` · `python`

```python
details = details
```

Source: `src/helia_profiler/errors.py:63`

## helia_profiler.CaptureError

`class` · `python`

```python
CaptureError()
```

Data capture failure — serial timeout, corrupt data, SWO framing.

**API tier:** `stable`

Source: `src/helia_profiler/errors.py:67`

## helia_profiler.DeterministicCaptureError

`class` · `python`

```python
DeterministicCaptureError()
```

A capture-path refusal no retry or power cycle can change.

Subclass of :class:`CaptureError` so existing ``except CaptureError``
handlers still catch it, but recovery paths that would otherwise cycle the
target rail and retry (``stages.flash_power``) re-raise it instead: these
are configuration/artifact gaps — a missing image, an unknown load
address — and cycling the rail only frames them as flaky hardware.

**API tier:** `stable`

Source: `src/helia_profiler/errors.py:71`

## helia_profiler.NetworkError

`class` · `python`

```python
NetworkError()
```

Transient network failure during sync/lock (git fetch, module download).

Subclass of :class:`BuildError` so existing ``except BuildError`` handlers
still catch it, but callers that want to retry can specifically catch this.

**API tier:** `stable`

Source: `src/helia_profiler/errors.py:82`

## helia_profiler.DependencyError

`class` · `python`

```python
DependencyError()
```

Deterministic dependency lock/workspace failure (base class).

Covers workspace fingerprinting, override, and provenance-collection
failures that are neither a version mismatch nor a lock-file problem.
Prefer :class:`VersionError` or :class:`LockError` when a failure is
specifically about an incompatible version or a missing/corrupt lock.

**API tier:** `experimental`

Source: `src/helia_profiler/errors.py:90`

## helia_profiler.VersionError

`class` · `python`

```python
VersionError()
```

A tool, package, engine, or lock schema version is incompatible.

Raised when an installed version fails a compatibility baseline check
(schema version, minimum/maximum engine version, pinned package
version) so callers can distinguish "wrong version" from other
dependency failures and surface an actionable upgrade/downgrade hint.

**API tier:** `experimental`

Source: `src/helia_profiler/errors.py:100`

## helia_profiler.LockError

`class` · `python`

```python
LockError()
```

A dependency lock file or its recorded provenance is unusable.

Raised for a missing, unreadable, structurally invalid, or
drifted ``nsx.lock`` / ``hpx-dependencies.json``, so field-diagnostics
collectors can skip just the lock-provenance section instead of
failing an entire report.

**API tier:** `experimental`

Source: `src/helia_profiler/errors.py:110`

## helia_profiler.PowerError

`class` · `python`

```python
PowerError()
```

Power measurement failure — Joulescope not found, calibration error.

**API tier:** `stable`

Source: `src/helia_profiler/errors.py:120`

## helia_profiler.ReportError

`class` · `python`

```python
ReportError()
```

Report generation failure — output path not writable, format error.

**API tier:** `stable`

Source: `src/helia_profiler/errors.py:124`
