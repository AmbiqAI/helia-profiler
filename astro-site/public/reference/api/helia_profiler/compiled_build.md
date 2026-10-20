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

``CARRY`` initializes one instance and keeps its internal state through
warmups and timed invocations, restoring only the fixed inputs each time.
``SINGLE`` runs one measured invocation per initialized instance, so no
state carries between invocations. Where an instance ends is the engine's
:class:`InstanceBoundary`: for a ``boot`` engine ``SINGLE`` is one timed
invocation and no warmups per boot; for an ``invocation`` engine, such as
ExecuTorch, every invocation is its own instance, so several measurements
are several instances, never one carried state.

**API tier:** `experimental`

Source: `src/helia_profiler/lifecycle.py:17`

### helia_profiler.Lifecycle.CARRY

`constant` · `python`

```python
CARRY = 'carry'
```

Source: `src/helia_profiler/lifecycle.py:30`

### helia_profiler.Lifecycle.SINGLE

`constant` · `python`

```python
SINGLE = 'single'
```

Source: `src/helia_profiler/lifecycle.py:31`

## helia_profiler.hpx_cache_root

`function` · `python`

```python
hpx_cache_root() -> Path
```

Return the root directory for hpx's persistent caches.

Resolved at call time so environment overrides apply per-invocation;
callers must not cache the result at import time.

**API tier:** `experimental`

Source: `src/helia_profiler/hostenv/cache_dirs.py:26`

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

Source: `src/helia_profiler/compiled_build.py:38`

### helia_profiler.CompiledBuild.plan

`attribute` · `python`

```python
plan: FrozenRun
```

Source: `src/helia_profiler/compiled_build.py:46`

### helia_profiler.CompiledBuild.build

`attribute` · `python`

```python
build: FixtureBuild
```

Source: `src/helia_profiler/compiled_build.py:47`

### helia_profiler.CompiledBuild.lifecycle

`attribute` · `python`

```python
lifecycle: Lifecycle
```

Source: `src/helia_profiler/compiled_build.py:48`

### helia_profiler.CompiledBuild.binding

`attribute` · `python`

```python
binding: str
```

Source: `src/helia_profiler/compiled_build.py:49`

### helia_profiler.CompiledBuild.identity

`attribute` · `python`

```python
identity: RunIdentity
```

The build's identity, with its plan's ``intent_hash``; ``observation_hash`` is ``None`` until it runs.

Source: `src/helia_profiler/compiled_build.py:52`

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

The runtime version and commit and the kernel commit are compared
wherever both the plan and the build state one; a build or plan
that states none of a fact is not compared on it. heliaRT and
heliaAOT compare with the plan's pins. TFLM compares with this HPX's
prepared TFLM record, because the plan pins the modules an ordinary
profile builds from source, which a fixture build does not link.

**Raises**

| Name | Description |
| --- | --- |
| ConfigError | Naming the first fact that does not match. |

Source: `src/helia_profiler/compiled_build.py:65`

### helia_profiler.CompiledBuild.save

`method` · `python`

```python
save(path: str | Path, *, roots: Mapping[str, Path]) -> Path
```

Write the build as JSON with paths relative to *roots* and return its path.

Source: `src/helia_profiler/compiled_build.py:178`

### helia_profiler.CompiledBuild.load

`method` · `python`

```python
load(path: str | Path, *, roots: Mapping[str, Path]) -> CompiledBuild
```

`classmethod`

Read and verify a build written by :meth:`save`, resolving paths under *roots*.

Source: `src/helia_profiler/compiled_build.py:209`

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

**Raises**

| Name | Description |
| --- | --- |
| ConfigError | The build does not verify, or *expect* cannot be compared. |
| CaptureError | The device reports counts other than the build's. |

Source: `src/helia_profiler/compiled_build.py:245`

## helia_profiler.RunResult

`class` · `python`

```python
RunResult(
    compiled: CompiledBuild,
    capture: FixtureCaptureResult,
    identity: RunIdentity,
    verification: Verification | None,
) -> None
```

`dataclass`

One capture from a :class:`CompiledBuild`.

**API tier:** `experimental`

Source: `src/helia_profiler/compiled_build.py:308`

### helia_profiler.RunResult.compiled

`attribute` · `python`

```python
compiled: CompiledBuild
```

Source: `src/helia_profiler/compiled_build.py:312`

### helia_profiler.RunResult.capture

`attribute` · `python`

```python
capture: FixtureCaptureResult
```

Source: `src/helia_profiler/compiled_build.py:313`

### helia_profiler.RunResult.identity

`attribute` · `python`

```python
identity: RunIdentity
```

Source: `src/helia_profiler/compiled_build.py:314`

### helia_profiler.RunResult.verification

`attribute` · `python`

```python
verification: Verification | None
```

Source: `src/helia_profiler/compiled_build.py:315`

### helia_profiler.RunResult.succeeded

`attribute` · `python`

```python
succeeded: bool
```

True only for a ``success`` capture.

Source: `src/helia_profiler/compiled_build.py:318`

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
heliaRT and TFLM link a prepared runtime: without *runtime*, the one the
plan names is taken from the HPX cache (``hpx runtimes prepare``), never
downloaded or built. The result is verified before it is returned.

**API tier:** `experimental`

Source: `src/helia_profiler/compiled_build.py:323`

## helia_profiler.load_build

`function` · `python`

```python
load_build(path: str | Path, *, roots: Mapping[str, Path]) -> CompiledBuild
```

Read and verify a compiled build written by :meth:`CompiledBuild.save`.

**API tier:** `experimental`

Source: `src/helia_profiler/compiled_build.py:471`

## helia_profiler.run_build

`function` · `python`

```python
run_build(
    compiled: CompiledBuild,
    *,
    guard: FixtureCaptureGuard,
    jlink_serial: str,
    evidence_dir: Path,
    settle_seconds: float,
    expect: Expect | None = None,
    expected_duration_s: float | None = None,
) -> RunResult
```

Flash *compiled*'s image, capture one run and compare outputs; see :meth:`CompiledBuild.run`.

**API tier:** `experimental`

Source: `src/helia_profiler/compiled_build.py:476`
