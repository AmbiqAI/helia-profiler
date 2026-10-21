# helia_profiler.compiled_build

Build once, run later: a frozen plan's compiled firmware, bound to its plan and saved so any host can verify, relocate and capture from it, each run saved as a verifiable result that loads back on any host, how model state carries across a run's invocations, and the build and capture records a compiled build and a run result carry. The hpx freeze, hpx build, hpx validate build and hpx run commands drive the same flow from the command line.

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
are several instances, never one carried state. The fixture firmware
refuses a timed span of zero ticks of its 32.768 kHz timer, so a
``SINGLE`` invocation shorter than about 30.5 µs can fail as
``TIMING_BOUND``.

**API tier:** `experimental`

Source: `src/helia_profiler/lifecycle.py:17`

### helia_profiler.Lifecycle.CARRY

`constant` · `python`

```python
CARRY = 'carry'
```

Source: `src/helia_profiler/lifecycle.py:33`

### helia_profiler.Lifecycle.SINGLE

`constant` · `python`

```python
SINGLE = 'single'
```

Source: `src/helia_profiler/lifecycle.py:34`

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

``binding``, an ``hpx.binding`` semantic hash, ties the build to the plan
and lifecycle it was compiled from; :func:`build` computes it and
:meth:`verify` recomputes it.

**API tier:** `experimental`

Source: `src/helia_profiler/compiled_build.py:61`

### helia_profiler.CompiledBuild.plan

`attribute` · `python`

```python
plan: FrozenRun
```

Source: `src/helia_profiler/compiled_build.py:70`

### helia_profiler.CompiledBuild.build

`attribute` · `python`

```python
build: FixtureBuild
```

Source: `src/helia_profiler/compiled_build.py:71`

### helia_profiler.CompiledBuild.lifecycle

`attribute` · `python`

```python
lifecycle: Lifecycle
```

Source: `src/helia_profiler/compiled_build.py:72`

### helia_profiler.CompiledBuild.binding

`attribute` · `python`

```python
binding: str
```

Source: `src/helia_profiler/compiled_build.py:73`

### helia_profiler.CompiledBuild.identity

`attribute` · `python`

```python
identity: RunIdentity
```

The build's identity, with its plan's ``intent_hash``; ``observation_hash`` is ``None`` until it runs.

Source: `src/helia_profiler/compiled_build.py:76`

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
heliaAOT compare with the plan's pins. LiteRT compares with this HPX's
prepared LiteRT record, because the plan pins the modules an ordinary
profile builds from source, which a fixture build does not link.

**Raises**

| Name | Description |
| --- | --- |
| ConfigError | Naming the first fact that does not match. |

Source: `src/helia_profiler/compiled_build.py:91`

### helia_profiler.CompiledBuild.save

`method` · `python`

```python
save(path: str | Path, *, roots: Mapping[str, Path]) -> Path
```

Write the build as JSON with paths relative to *roots* and return its path.

Source: `src/helia_profiler/compiled_build.py:205`

### helia_profiler.CompiledBuild.load

`method` · `python`

```python
load(path: str | Path, *, roots: Mapping[str, Path]) -> CompiledBuild
```

`classmethod`

Read and verify a build written by :meth:`save`, resolving paths under *roots*.

