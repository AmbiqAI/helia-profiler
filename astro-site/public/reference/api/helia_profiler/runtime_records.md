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

Source: `src/helia_profiler/runtime_records.py:32`

### helia_profiler.RuntimeQualification.QUALIFIED

`constant` · `python`

```python
QUALIFIED = 'qualified'
```

Source: `src/helia_profiler/runtime_records.py:35`

### helia_profiler.RuntimeQualification.SUPPORTED

`constant` · `python`

```python
SUPPORTED = 'supported'
```

Source: `src/helia_profiler/runtime_records.py:36`

### helia_profiler.RuntimeQualification.UNSUPPORTED

`constant` · `python`

```python
UNSUPPORTED = 'unsupported'
```

Source: `src/helia_profiler/runtime_records.py:37`

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

Source: `src/helia_profiler/runtime_records.py:57`

### helia_profiler.RuntimeRecord.name

`attribute` · `python`

```python
name: str
```

Source: `src/helia_profiler/runtime_records.py:59`

### helia_profiler.RuntimeRecord.version

`attribute` · `python`

```python
version: str
```

Source: `src/helia_profiler/runtime_records.py:60`

### helia_profiler.RuntimeRecord.default

`attribute` · `python`

```python
default: bool
```

Source: `src/helia_profiler/runtime_records.py:61`

### helia_profiler.RuntimeRecord.source

`attribute` · `python`

```python
source: RuntimeSource
```

Source: `src/helia_profiler/runtime_records.py:62`

### helia_profiler.RuntimeRecord.precisions

`attribute` · `python`

```python
precisions: Mapping[str, str | None]
```

Source: `src/helia_profiler/runtime_records.py:64`

### helia_profiler.RuntimeRecord.qualified

`attribute` · `python`

```python
qualified: tuple[QualifiedTarget, ...]
```

Source: `src/helia_profiler/runtime_records.py:65`

## helia_profiler.Qualification

`class` · `python`

```python
Qualification(state: RuntimeQualification, reason: str | None, record: RuntimeRecord | None) -> None
```

`dataclass`

**API tier:** `experimental`

Source: `src/helia_profiler/runtime_records.py:68`

### helia_profiler.Qualification.state

`attribute` · `python`

```python
state: RuntimeQualification
```

Source: `src/helia_profiler/runtime_records.py:70`

### helia_profiler.Qualification.reason

`attribute` · `python`

```python
reason: str | None
```

Source: `src/helia_profiler/runtime_records.py:71`

### helia_profiler.Qualification.record

`attribute` · `python`

```python
record: RuntimeRecord | None
```

Source: `src/helia_profiler/runtime_records.py:72`

## helia_profiler.runtimes

`function` · `python`

```python
runtimes() -> tuple[RuntimeRecord, ...]
```

`cached`

Every runtime record shipped with this heliaPROFILER, by name then version.

**API tier:** `experimental`

Source: `src/helia_profiler/runtime_records.py:75`

## helia_profiler.qualification

`function` · `python`

```python
qualification(name: str, version: str | None = None, *, board: str, clock: str, precision: str) -> Qualification
```

Whether ``name`` at ``version`` is qualified for ``precision`` on ``board`` at ``clock``.

A version without a record is unsupported here, even one an engine's own
version check would build: this heliaPROFILER makes no claim about it.

**API tier:** `experimental`

Source: `src/helia_profiler/runtime_records.py:121`
