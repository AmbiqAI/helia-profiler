# helia_profiler.runtime_records

The runtime versions this heliaPROFILER records, and whether each is qualified for a precision on a board and clock.

Every name on this page is imported from `helia_profiler`.

**API tier:** `experimental`

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__`.

## helia_profiler.RuntimeQualification

`class` · `python`

```python
RuntimeQualification()
```

What heliaPROFILER says about one runtime version, precision and target.

**API tier:** `experimental`

Source: `src/helia_profiler/runtime_records.py:35`

### helia_profiler.RuntimeQualification.QUALIFIED

`constant` · `python`

```python
QUALIFIED = 'qualified'
```

Source: `src/helia_profiler/runtime_records.py:38`

### helia_profiler.RuntimeQualification.SUPPORTED

`constant` · `python`

```python
SUPPORTED = 'supported'
```

Source: `src/helia_profiler/runtime_records.py:39`

### helia_profiler.RuntimeQualification.UNSUPPORTED

`constant` · `python`

```python
UNSUPPORTED = 'unsupported'
```

Source: `src/helia_profiler/runtime_records.py:40`

## helia_profiler.RuntimeSource

`class` · `python`

```python
RuntimeSource(repo: str, commit: str) -> None
```

`dataclass`

The repository and commit a runtime version is built from.

**API tier:** `experimental`

Source: `src/helia_profiler/runtime_records.py:43`

### helia_profiler.RuntimeSource.repo

`attribute` · `python`

```python
repo: str
```

Source: `src/helia_profiler/runtime_records.py:47`

### helia_profiler.RuntimeSource.commit

`attribute` · `python`

```python
commit: str
```

Source: `src/helia_profiler/runtime_records.py:48`

## helia_profiler.QualifiedTarget

`class` · `python`

```python
QualifiedTarget(board: str, clock: str, precisions: tuple[str, ...], basis: str, trace: str) -> None
```

`dataclass`

A board class and clock the runtime is qualified on, and why.

**API tier:** `experimental`

Source: `src/helia_profiler/runtime_records.py:51`

### helia_profiler.QualifiedTarget.board

`attribute` · `python`

```python
board: str
```

Source: `src/helia_profiler/runtime_records.py:55`

### helia_profiler.QualifiedTarget.clock

`attribute` · `python`

```python
clock: str
```

Source: `src/helia_profiler/runtime_records.py:56`

### helia_profiler.QualifiedTarget.precisions

`attribute` · `python`

```python
precisions: tuple[str, ...]
```

Source: `src/helia_profiler/runtime_records.py:57`

### helia_profiler.QualifiedTarget.basis

`attribute` · `python`

```python
basis: str
```

Source: `src/helia_profiler/runtime_records.py:58`

### helia_profiler.QualifiedTarget.trace

`attribute` · `python`

```python
trace: str
```

Source: `src/helia_profiler/runtime_records.py:59`

## helia_profiler.RuntimeRecord

`class` · `python`

```python
RuntimeRecord(
    name: str,
    version: str,
    default: bool,
    source: RuntimeSource,
    precisions: Mapping[str, str | None],
    qualified: tuple[QualifiedTarget, ...],
    kernels: RuntimeSource | None = None,
    archive_sha256: str | None = None,
) -> None
```

`dataclass`

One runtime version: its source, supported precisions and qualified targets.

**API tier:** `experimental`

Source: `src/helia_profiler/runtime_records.py:62`

### helia_profiler.RuntimeRecord.name

`attribute` · `python`

```python
name: str
```

Source: `src/helia_profiler/runtime_records.py:66`

### helia_profiler.RuntimeRecord.version

`attribute` · `python`

```python
version: str
```

Source: `src/helia_profiler/runtime_records.py:67`

### helia_profiler.RuntimeRecord.default

`attribute` · `python`

```python
default: bool
```

Source: `src/helia_profiler/runtime_records.py:68`

### helia_profiler.RuntimeRecord.source

`attribute` · `python`

```python
source: RuntimeSource
```

Source: `src/helia_profiler/runtime_records.py:69`

### helia_profiler.RuntimeRecord.precisions

`attribute` · `python`

```python
precisions: Mapping[str, str | None]
```

Source: `src/helia_profiler/runtime_records.py:71`

### helia_profiler.RuntimeRecord.qualified

`attribute` · `python`

```python
qualified: tuple[QualifiedTarget, ...]
```

Source: `src/helia_profiler/runtime_records.py:72`

### helia_profiler.RuntimeRecord.kernels

`attribute` · `python`

```python
kernels: RuntimeSource | None = None
```

Source: `src/helia_profiler/runtime_records.py:74`

### helia_profiler.RuntimeRecord.archive_sha256

`attribute` · `python`

```python
archive_sha256: str | None = None
```

Source: `src/helia_profiler/runtime_records.py:75`

## helia_profiler.Qualification

`class` · `python`

```python
Qualification(state: RuntimeQualification, reason: str | None, record: RuntimeRecord | None) -> None
```

`dataclass`

The answer for one runtime version, precision and target, with the record it came from.

**API tier:** `experimental`

Source: `src/helia_profiler/runtime_records.py:78`

### helia_profiler.Qualification.state

`attribute` · `python`

```python
state: RuntimeQualification
```

Source: `src/helia_profiler/runtime_records.py:82`

### helia_profiler.Qualification.reason

`attribute` · `python`

```python
reason: str | None
```

Source: `src/helia_profiler/runtime_records.py:83`

### helia_profiler.Qualification.record

`attribute` · `python`

```python
record: RuntimeRecord | None
```

Source: `src/helia_profiler/runtime_records.py:84`

## helia_profiler.runtimes

`function` · `python`

```python
runtimes() -> tuple[RuntimeRecord, ...]
```

`cached`

Every runtime record shipped with this heliaPROFILER, by name then version.

**API tier:** `experimental`

Source: `src/helia_profiler/runtime_records.py:87`

## helia_profiler.qualification

`function` · `python`

```python
qualification(name: str, version: str | None = None, *, board: str, clock: str, precision: str) -> Qualification
```

Whether ``name`` at ``version`` is qualified for ``precision`` on ``board`` at ``clock``.

``board`` and ``clock`` must be a built-in board and one of its CPU clock
profiles (records qualify built-in boards only, so a ``target.custom_boards``
name is refused too), and ``precision`` one of :data:`PRECISIONS`; anything
else raises ``ValueError``, the precision first.

A version without a record, or a precision its record does not declare,
is unsupported here, even when an engine's own version check would build
it: this heliaPROFILER makes no claim about it.

**API tier:** `experimental`

Source: `src/helia_profiler/runtime_records.py:133`