Source: `src/helia_profiler/compiled_build.py:230`

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
    power: FixtureRailPowerRequest | FixtureBoardPowerRequest | None = None,
    power_guard: FixturePowerGuard | None = None,
) -> RunResult
```

Flash the recorded image, capture one run and compare outputs when *expect* is given.

A failed capture comes back with ``capture.state == "failure"`` and no
verification; errors raised while capturing propagate.

*power* selects the instrument: a :class:`FixtureRailPowerRequest`
measures the MCU rail of a build compiled with the same
``power_sync`` wiring, and a :class:`FixtureBoardPowerRequest`
powers the board through a configured supply for a timing capture
of a build without power wiring. Either needs *power_guard*, the
station's authority over that instrument and rail; *guard* keeps
its authority over the board and probe. Every refusal comes before
the device is touched.

**Raises**

| Name | Description |
| --- | --- |
| ConfigError | The build does not verify, *expect* cannot be compared, or *power* does not fit the build or the run API's rail-energy route. |
| CaptureError | The device reports counts other than the build's. |

Source: `src/helia_profiler/compiled_build.py:272`

## helia_profiler.RunResultError

`class` · `python`

```python
RunResultError(code: str, message: str, *, hint: str | None = None) -> None
```

A saved run result that does not load; ``code`` names the check it failed.

The codes are the verified result reader's: ``invalid_json`` (not strict
JSON), ``unsupported_schema`` (another schema or version),
``invalid_path`` (unusable roots, or a result not where it records
itself), ``artifact_changed`` (a pinned file missing or changed),
``runtime_missing`` (the build's prepared runtime is not in the cache),
``identity_mismatch`` (the identity or output comparison does not
recompute) and ``invalid_evidence`` (anything else: an unreadable or
malformed result, a build that does not verify, a power request the build
cannot take).

**API tier:** `experimental`

Source: `src/helia_profiler/run_record.py:62`

### helia_profiler.RunResultError.code

`attribute` · `python`

```python
code = code
```

Source: `src/helia_profiler/run_record.py:77`

## helia_profiler.RunResultRecord

`class` · `python`

```python
RunResultRecord(
    path: Path,
    size_bytes: int,
    sha256: str,
    hpx_version: str,
    result: RunResult,
    document: Mapping[str, Any],
) -> None
```

`dataclass`

A saved run result, loaded and verified.

``path``, ``size_bytes`` and ``sha256`` identify the saved document,
``document`` is its JSON, which the portable view cites by JSON pointer,
``hpx_version`` is the heliaPROFILER that wrote it, and ``result`` is the
run it records.

**API tier:** `experimental`

Source: `src/helia_profiler/run_record.py:81`

### helia_profiler.RunResultRecord.path

`attribute` · `python`

```python
path: Path
```

Source: `src/helia_profiler/run_record.py:91`

### helia_profiler.RunResultRecord.size_bytes

`attribute` · `python`

```python
size_bytes: int
```

Source: `src/helia_profiler/run_record.py:92`

### helia_profiler.RunResultRecord.sha256

`attribute` · `python`

```python
sha256: str
```

Source: `src/helia_profiler/run_record.py:93`

### helia_profiler.RunResultRecord.hpx_version

`attribute` · `python`

```python
hpx_version: str
```

Source: `src/helia_profiler/run_record.py:94`

### helia_profiler.RunResultRecord.result

`attribute` · `python`

```python
result: RunResult
```

Source: `src/helia_profiler/run_record.py:95`

### helia_profiler.RunResultRecord.document

`attribute` · `python`

```python
document: Mapping[str, Any]
```

Source: `src/helia_profiler/run_record.py:96`

## helia_profiler.load_run

`function` · `python`

```python
load_run(path: str | Path, *, roots: Mapping[str, Path]) -> RunResultRecord
```

Read and verify a run result saved by :meth:`RunResult.save`, resolving paths under *roots*.

**API tier:** `experimental`

**Raises**

| Name | Description |
| --- | --- |
| RunResultError | A ``ConfigError`` naming the first check that fails: the schema or version, the compiled build, a pinned file, the identity or the output comparison. |

Source: `src/helia_profiler/run_record.py:99`

## helia_profiler.RunResult

`class` · `python`

```python
RunResult(
    compiled: CompiledBuild,
    capture: FixtureCaptureResult,
    identity: RunIdentity,
    verification: Verification | None,
    power: FixtureRailPowerRequest | FixtureBoardPowerRequest | None = None,
) -> None
```

`dataclass`

One capture from a :class:`CompiledBuild`.

``power`` is the power request the run was captured with, or ``None``.

**API tier:** `experimental`

Source: `src/helia_profiler/compiled_build.py:508`

### helia_profiler.RunResult.compiled

`attribute` · `python`

```python
compiled: CompiledBuild
```

Source: `src/helia_profiler/compiled_build.py:515`

### helia_profiler.RunResult.capture

`attribute` · `python`

```python
capture: FixtureCaptureResult
```

Source: `src/helia_profiler/compiled_build.py:516`

### helia_profiler.RunResult.identity

`attribute` · `python`

```python
identity: RunIdentity
```

Source: `src/helia_profiler/compiled_build.py:517`

### helia_profiler.RunResult.verification

`attribute` · `python`

```python
verification: Verification | None
```

Source: `src/helia_profiler/compiled_build.py:518`

### helia_profiler.RunResult.power

`attribute` · `python`

```python
power: FixtureRailPowerRequest | FixtureBoardPowerRequest | None = None
```

Source: `src/helia_profiler/compiled_build.py:519`

### helia_profiler.RunResult.succeeded

`attribute` · `python`

```python
succeeded: bool
```

True only for a ``success`` capture.

Source: `src/helia_profiler/compiled_build.py:522`

### helia_profiler.RunResult.energy

`attribute` · `python`

```python
energy: FixtureRailEnergy | None
```

The capture's gross MCU-rail energy, or ``None`` when it measured none.

It holds the measured iterations, duration, mean power and current,
energy and energy per iteration, the rail and instrument serial, the
hash-pinned raw instrument trace, and the protocol-2 READY, GO,
gate and DONE samples in ``completion``. A failed capture measures
none.

Source: `src/helia_profiler/compiled_build.py:527`

### helia_profiler.RunResult.save

`method` · `python`

```python
save(path: str | Path, *, roots: Mapping[str, Path], build: str | Path) -> Path
```

Write the run as a ``hpx.run-result`` with paths relative to *roots* and return its path.

*build* is the file the run's compiled build was saved to; the result
pins it by sha256 and size. The capture's files are pinned as they
are; the reference outputs of a verification are written beside the
result under unique names and pinned too. Every path must sit under
one of *roots*. :func:`load_run` reads it back.

**Raises**

| Name | Description |
| --- | --- |
| ConfigError | *build* is not this run's compiled build, a file is missing or changed, a path is under no root, or the identity or verification does not recompute from the build and capture. |

Source: `src/helia_profiler/compiled_build.py:538`

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
iterations and warmups the firmware runs. A *method* with ``power_sync``
compiles the protocol-2 power firmware, which a run measures with a
matching :class:`~helia_profiler.FixtureRailPowerRequest`; it is admitted
for a heliaAOT or LiteRT plan on the ``apollo510_evb`` under
``Lifecycle.CARRY``, and refused otherwise. A plan frozen with power
enabled needs such a method. *host* supplies host-local
settings, such as ``{"work_dir": ...}``, as :meth:`FrozenRun.config` does.
heliaRT and LiteRT link a prepared runtime: without *runtime*, the one the
plan names is taken from the HPX cache (``hpx runtimes prepare``), never
downloaded or built. The result is verified before it is returned.

**API tier:** `experimental`

Source: `src/helia_profiler/compiled_build.py:557`

## helia_profiler.load_build

`function` · `python`

```python
load_build(path: str | Path, *, roots: Mapping[str, Path]) -> CompiledBuild
```

Read and verify a compiled build written by :meth:`CompiledBuild.save`.

**API tier:** `experimental`

Source: `src/helia_profiler/compiled_build.py:763`

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
    power: FixtureRailPowerRequest | FixtureBoardPowerRequest | None = None,
    power_guard: FixturePowerGuard | None = None,
) -> RunResult
```

Flash *compiled*'s image, capture one run and compare outputs; see :meth:`CompiledBuild.run`.

**API tier:** `experimental`

Source: `src/helia_profiler/compiled_build.py:768`
