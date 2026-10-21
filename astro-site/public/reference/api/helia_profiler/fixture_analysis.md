# helia_profiler.fixture_analysis

What this HPX can run: engines, data types and qualified targets.

Every name on this page is imported from `helia_profiler`.

**API tier:** `experimental`

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__`.

## helia_profiler.capabilities

`function` · `python`

```python
capabilities() -> FixtureCapabilities
```

The targets, engines and IO element types a run can be built and captured with.

It reports what this HPX implements, not whether a runtime release is
qualified on a board; :func:`~helia_profiler.qualification` answers that.

**API tier:** `experimental`

Source: `src/helia_profiler/run_inputs.py:23`

## helia_profiler.QualifiedFixtureTarget

`class` · `python`

```python
QualifiedFixtureTarget(
    board: str,
    clock_profile: str,
    cpu_hz: int,
    device: str,
    load_address: int,
    timer_hz: int,
    image_limit: int,
    settle_ticks: tuple[int, int],
    engines: tuple[EngineType, ...] = (),
) -> None
```

`dataclass`

A supported fixture build and capture target, not model qualification.

**API tier:** `experimental`

Source: `src/helia_profiler/_fixture_facts.py:25`

### helia_profiler.QualifiedFixtureTarget.board

`attribute` · `python`

```python
board: str
```

Source: `src/helia_profiler/_fixture_facts.py:29`

### helia_profiler.QualifiedFixtureTarget.clock_profile

`attribute` · `python`

```python
clock_profile: str
```

Source: `src/helia_profiler/_fixture_facts.py:30`

### helia_profiler.QualifiedFixtureTarget.cpu_hz

`attribute` · `python`

```python
cpu_hz: int
```

Source: `src/helia_profiler/_fixture_facts.py:31`

### helia_profiler.QualifiedFixtureTarget.device

`attribute` · `python`

```python
device: str
```

Source: `src/helia_profiler/_fixture_facts.py:32`

### helia_profiler.QualifiedFixtureTarget.load_address

`attribute` · `python`

```python
load_address: int
```

Source: `src/helia_profiler/_fixture_facts.py:33`

### helia_profiler.QualifiedFixtureTarget.timer_hz

`attribute` · `python`

```python
timer_hz: int
```

Source: `src/helia_profiler/_fixture_facts.py:34`

### helia_profiler.QualifiedFixtureTarget.image_limit

`attribute` · `python`

```python
image_limit: int
```

Source: `src/helia_profiler/_fixture_facts.py:35`

### helia_profiler.QualifiedFixtureTarget.settle_ticks

`attribute` · `python`

```python
settle_ticks: tuple[int, int]
```

Source: `src/helia_profiler/_fixture_facts.py:36`

### helia_profiler.QualifiedFixtureTarget.engines

`attribute` · `python`

```python
engines: tuple[EngineType, ...] = ()
```

Source: `src/helia_profiler/_fixture_facts.py:37`

## helia_profiler.EngineDTypeCapability

`class` · `python`

```python
EngineDTypeCapability(engine: EngineType, dtype: FixtureDType, capability: FixtureCapability) -> None
```

`dataclass`

Producer status of one engine and IO element type.

**API tier:** `experimental`

Source: `src/helia_profiler/_fixture_facts.py:40`

### helia_profiler.EngineDTypeCapability.engine

`attribute` · `python`

```python
engine: EngineType
```

Source: `src/helia_profiler/_fixture_facts.py:44`

### helia_profiler.EngineDTypeCapability.dtype

`attribute` · `python`

```python
dtype: FixtureDType
```

Source: `src/helia_profiler/_fixture_facts.py:45`

### helia_profiler.EngineDTypeCapability.capability

`attribute` · `python`

```python
capability: FixtureCapability
```

Source: `src/helia_profiler/_fixture_facts.py:46`

## helia_profiler.FixtureCapabilities

`class` · `python`

```python
FixtureCapabilities(
    targets: tuple[QualifiedFixtureTarget, ...],
    engines: tuple[EngineDTypeCapability, ...],
    readback_budget: int,
) -> None
```

`dataclass`

Targets, engine and dtype status, and limits of this heliaPROFILER's runs.

**API tier:** `experimental`

Source: `src/helia_profiler/_fixture_facts.py:49`

### helia_profiler.FixtureCapabilities.targets

`attribute` · `python`

```python
targets: tuple[QualifiedFixtureTarget, ...]
```

Source: `src/helia_profiler/_fixture_facts.py:53`

### helia_profiler.FixtureCapabilities.engines

`attribute` · `python`

```python
engines: tuple[EngineDTypeCapability, ...]
```

Source: `src/helia_profiler/_fixture_facts.py:54`

### helia_profiler.FixtureCapabilities.readback_budget

`attribute` · `python`

```python
readback_budget: int
```

Source: `src/helia_profiler/_fixture_facts.py:55`

## helia_profiler.FixtureCapability

`class` · `python`

```python
FixtureCapability()
```

Producer-declared status of one engine and IO dtype for fixed fixtures.

**API tier:** `experimental`

Source: `src/helia_profiler/_fixture_build.py:143`

### helia_profiler.FixtureCapability.QUALIFIED

`constant` · `python`

```python
QUALIFIED = 'qualified'
```

Source: `src/helia_profiler/_fixture_build.py:146`

### helia_profiler.FixtureCapability.SUPPORTED

`constant` · `python`

```python
SUPPORTED = 'supported'
```

Source: `src/helia_profiler/_fixture_build.py:147`

### helia_profiler.FixtureCapability.UNSUPPORTED

`constant` · `python`

```python
UNSUPPORTED = 'unsupported'
```

Source: `src/helia_profiler/_fixture_build.py:148`
