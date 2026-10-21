# helia_profiler.identity

Shared identity vocabulary: the identity of a captured run and the build, run, runtime and kernel facts it names, canonical engine and kernel names with today's spellings as aliases, and unknown facts that carry their reason.

Every name on this page is imported from `helia_profiler`.

**API tier:** `experimental`

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__`.

## helia_profiler.CanonicalEngine

`class` · `python`

```python
CanonicalEngine()
```

An inference engine, by its shared name.

**API tier:** `experimental`

Source: `src/helia_profiler/identity.py:24`

### helia_profiler.CanonicalEngine.LITERT

`constant` · `python`

```python
LITERT = 'litert'
```

Source: `src/helia_profiler/identity.py:27`

### helia_profiler.CanonicalEngine.HELIA_RT

`constant` · `python`

```python
HELIA_RT = 'helia-rt'
```

Source: `src/helia_profiler/identity.py:28`

### helia_profiler.CanonicalEngine.HELIA_AOT

`constant` · `python`

```python
HELIA_AOT = 'helia-aot'
```

Source: `src/helia_profiler/identity.py:29`

### helia_profiler.CanonicalEngine.EXECUTORCH

`constant` · `python`

```python
EXECUTORCH = 'executorch'
```

Source: `src/helia_profiler/identity.py:30`

## helia_profiler.CanonicalKernel

`class` · `python`

```python
CanonicalKernel()
```

A kernel library, by its shared name.

``CMSIS_NN`` is Arm's CMSIS-NN. ``HELIA_CORE`` is heliaCORE, the product
name of Ambiq's ``ns-cmsis-nn`` library.

**API tier:** `experimental`

Source: `src/helia_profiler/identity.py:33`

### helia_profiler.CanonicalKernel.REFERENCE

`constant` · `python`

```python
REFERENCE = 'reference'
```

Source: `src/helia_profiler/identity.py:40`

### helia_profiler.CanonicalKernel.CMSIS_NN

`constant` · `python`

```python
CMSIS_NN = 'cmsis-nn'
```

Source: `src/helia_profiler/identity.py:41`

### helia_profiler.CanonicalKernel.HELIA_CORE

`constant` · `python`

```python
HELIA_CORE = 'helia-core'
```

Source: `src/helia_profiler/identity.py:42`

## helia_profiler.Unknown

`class` · `python`

```python
Unknown(reason: str) -> None
```

`dataclass`

A fact that could not be determined, with the reason.

**API tier:** `experimental`

Source: `src/helia_profiler/identity.py:45`

### helia_profiler.Unknown.reason

`attribute` · `python`

```python
reason: str
```

Source: `src/helia_profiler/identity.py:49`

## helia_profiler.canonical_engine

`function` · `python`

```python
canonical_engine(name: str) -> CanonicalEngine
```

The shared name for an engine, from its canonical name or any alias.

**API tier:** `experimental`

Source: `src/helia_profiler/identity.py:84`

## helia_profiler.canonical_kernel

`function` · `python`

```python
canonical_kernel(name: str) -> CanonicalKernel
```

The shared name for a kernel library, from its canonical name or any alias.

**API tier:** `experimental`

Source: `src/helia_profiler/identity.py:89`

## helia_profiler.RuntimeIdentity

`class` · `python`

```python
RuntimeIdentity(
    engine: CanonicalEngine,
    repository: str | Unknown,
    version: str | Unknown,
    commit: str | Unknown,
) -> None
```

`dataclass`

The runtime a build linked: its engine, source repository, version and commit.

**API tier:** `experimental`

Source: `src/helia_profiler/identity.py:105`

### helia_profiler.RuntimeIdentity.engine

`attribute` · `python`

```python
engine: CanonicalEngine
```

Source: `src/helia_profiler/identity.py:109`

### helia_profiler.RuntimeIdentity.repository

`attribute` · `python`

```python
repository: str | Unknown
```

Source: `src/helia_profiler/identity.py:110`

### helia_profiler.RuntimeIdentity.version

`attribute` · `python`

```python
version: str | Unknown
```

Source: `src/helia_profiler/identity.py:111`

### helia_profiler.RuntimeIdentity.commit

`attribute` · `python`

```python
commit: str | Unknown
```

Source: `src/helia_profiler/identity.py:112`

## helia_profiler.KernelIdentity

`class` · `python`

```python
KernelIdentity(
    library: CanonicalKernel | Unknown,
    repository: str | Unknown,
    version: str | Unknown,
    commit: str | Unknown,
) -> None
```

`dataclass`

The kernel library a build linked, taken from the module or archive it linked.

**API tier:** `experimental`

Source: `src/helia_profiler/identity.py:115`

### helia_profiler.KernelIdentity.library

`attribute` · `python`

```python
library: CanonicalKernel | Unknown
```

Source: `src/helia_profiler/identity.py:119`

### helia_profiler.KernelIdentity.repository

`attribute` · `python`

```python
repository: str | Unknown
```

Source: `src/helia_profiler/identity.py:120`

### helia_profiler.KernelIdentity.version

`attribute` · `python`

```python
version: str | Unknown
```

Source: `src/helia_profiler/identity.py:121`

### helia_profiler.KernelIdentity.commit

`attribute` · `python`

```python
commit: str | Unknown
```

Source: `src/helia_profiler/identity.py:122`

## helia_profiler.BuildFacts

`class` · `python`

```python
BuildFacts(
    toolchain: str | Unknown,
    toolchain_version: str | Unknown,
    board: str,
    cpu_hz: int | Unknown,
    clock_profile: str | Unknown,
    arena_location: str | Unknown,
    weights_location: str | Unknown,
    arena_bytes: int | Unknown,
) -> None
```

`dataclass`

How the image was built and placed.

**API tier:** `experimental`

Source: `src/helia_profiler/identity.py:125`

### helia_profiler.BuildFacts.toolchain

`attribute` · `python`

```python
toolchain: str | Unknown
```

Source: `src/helia_profiler/identity.py:129`

### helia_profiler.BuildFacts.toolchain_version

`attribute` · `python`

```python
toolchain_version: str | Unknown
```

Source: `src/helia_profiler/identity.py:130`

### helia_profiler.BuildFacts.board

`attribute` · `python`

```python
board: str
```

Source: `src/helia_profiler/identity.py:131`

### helia_profiler.BuildFacts.cpu_hz

`attribute` · `python`

```python
cpu_hz: int | Unknown
```

Source: `src/helia_profiler/identity.py:132`

### helia_profiler.BuildFacts.clock_profile

`attribute` · `python`

```python
clock_profile: str | Unknown
```

Source: `src/helia_profiler/identity.py:133`

### helia_profiler.BuildFacts.arena_location

`attribute` · `python`

```python
arena_location: str | Unknown
```

Source: `src/helia_profiler/identity.py:134`

### helia_profiler.BuildFacts.weights_location

`attribute` · `python`

```python
weights_location: str | Unknown
```

Source: `src/helia_profiler/identity.py:135`

### helia_profiler.BuildFacts.arena_bytes

`attribute` · `python`

```python
arena_bytes: int | Unknown
```

Source: `src/helia_profiler/identity.py:136`

## helia_profiler.RunFacts

`class` · `python`

```python
RunFacts(
    lifecycle: str,
    iterations: int,
    warmups: int,
    timing_scope: str,
    instance_boundary: str | Unknown = _UNRECORDED,
    first_invoke_initializer: bool | Unknown = _UNRECORDED,
    first_invoke_window: str | Unknown = _UNRECORDED,
    timing_clock: str | Unknown = _UNRECORDED,
    timing_clock_hz: int | Unknown = _UNRECORDED,
    timing_statistic: str | Unknown = _UNRECORDED,
) -> None
```

`dataclass`

How the model was run: state handling, invocation counts, the timed span and its clock.

``timing_scope`` is what each timed span covers: ``invoke_only`` or
``restore_and_invoke``, which also restores the fixed inputs.
``instance_boundary`` is where one model instance ends: ``boot`` when the
engine initializes once and every invocation reuses it, ``invocation``
when each invocation loads and initializes its own.
``first_invoke_initializer`` is whether the first invocation of an
instance runs the model's one-time initializer, such as a LiteRT
``CALL_ONCE`` subgraph, and ``first_invoke_window`` where that invocation
falls: ``warmup`` or ``measured``. ``timing_clock`` is the clock that times
the span, ``cpu`` cycles or the 32.768 kHz ``stimer``; ``timing_clock_hz``
is its configured rate, not a measured one. ``timing_statistic`` is what
the reported time is: the ``total`` over the measured invocations or
their ``mean``.

**API tier:** `experimental`

Source: `src/helia_profiler/identity.py:142`

### helia_profiler.RunFacts.lifecycle

`attribute` · `python`

```python
lifecycle: str
```

Source: `src/helia_profiler/identity.py:161`

### helia_profiler.RunFacts.iterations

`attribute` · `python`

```python
iterations: int
```

Source: `src/helia_profiler/identity.py:162`

### helia_profiler.RunFacts.warmups

`attribute` · `python`

```python
warmups: int
```

Source: `src/helia_profiler/identity.py:163`

### helia_profiler.RunFacts.timing_scope

`attribute` · `python`

```python
timing_scope: str
```

Source: `src/helia_profiler/identity.py:164`

### helia_profiler.RunFacts.instance_boundary

`attribute` · `python`

```python
instance_boundary: str | Unknown = _UNRECORDED
```

Source: `src/helia_profiler/identity.py:165`

### helia_profiler.RunFacts.first_invoke_initializer

`attribute` · `python`

```python
first_invoke_initializer: bool | Unknown = _UNRECORDED
```

Source: `src/helia_profiler/identity.py:166`

### helia_profiler.RunFacts.first_invoke_window

`attribute` · `python`

```python
first_invoke_window: str | Unknown = _UNRECORDED
```

Source: `src/helia_profiler/identity.py:167`

### helia_profiler.RunFacts.timing_clock

`attribute` · `python`

```python
timing_clock: str | Unknown = _UNRECORDED
```

Source: `src/helia_profiler/identity.py:168`

### helia_profiler.RunFacts.timing_clock_hz

`attribute` · `python`

```python
timing_clock_hz: int | Unknown = _UNRECORDED
```

Source: `src/helia_profiler/identity.py:169`

### helia_profiler.RunFacts.timing_statistic

`attribute` · `python`

```python
timing_statistic: str | Unknown = _UNRECORDED
```

Source: `src/helia_profiler/identity.py:170`

## helia_profiler.RunIdentity

`class` · `python`

```python
RunIdentity(
    runtime: RuntimeIdentity,
    kernels: KernelIdentity,
    prepared_archive_sha256: str | None,
    build: BuildFacts,
    baseline_id: str | Unknown,
    dependency_lock_sha256: str | Unknown,
    model_sha256: str | Unknown,
    inputs_sha256: str | Unknown,
    run: RunFacts,
    producer: str | Unknown,
    build_hash: str | Unknown,
    observation_hash: str | None = None,
    intent_hash: str | None = None,
) -> None
```

`dataclass`

Everything a comparison needs to know about what ran.

``build_hash`` covers the flashed image, the dependency lock, the linked
runtime archive and the model, and never the HPX release (``producer``),
so it changes only when what runs changes. ``observation_hash`` covers a
capture's measured facts, so the same firmware measured twice gives two.
``intent_hash`` is the :attr:`~helia_profiler.FrozenRun.intent_hash` of
the plan a run was built from, and ``None`` for a run built without one.

**API tier:** `experimental`

Source: `src/helia_profiler/identity.py:173`

### helia_profiler.RunIdentity.runtime

`attribute` · `python`

```python
runtime: RuntimeIdentity
```

Source: `src/helia_profiler/identity.py:185`

### helia_profiler.RunIdentity.kernels

`attribute` · `python`

```python
kernels: KernelIdentity
```

Source: `src/helia_profiler/identity.py:186`

### helia_profiler.RunIdentity.prepared_archive_sha256

`attribute` · `python`

```python
prepared_archive_sha256: str | None
```

Source: `src/helia_profiler/identity.py:187`

### helia_profiler.RunIdentity.build

`attribute` · `python`

```python
build: BuildFacts
```

Source: `src/helia_profiler/identity.py:188`

### helia_profiler.RunIdentity.baseline_id

`attribute` · `python`

```python
baseline_id: str | Unknown
```

Source: `src/helia_profiler/identity.py:189`

### helia_profiler.RunIdentity.dependency_lock_sha256

`attribute` · `python`

```python
dependency_lock_sha256: str | Unknown
```

Source: `src/helia_profiler/identity.py:190`

### helia_profiler.RunIdentity.model_sha256

`attribute` · `python`

```python
model_sha256: str | Unknown
```

Source: `src/helia_profiler/identity.py:191`

### helia_profiler.RunIdentity.inputs_sha256

`attribute` · `python`

```python
inputs_sha256: str | Unknown
```

Source: `src/helia_profiler/identity.py:192`

### helia_profiler.RunIdentity.run

`attribute` · `python`

```python
run: RunFacts
```

Source: `src/helia_profiler/identity.py:193`

### helia_profiler.RunIdentity.producer

`attribute` · `python`

```python
producer: str | Unknown
```

Source: `src/helia_profiler/identity.py:194`

### helia_profiler.RunIdentity.build_hash

`attribute` · `python`

```python
build_hash: str | Unknown
```

Source: `src/helia_profiler/identity.py:195`

### helia_profiler.RunIdentity.observation_hash

`attribute` · `python`

```python
observation_hash: str | None = None
```

Source: `src/helia_profiler/identity.py:196`

### helia_profiler.RunIdentity.intent_hash

`attribute` · `python`

```python
intent_hash: str | None = None
```

Source: `src/helia_profiler/identity.py:197`

### helia_profiler.RunIdentity.to_dict

`method` · `python`

```python
to_dict() -> dict[str, Any]
```

JSON-ready form; an unknown fact becomes ``{"unknown": reason}``.

Source: `src/helia_profiler/identity.py:199`
