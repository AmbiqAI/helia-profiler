# helia_profiler.build_record

Build once, run later: a frozen plan's compiled firmware saved as a record that any host can verify, relocate and capture from, and how model state carries across a run's invocations.

Every name on this page is imported from `helia_profiler`.

**API tier:** `experimental`

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__`.

## helia_profiler.Lifecycle

`class` · `python`

```python
Lifecycle()
```

State handling across invocations.

``CARRY`` initializes the engine once and keeps internal state through
warmups and timed invocations, restoring only the fixed inputs each time.
``SINGLE`` invokes the model once, so no state carries between
invocations.

**API tier:** `experimental`

Source: `src/helia_profiler/lifecycle.py:18`

### helia_profiler.Lifecycle.CARRY

`constant` · `python`

```python
CARRY = 'carry'
```

Source: `src/helia_profiler/lifecycle.py:27`

### helia_profiler.Lifecycle.SINGLE

`constant` · `python`

```python
SINGLE = 'single'
```

Source: `src/helia_profiler/lifecycle.py:28`

## helia_profiler.BuildRecord

`class` · `python`

```python
BuildRecord(plan: FrozenRun, build: FixtureBuild, lifecycle: Lifecycle) -> None
```

`dataclass`

A frozen plan and the firmware compiled from it, verifiable on any host.

**API tier:** `experimental`

Source: `src/helia_profiler/build_record.py:34`

### helia_profiler.BuildRecord.plan

`attribute` · `python`

```python
plan: FrozenRun
```

Source: `src/helia_profiler/build_record.py:38`

### helia_profiler.BuildRecord.build

`attribute` · `python`

```python
build: FixtureBuild
```

Source: `src/helia_profiler/build_record.py:39`

### helia_profiler.BuildRecord.lifecycle

`attribute` · `python`

```python
lifecycle: Lifecycle
```

Source: `src/helia_profiler/build_record.py:40`

### helia_profiler.BuildRecord.identity

`attribute` · `python`

```python
identity: RunIdentity
```

The record's identity; ``observation_hash`` is ``None`` until it runs.

Source: `src/helia_profiler/build_record.py:43`

### helia_profiler.BuildRecord.verify

`method` · `python`

```python
verify() -> None
```

Re-check the build against its pinned files and its plan; raise on any mismatch.

Source: `src/helia_profiler/build_record.py:50`

### helia_profiler.BuildRecord.save

`method` · `python`

```python
save(path: str | Path, *, roots: Mapping[str, Path]) -> Path
```

Write the record as JSON with paths relative to *roots* and return its path.

Source: `src/helia_profiler/build_record.py:79`

### helia_profiler.BuildRecord.load

`method` · `python`

```python
load(path: str | Path, *, roots: Mapping[str, Path]) -> BuildRecord
```

`classmethod`

Read and verify a record written by :meth:`save`, resolving paths under *roots*.

Source: `src/helia_profiler/build_record.py:101`

### helia_profiler.BuildRecord.run

`method` · `python`

```python
run(
    *,
    guard: FixtureCaptureGuard,
    jlink_serial: str,
    evidence_dir: Path,
    settle_seconds: float,
    expect: Expect | None = None,
    expected_duration_s: float | None = None,
) -> RunResult
```

Flash the recorded image, capture one run and compare outputs when *expect* is given.

Source: `src/helia_profiler/build_record.py:135`

## helia_profiler.RunResult

`class` · `python`

```python
RunResult(
    record: BuildRecord,
    capture: FixtureCaptureResult,
    identity: RunIdentity,
    verification: Verification | None,
) -> None
```

`dataclass`

One capture from a :class:`BuildRecord`.

**API tier:** `experimental`

Source: `src/helia_profiler/build_record.py:149`

### helia_profiler.RunResult.record

`attribute` · `python`

```python
record: BuildRecord
```

Source: `src/helia_profiler/build_record.py:153`

### helia_profiler.RunResult.capture

`attribute` · `python`

```python
capture: FixtureCaptureResult
```

Source: `src/helia_profiler/build_record.py:154`

### helia_profiler.RunResult.identity

`attribute` · `python`

```python
identity: RunIdentity
```

Source: `src/helia_profiler/build_record.py:155`

### helia_profiler.RunResult.verification

`attribute` · `python`

```python
verification: Verification | None
```

Source: `src/helia_profiler/build_record.py:156`

### helia_profiler.RunResult.succeeded

`attribute` · `python`

```python
succeeded: bool
```

Source: `src/helia_profiler/build_record.py:159`
