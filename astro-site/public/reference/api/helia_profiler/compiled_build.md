# helia_profiler.compiled_build

Build once, run later: a frozen plan's compiled firmware, bound to its plan and saved so any host can verify, relocate and capture from it, and how model state carries across a run's invocations.

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
invocations. The fixture firmware times with the 32.768 kHz STIMER and
refuses a timed span that reads zero ticks, so a ``SINGLE`` invocation
shorter than one tick, about 30.5 µs, can fail as out of bounds.

**API tier:** `experimental`

Source: `src/helia_profiler/lifecycle.py:17`

### helia_profiler.Lifecycle.CARRY

`constant` · `python`

```python
CARRY = 'carry'
```

Source: `src/helia_profiler/lifecycle.py:28`

### helia_profiler.Lifecycle.SINGLE

`constant` · `python`

```python
SINGLE = 'single'
```

Source: `src/helia_profiler/lifecycle.py:29`

## helia_profiler.CompiledBuild

`class` · `python`

```python
CompiledBuild(plan: FrozenRun, build: FixtureBuild, lifecycle: Lifecycle, binding: str) -> None
```

`dataclass`

A frozen plan and the firmware compiled from it, verifiable on any host.

``binding`` ties the build to the plan and lifecycle it was compiled
from; :func:`build` computes it and :meth:`verify` recomputes it.

**API tier:** `experimental`

Source: `src/helia_profiler/compiled_build.py:37`

### helia_profiler.CompiledBuild.plan

`attribute` · `python`

```python
plan: FrozenRun
```

Source: `src/helia_profiler/compiled_build.py:45`

### helia_profiler.CompiledBuild.build

`attribute` · `python`

```python
build: FixtureBuild
```

Source: `src/helia_profiler/compiled_build.py:46`

### helia_profiler.CompiledBuild.lifecycle

`attribute` · `python`

```python
lifecycle: Lifecycle
```

Source: `src/helia_profiler/compiled_build.py:47`

### helia_profiler.CompiledBuild.binding

`attribute` · `python`

```python
binding: str
```

Source: `src/helia_profiler/compiled_build.py:48`

### helia_profiler.CompiledBuild.identity

`attribute` · `python`

```python
identity: RunIdentity
```

The build's identity, with its plan's ``intent_hash``; ``observation_hash`` is ``None`` until it runs.

Source: `src/helia_profiler/compiled_build.py:51`

### helia_profiler.CompiledBuild.verify

`method` · `python`

```python
verify() -> None
```

Re-check the build against its pinned files and its plan; raise on any mismatch.

The build's files must match their hashes, the plan its intent hash,
and the binding the plan, lifecycle and build. The facts the build
records must be the ones the plan states: engine, board, model,
toolchain and its version, arena and weights placement, arena size,
clock profile and the lifecycle's invocation counts.

**Raises**

| Name | Description |
| --- | --- |
| ConfigError | Naming the first fact that does not match. |

Source: `src/helia_profiler/compiled_build.py:64`

### helia_profiler.CompiledBuild.save

`method` · `python`

```python
save(path: str | Path, *, roots: Mapping[str, Path]) -> Path
```

Write the build as JSON with paths relative to *roots* and return its path.

Source: `src/helia_profiler/compiled_build.py:146`

### helia_profiler.CompiledBuild.load

`method` · `python`

```python
load(path: str | Path, *, roots: Mapping[str, Path]) -> CompiledBuild
```

`classmethod`

Read and verify a build written by :meth:`save`, resolving paths under *roots*.

Source: `src/helia_profiler/compiled_build.py:173`

### helia_profiler.CompiledBuild.run

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

A failed capture comes back with ``capture.state == "failure"`` and no
verification; errors raised while capturing propagate.

Source: `src/helia_profiler/compiled_build.py:209`

## helia_profiler.RunResult

`class` · `python`

```python
RunResult(
    record: CompiledBuild,
    capture: FixtureCaptureResult,
    identity: RunIdentity,
    verification: Verification | None,
) -> None
```

`dataclass`

One capture from a :class:`CompiledBuild`.

**API tier:** `experimental`

Source: `src/helia_profiler/compiled_build.py:255`

### helia_profiler.RunResult.record

`attribute` · `python`

```python
record: CompiledBuild
```

Source: `src/helia_profiler/compiled_build.py:259`

### helia_profiler.RunResult.capture

`attribute` · `python`

```python
capture: FixtureCaptureResult
```

Source: `src/helia_profiler/compiled_build.py:260`

### helia_profiler.RunResult.identity

`attribute` · `python`

```python
identity: RunIdentity
```

Source: `src/helia_profiler/compiled_build.py:261`

### helia_profiler.RunResult.verification

`attribute` · `python`

```python
verification: Verification | None
```

Source: `src/helia_profiler/compiled_build.py:262`

### helia_profiler.RunResult.succeeded

`attribute` · `python`

```python
succeeded: bool
```

Source: `src/helia_profiler/compiled_build.py:265`

## helia_profiler.build

`function` · `python`

```python
build(
    plan: FrozenRun,
    fixture: TypedFixture,
    *,
    method: FixtureMethod,
    lifecycle: Lifecycle = Lifecycle.CARRY,
    runtime: PreparedUpstreamRuntime | None = None,
    model: str | Path | None = None,
    host: Mapping[str, object] | None = None,
    observe_aot_arenas: bool = False,
    observe_ram: bool = False,
) -> CompiledBuild
```

Compile *plan* for *fixture*'s pinned inputs and return the compiled build.

*lifecycle* must be one the plan's engine implements; it sets the timed
iterations and warmups the firmware runs. *host* supplies host-local
settings, such as ``{"work_dir": ...}``, as :meth:`FrozenRun.config` does.

**API tier:** `experimental`

Source: `src/helia_profiler/compiled_build.py:269`

## helia_profiler.load_build

`function` · `python`

```python
load_build(path: str | Path, *, roots: Mapping[str, Path]) -> CompiledBuild
```

Read and verify a compiled build written by :meth:`CompiledBuild.save`.

**API tier:** `experimental`

Source: `src/helia_profiler/compiled_build.py:350`

## helia_profiler.run_build

`function` · `python`

```python
run_build(
    record: CompiledBuild,
    *,
    guard: FixtureCaptureGuard,
    jlink_serial: str,
    evidence_dir: Path,
    settle_seconds: float,
    expect: Expect | None = None,
    expected_duration_s: float | None = None,
) -> RunResult
```

Flash *record*'s image, capture one run and compare outputs; see :meth:`CompiledBuild.run`.

**API tier:** `experimental`

Source: `src/helia_profiler/compiled_build.py:355`
