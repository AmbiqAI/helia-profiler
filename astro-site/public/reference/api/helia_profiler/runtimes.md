# helia_profiler.runtimes

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

Source: `src/helia_profiler/runtimes.py:32`

### helia_profiler.RuntimeQualification.QUALIFIED

`constant` · `python`

```python
QUALIFIED = 'qualified'
```

Source: `src/helia_profiler/runtimes.py:35`

### helia_profiler.RuntimeQualification.SUPPORTED

`constant` · `python`

```python
SUPPORTED = 'supported'
```

Source: `src/helia_profiler/runtimes.py:36`

### helia_profiler.RuntimeQualification.UNSUPPORTED

`constant` · `python`

```python
UNSUPPORTED = 'unsupported'
```

Source: `src/helia_profiler/runtimes.py:37`

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
) -> None
```

`dataclass`

**API tier:** `experimental`

Source: `src/helia_profiler/runtimes.py:57`

### helia_profiler.RuntimeRecord.name

`attribute` · `python`

```python
name: str
```

Source: `src/helia_profiler/runtimes.py:59`

### helia_profiler.RuntimeRecord.version

`attribute` · `python`

```python
version: str
```

Source: `src/helia_profiler/runtimes.py:60`

### helia_profiler.RuntimeRecord.default

`attribute` · `python`

```python
default: bool
```

Source: `src/helia_profiler/runtimes.py:61`

### helia_profiler.RuntimeRecord.source

`attribute` · `python`

```python
source: RuntimeSource
```

Source: `src/helia_profiler/runtimes.py:62`

### helia_profiler.RuntimeRecord.precisions

`attribute` · `python`

```python
precisions: Mapping[str, str | None]
```

Source: `src/helia_profiler/runtimes.py:64`

### helia_profiler.RuntimeRecord.qualified

`attribute` · `python`

```python
qualified: tuple[QualifiedTarget, ...]
```

Source: `src/helia_profiler/runtimes.py:65`

## helia_profiler.Qualification

`class` · `python`

```python
Qualification(state: RuntimeQualification, reason: str | None, record: RuntimeRecord | None) -> None
```

`dataclass`

**API tier:** `experimental`

Source: `src/helia_profiler/runtimes.py:68`

### helia_profiler.Qualification.state

`attribute` · `python`

```python
state: RuntimeQualification
```

Source: `src/helia_profiler/runtimes.py:70`

### helia_profiler.Qualification.reason

`attribute` · `python`

```python
reason: str | None
```

Source: `src/helia_profiler/runtimes.py:71`

### helia_profiler.Qualification.record

`attribute` · `python`

```python
record: RuntimeRecord | None
```

Source: `src/helia_profiler/runtimes.py:72`

## helia_profiler.qualification

`function` · `python`

```python
qualification(name: str, version: str | None = None, *, board: str, clock: str, precision: str) -> Qualification
```

Whether ``name`` at ``version`` is qualified for ``precision`` on ``board`` at ``clock``.

A version without a record is unsupported: this heliaPROFILER makes no
claim about it.

**API tier:** `experimental`

Source: `src/helia_profiler/runtimes.py:119`

---

# helia_profiler.runtimes.runtimes

Runtime records and the runtime × target qualification lookup.

Each runtime version heliaPROFILER builds is one small JSON record under
``data/runtimes/<name>/<version>.json``: its source, the precisions it
supports and the targets it is qualified on. The records are the only place
these facts live; nothing keeps a combined index.

**API tier:** `experimental`

## helia_profiler.runtimes.RUNTIME_SCHEMA

`constant` · `python`

```python
RUNTIME_SCHEMA = 'helia-profiler/runtime@1'
```

Source: `src/helia_profiler/runtimes.py:23`

## helia_profiler.runtimes.PRECISIONS

`constant` · `python`

```python
PRECISIONS = ('fp32', 'fp16', 'a8w8', 'a16w8', 'a8w4')
```

Source: `src/helia_profiler/runtimes.py:25`

## helia_profiler.runtimes.RuntimeQualification

`class` · `python`

```python
RuntimeQualification()
```

What heliaPROFILER says about one runtime version, precision and target.

Source: `src/helia_profiler/runtimes.py:32`

### helia_profiler.runtimes.RuntimeQualification.QUALIFIED

`constant` · `python`

```python
QUALIFIED = 'qualified'
```

Source: `src/helia_profiler/runtimes.py:35`

### helia_profiler.runtimes.RuntimeQualification.SUPPORTED

`constant` · `python`

```python
SUPPORTED = 'supported'
```

Source: `src/helia_profiler/runtimes.py:36`

### helia_profiler.runtimes.RuntimeQualification.UNSUPPORTED

`constant` · `python`

```python
UNSUPPORTED = 'unsupported'
```

Source: `src/helia_profiler/runtimes.py:37`

## helia_profiler.runtimes.RuntimeSource

`class` · `python`

```python
RuntimeSource(repo: str, commit: str) -> None
```

`dataclass`

Source: `src/helia_profiler/runtimes.py:40`

### helia_profiler.runtimes.RuntimeSource.repo

`attribute` · `python`

```python
repo: str
```

Source: `src/helia_profiler/runtimes.py:42`

### helia_profiler.runtimes.RuntimeSource.commit

`attribute` · `python`

```python
commit: str
```

Source: `src/helia_profiler/runtimes.py:43`

## helia_profiler.runtimes.QualifiedTarget

`class` · `python`

```python
QualifiedTarget(board: str, clock: str, precisions: tuple[str, ...], basis: str, trace: str) -> None
```

`dataclass`

A board class and clock the runtime is qualified on, and why.

Source: `src/helia_profiler/runtimes.py:46`

### helia_profiler.runtimes.QualifiedTarget.board

`attribute` · `python`

```python
board: str
```

Source: `src/helia_profiler/runtimes.py:50`

### helia_profiler.runtimes.QualifiedTarget.clock

`attribute` · `python`

```python
clock: str
```

Source: `src/helia_profiler/runtimes.py:51`

### helia_profiler.runtimes.QualifiedTarget.precisions

`attribute` · `python`

```python
precisions: tuple[str, ...]
```

Source: `src/helia_profiler/runtimes.py:52`

### helia_profiler.runtimes.QualifiedTarget.basis

`attribute` · `python`

```python
basis: str
```

Source: `src/helia_profiler/runtimes.py:53`

### helia_profiler.runtimes.QualifiedTarget.trace

`attribute` · `python`

```python
trace: str
```

Source: `src/helia_profiler/runtimes.py:54`

## helia_profiler.runtimes.RuntimeRecord

`class` · `python`

```python
RuntimeRecord(
    name: str,
    version: str,
    default: bool,
    source: RuntimeSource,
    precisions: Mapping[str, str | None],
    qualified: tuple[QualifiedTarget, ...],
) -> None
```

`dataclass`

Source: `src/helia_profiler/runtimes.py:57`

### helia_profiler.runtimes.RuntimeRecord.name

`attribute` · `python`

```python
name: str
```

Source: `src/helia_profiler/runtimes.py:59`

### helia_profiler.runtimes.RuntimeRecord.version

`attribute` · `python`

```python
version: str
```

Source: `src/helia_profiler/runtimes.py:60`

### helia_profiler.runtimes.RuntimeRecord.default

`attribute` · `python`

```python
default: bool
```

Source: `src/helia_profiler/runtimes.py:61`

### helia_profiler.runtimes.RuntimeRecord.source

`attribute` · `python`

```python
source: RuntimeSource
```

Source: `src/helia_profiler/runtimes.py:62`

### helia_profiler.runtimes.RuntimeRecord.precisions

`attribute` · `python`

```python
precisions: Mapping[str, str | None]
```

Source: `src/helia_profiler/runtimes.py:64`

### helia_profiler.runtimes.RuntimeRecord.qualified

`attribute` · `python`

```python
qualified: tuple[QualifiedTarget, ...]
```

Source: `src/helia_profiler/runtimes.py:65`

## helia_profiler.runtimes.Qualification

`class` · `python`

```python
Qualification(state: RuntimeQualification, reason: str | None, record: RuntimeRecord | None) -> None
```

`dataclass`

Source: `src/helia_profiler/runtimes.py:68`

### helia_profiler.runtimes.Qualification.state

`attribute` · `python`

```python
state: RuntimeQualification
```

Source: `src/helia_profiler/runtimes.py:70`

### helia_profiler.runtimes.Qualification.reason

`attribute` · `python`

```python
reason: str | None
```

Source: `src/helia_profiler/runtimes.py:71`

### helia_profiler.runtimes.Qualification.record

`attribute` · `python`

```python
record: RuntimeRecord | None
```

Source: `src/helia_profiler/runtimes.py:72`

## helia_profiler.runtimes.runtimes

`function` · `python`

```python
runtimes() -> tuple[RuntimeRecord, ...]
```

`cached`

Every runtime record shipped with this heliaPROFILER, by name then version.

Source: `src/helia_profiler/runtimes.py:75`

## helia_profiler.runtimes.load_runtime_records

`function` · `python`

```python
load_runtime_records(root: Traversable) -> tuple[RuntimeRecord, ...]
```

Load and check every ``<name>/<version>.json`` record under ``root``.

Source: `src/helia_profiler/runtimes.py:83`

## helia_profiler.runtimes.runtime

`function` · `python`

```python
runtime(name: str, version: str | None = None) -> RuntimeRecord | None
```

The record for ``name`` at ``version``, or its default record when ``version`` is None.

Source: `src/helia_profiler/runtimes.py:106`

## helia_profiler.runtimes.qualification

`function` · `python`

```python
qualification(name: str, version: str | None = None, *, board: str, clock: str, precision: str) -> Qualification
```

Whether ``name`` at ``version`` is qualified for ``precision`` on ``board`` at ``clock``.

A version without a record is unsupported: this heliaPROFILER makes no
claim about it.

Source: `src/helia_profiler/runtimes.py:119`
