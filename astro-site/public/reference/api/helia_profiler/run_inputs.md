# helia_profiler.run_inputs

What a run is built for and captured under: the typed fixture that pins a model's input bytes and declares its outputs, the tensors' element types and quantization, how the run is timed, the prepared runtime a build links, the board guard a capture answers to, the model analysis that lists a model's inputs and outputs, and the targets and IO types this heliaPROFILER builds.

Every name on this page is imported from `helia_profiler`.

**API tier:** `experimental`

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__`.

## helia_profiler.OperatorGuard

`class` · `python`

```python
OperatorGuard(board: str, jlink_serial: str) -> None
```

`dataclass`

Capture authority from an operator who names the board and the J-Link probe on it.

HPX cannot prove which physical board a probe is wired to: the core's
CPUID and the probe list name a SoC and a serial, not a board. With this
guard the operator vouches for the board and holds it for the whole
capture, as ``hpx profile`` assumes. :meth:`verify_target` refuses a
capture whose board or probe serial differs from the ones the operator
named, and :meth:`check` allows every step. A harness that can prove the
board and lock it passes its own
:class:`~helia_profiler.FixtureCaptureGuard` instead.

**API tier:** `experimental`

Source: `src/helia_profiler/run_guard.py:14`

### helia_profiler.OperatorGuard.board

`attribute` · `python`

```python
board: str
```

Source: `src/helia_profiler/run_guard.py:28`

### helia_profiler.OperatorGuard.jlink_serial

`attribute` · `python`

```python
jlink_serial: str
```

Source: `src/helia_profiler/run_guard.py:29`

### helia_profiler.OperatorGuard.check

`method` · `python`

```python
check(*, require_free: bool, remaining_s: float) -> None
```

Allow the step: the operator holds the board for the whole capture.

Source: `src/helia_profiler/run_guard.py:37`

### helia_profiler.OperatorGuard.verify_target

`method` · `python`

```python
verify_target(*, target: FixtureTarget, jlink_serial: str) -> None
```

Refuse a target board or probe serial other than the operator's.

**Raises**

| Name | Description |
| --- | --- |
| ConfigError | The board or the serial differs from the operator's. |

Source: `src/helia_profiler/run_guard.py:41`

## helia_profiler.ResolverPlan

`class` · `python`

```python
ResolverPlan(mode: str, registrations: tuple[str, ...]) -> None
```

`dataclass`

Concrete registration plan for generated firmware.

**API tier:** `experimental`

Source: `src/helia_profiler/firmware/op_resolver.py:14`

### helia_profiler.ResolverPlan.mode

`attribute` · `python`

```python
mode: str
```

Source: `src/helia_profiler/firmware/op_resolver.py:18`

### helia_profiler.ResolverPlan.registrations

`attribute` · `python`

```python
registrations: tuple[str, ...]
```

Source: `src/helia_profiler/firmware/op_resolver.py:19`

### helia_profiler.ResolverPlan.max_ops

`attribute` · `python`

```python
max_ops: int
```

Source: `src/helia_profiler/firmware/op_resolver.py:22`

## helia_profiler.FixtureFile

`class` · `python`

```python
FixtureFile(path: Path, sha256: str) -> None
```

`dataclass`

A file pinned by its SHA-256; :meth:`read` refuses bytes that no longer match.

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_runtime.py:15`

### helia_profiler.FixtureFile.path

`attribute` · `python`

```python
path: Path
```

Source: `src/helia_profiler/fixture_runtime.py:19`

### helia_profiler.FixtureFile.sha256

`attribute` · `python`

```python
sha256: str
```

Source: `src/helia_profiler/fixture_runtime.py:20`

### helia_profiler.FixtureFile.read

`method` · `python`

```python
read() -> bytes
```

Source: `src/helia_profiler/fixture_runtime.py:22`

## helia_profiler.FixtureTarget

`class` · `python`

```python
FixtureTarget(board: str, device: str, load_address: int) -> None
```

`dataclass`

The board a fixture image runs on, its J-Link device and the image's load address.

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_target.py:19`

### helia_profiler.FixtureTarget.board

`attribute` · `python`

```python
board: str
```

Source: `src/helia_profiler/fixture_target.py:23`

### helia_profiler.FixtureTarget.device

`attribute` · `python`

```python
device: str
```

Source: `src/helia_profiler/fixture_target.py:24`

### helia_profiler.FixtureTarget.load_address

`attribute` · `python`

```python
load_address: int
```

Source: `src/helia_profiler/fixture_target.py:25`

### helia_profiler.FixtureTarget.verify

`method` · `python`

```python
verify() -> None
```

Source: `src/helia_profiler/fixture_target.py:27`

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

## helia_profiler.analyze_run_model

`function` · `python`

```python
analyze_run_model(path: str | Path) -> TypedFixtureModelAnalysis
```

Read a LiteRT model's public inputs and outputs without selecting an engine.

**API tier:** `experimental`

**Parameters**

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| path | str \| Path | Required | The ``.tflite`` model. |

**Returns**

| Name | Type | Description |
| --- | --- | --- |
|  | TypedFixtureModelAnalysis | The input and output tensors in model order, with their dtype, shape |
|  | TypedFixtureModelAnalysis | and quantization, and the model's graph facts. |

**Raises**

| Name | Description |
| --- | --- |
| ConfigError | The model is unreadable, or a run cannot pin inputs for it, for example because an input or output shape is dynamic. |

Source: `src/helia_profiler/run_inputs.py:34`

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

## helia_profiler.FixtureTimingScope

`class` · `python`

```python
FixtureTimingScope()
```

What the timed span covers.

``INVOKE_ONLY`` brackets each invocation; ``RESTORE_AND_INVOKE`` brackets
the timed loop, so restoring the fixed inputs before each invocation is
inside the span.

**API tier:** `experimental`

Source: `src/helia_profiler/_fixture_build.py:45`

### helia_profiler.FixtureTimingScope.INVOKE_ONLY

`constant` · `python`

```python
INVOKE_ONLY = 'invoke_only'
```

Source: `src/helia_profiler/_fixture_build.py:53`

### helia_profiler.FixtureTimingScope.RESTORE_AND_INVOKE

`constant` · `python`

```python
RESTORE_AND_INVOKE = 'restore_and_invoke'
```

Source: `src/helia_profiler/_fixture_build.py:54`

## helia_profiler.FixtureCaptureGuard

`class` · `python`

```python
FixtureCaptureGuard()
```

Caller-owned authorization and remaining-window checks.

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_capture.py:46`

### helia_profiler.FixtureCaptureGuard.check

`method` · `python`

```python
check(*, require_free: bool, remaining_s: float) -> None
```

Source: `src/helia_profiler/fixture_capture.py:49`

### helia_profiler.FixtureCaptureGuard.verify_target

`method` · `python`

```python
verify_target(*, target: FixtureTarget, jlink_serial: str) -> None
```

Require caller-verified physical board/serial identity and exclusive ownership.

Core CPUID and probe enumeration cannot establish the physical board.
Reject if the caller has no current independent board/serial evidence.

Source: `src/helia_profiler/fixture_capture.py:51`

## helia_profiler.PerTensorQuantization

`class` · `python`

```python
PerTensorQuantization(scale: float, zero_point: int) -> None
```

`dataclass`

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_analysis.py:47`

### helia_profiler.PerTensorQuantization.scale

`attribute` · `python`

```python
scale: float
```

Source: `src/helia_profiler/fixture_analysis.py:49`

### helia_profiler.PerTensorQuantization.zero_point

`attribute` · `python`

```python
zero_point: int
```

Source: `src/helia_profiler/fixture_analysis.py:50`

## helia_profiler.FixtureCapabilities

`class` · `python`

```python
FixtureCapabilities(
    api_version: tuple[int, int],
    targets: tuple[QualifiedFixtureTarget, ...],
    engines: tuple[EngineDTypeCapability, ...],
    readback_budget: int,
) -> None
```

`dataclass`

Targets, engine and dtype status, and limits of this fixture API.

**API tier:** `experimental`

Source: `src/helia_profiler/_fixture_facts.py:49`

### helia_profiler.FixtureCapabilities.api_version

`attribute` · `python`

```python
api_version: tuple[int, int]
```

Source: `src/helia_profiler/_fixture_facts.py:53`

### helia_profiler.FixtureCapabilities.targets

`attribute` · `python`

```python
targets: tuple[QualifiedFixtureTarget, ...]
```

Source: `src/helia_profiler/_fixture_facts.py:54`

### helia_profiler.FixtureCapabilities.engines

`attribute` · `python`

```python
engines: tuple[EngineDTypeCapability, ...]
```

Source: `src/helia_profiler/_fixture_facts.py:55`

### helia_profiler.FixtureCapabilities.readback_budget

`attribute` · `python`

```python
readback_budget: int
```

Source: `src/helia_profiler/_fixture_facts.py:56`

## helia_profiler.PerAxisQuantization

`class` · `python`

```python
PerAxisQuantization(axis: int, scales: tuple[float, ...], zero_points: tuple[int, ...]) -> None
```

`dataclass`

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_analysis.py:53`

### helia_profiler.PerAxisQuantization.axis

`attribute` · `python`

```python
axis: int
```

Source: `src/helia_profiler/fixture_analysis.py:55`

### helia_profiler.PerAxisQuantization.scales

`attribute` · `python`

```python
scales: tuple[float, ...]
```

Source: `src/helia_profiler/fixture_analysis.py:56`

### helia_profiler.PerAxisQuantization.zero_points

`attribute` · `python`

```python
zero_points: tuple[int, ...]
```

Source: `src/helia_profiler/fixture_analysis.py:57`

## helia_profiler.analyze_run_model_for_engine

`function` · `python`

```python
analyze_run_model_for_engine(path: str | Path, engine: EngineType | str) -> TypedFixtureModelAnalysis
```

Read a model's public inputs and outputs as *engine*'s fixture integration admits them.

**API tier:** `experimental`

**Parameters**

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| path | str \| Path | Required | The ``.tflite`` model. |
| engine | EngineType \| str | Required | The engine the run builds with. |

**Returns**

| Name | Type | Description |
| --- | --- | --- |
|  | TypedFixtureModelAnalysis | The same analysis as :func:`analyze_run_model`, after the engine has |
|  | TypedFixtureModelAnalysis | admitted the model. |

**Raises**

| Name | Description |
| --- | --- |
| ConfigError | *engine* is not an engine name, the model is unreadable, or the engine does not admit the model. |

Source: `src/helia_profiler/run_inputs.py:56`

## helia_profiler.FixtureStatePolicy

`class` · `python`

```python
FixtureStatePolicy()
```

Internal state behavior; input restoration is independent of this policy.

**API tier:** `experimental`

Source: `src/helia_profiler/_fixture_build.py:57`

### helia_profiler.FixtureStatePolicy.CARRY

`constant` · `python`

```python
CARRY = 'carry'
```

Source: `src/helia_profiler/_fixture_build.py:60`

## helia_profiler.FixtureTensor

`class` · `python`

```python
FixtureTensor(
    name: str,
    index: int,
    dtype: str,
    shape: tuple[int, ...],
    quantization: PerTensorQuantization | PerAxisQuantization | None,
) -> None
```

`dataclass`

One named model input or output with its dtype, shape and quantization.

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_analysis.py:60`

### helia_profiler.FixtureTensor.name

`attribute` · `python`

```python
name: str
```

Source: `src/helia_profiler/fixture_analysis.py:64`

### helia_profiler.FixtureTensor.index

`attribute` · `python`

```python
index: int
```

Source: `src/helia_profiler/fixture_analysis.py:65`

### helia_profiler.FixtureTensor.dtype

`attribute` · `python`

```python
dtype: str
```

Source: `src/helia_profiler/fixture_analysis.py:66`

### helia_profiler.FixtureTensor.shape

`attribute` · `python`

```python
shape: tuple[int, ...]
```

Source: `src/helia_profiler/fixture_analysis.py:67`

### helia_profiler.FixtureTensor.quantization

`attribute` · `python`

```python
quantization: PerTensorQuantization | PerAxisQuantization | None
```

Source: `src/helia_profiler/fixture_analysis.py:68`

### helia_profiler.FixtureTensor.size_bytes

`attribute` · `python`

```python
size_bytes: int
```

Source: `src/helia_profiler/fixture_analysis.py:102`

## helia_profiler.RuntimeProvider

`class` · `python`

```python
RuntimeProvider(name: str, url: str, revision: str) -> None
```

`dataclass`

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_runtime.py:61`

### helia_profiler.RuntimeProvider.name

`attribute` · `python`

```python
name: str
```

Source: `src/helia_profiler/fixture_runtime.py:63`

### helia_profiler.RuntimeProvider.url

`attribute` · `python`

```python
url: str
```

Source: `src/helia_profiler/fixture_runtime.py:64`

### helia_profiler.RuntimeProvider.revision

`attribute` · `python`

```python
revision: str
```

Source: `src/helia_profiler/fixture_runtime.py:65`

## helia_profiler.FixtureRole

`class` · `python`

```python
FixtureRole()
```

Role of one fixture tensor; outputs are always ``SIGNAL``.

**API tier:** `experimental`

Source: `src/helia_profiler/_fixture_build.py:63`

### helia_profiler.FixtureRole.SIGNAL

`constant` · `python`

```python
SIGNAL = 'signal'
```

Source: `src/helia_profiler/_fixture_build.py:66`

### helia_profiler.FixtureRole.AUX

`constant` · `python`

```python
AUX = 'aux'
```

Source: `src/helia_profiler/_fixture_build.py:67`

## helia_profiler.RuntimeABI

`class` · `python`

```python
RuntimeABI(toolchain: str, cpu: str, float_abi: str, short_enums: bool) -> None
```

`dataclass`

Manifest-declared ABI; archive members are not independently inspected.

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_runtime.py:68`

### helia_profiler.RuntimeABI.toolchain

`attribute` · `python`

```python
toolchain: str
```

Source: `src/helia_profiler/fixture_runtime.py:72`

### helia_profiler.RuntimeABI.cpu

`attribute` · `python`

```python
cpu: str
```

Source: `src/helia_profiler/fixture_runtime.py:73`

### helia_profiler.RuntimeABI.float_abi

`attribute` · `python`

```python
float_abi: str
```

Source: `src/helia_profiler/fixture_runtime.py:74`

### helia_profiler.RuntimeABI.short_enums

`attribute` · `python`

```python
short_enums: bool
```

Source: `src/helia_profiler/fixture_runtime.py:75`

## helia_profiler.FixtureDType

`class` · `python`

```python
FixtureDType()
```

IO tensor element types a fixture can declare.

**API tier:** `experimental`

Source: `src/helia_profiler/_fixture_build.py:70`

### helia_profiler.FixtureDType.INT8

`constant` · `python`

```python
INT8 = 'int8'
```

Source: `src/helia_profiler/_fixture_build.py:73`

### helia_profiler.FixtureDType.INT16

`constant` · `python`

```python
INT16 = 'int16'
```

Source: `src/helia_profiler/_fixture_build.py:74`

### helia_profiler.FixtureDType.INT32

`constant` · `python`

```python
INT32 = 'int32'
```

Source: `src/helia_profiler/_fixture_build.py:75`

### helia_profiler.FixtureDType.FLOAT16

`constant` · `python`

```python
FLOAT16 = 'float16'
```

Source: `src/helia_profiler/_fixture_build.py:76`

### helia_profiler.FixtureDType.FLOAT32

`constant` · `python`

```python
FLOAT32 = 'float32'
```

Source: `src/helia_profiler/_fixture_build.py:77`

## helia_profiler.RuntimeHeader

`class` · `python`

```python
RuntimeHeader(name: str, sha256: str) -> None
```

`dataclass`

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_runtime.py:78`

### helia_profiler.RuntimeHeader.name

`attribute` · `python`

```python
name: str
```

Source: `src/helia_profiler/fixture_runtime.py:80`

### helia_profiler.RuntimeHeader.sha256

`attribute` · `python`

```python
sha256: str
```

Source: `src/helia_profiler/fixture_runtime.py:81`

## helia_profiler.FixtureMethod

`class` · `python`

```python
FixtureMethod(
    timing_scope: FixtureTimingScope,
    state_policy: FixtureStatePolicy | None = None,
    power_sync: FixturePowerSync | None = None,
) -> None
```

`dataclass`

Select input-restoration timing and, optionally, the internal-state policy.

Carry initializes the engine once, then retains internal state through
warmups and measurements. It is how fixtures always run, so a build of a
model with internal state records it when no policy is given. Fixed external
inputs are still restored each invocation; no internal reset or
source-initializer equivalence is implied.

**API tier:** `experimental`

Source: `src/helia_profiler/_fixture_build.py:87`

### helia_profiler.FixtureMethod.timing_scope

`attribute` · `python`

```python
timing_scope: FixtureTimingScope
```

Source: `src/helia_profiler/_fixture_build.py:98`

### helia_profiler.FixtureMethod.state_policy

`attribute` · `python`

```python
state_policy: FixtureStatePolicy | None = None
```

Source: `src/helia_profiler/_fixture_build.py:99`

### helia_profiler.FixtureMethod.power_sync

`attribute` · `python`

```python
power_sync: FixturePowerSync | None = None
```

Source: `src/helia_profiler/_fixture_build.py:100`

## helia_profiler.PreparedRuntimeManifest

`class` · `python`

```python
PreparedRuntimeManifest(
    schema_version: int,
    archive_sha256: str,
    providers: tuple[RuntimeProvider, ...],
    abi: RuntimeABI,
    headers: tuple[RuntimeHeader, ...],
    include_dirs: tuple[str, ...],
    stack: str = 'upstream',
    consumer_defines: tuple[str, ...] = _UPSTREAM_DEFINES,
    kernel_dir: str | None = None,
) -> None
```

`dataclass`

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_runtime.py:110`

### helia_profiler.PreparedRuntimeManifest.schema_version

`attribute` · `python`

```python
schema_version: int
```

Source: `src/helia_profiler/fixture_runtime.py:112`

### helia_profiler.PreparedRuntimeManifest.archive_sha256

`attribute` · `python`

```python
archive_sha256: str
```

Source: `src/helia_profiler/fixture_runtime.py:113`

### helia_profiler.PreparedRuntimeManifest.providers

`attribute` · `python`

```python
providers: tuple[RuntimeProvider, ...]
```

Source: `src/helia_profiler/fixture_runtime.py:114`

### helia_profiler.PreparedRuntimeManifest.abi

`attribute` · `python`

```python
abi: RuntimeABI
```

Source: `src/helia_profiler/fixture_runtime.py:115`

### helia_profiler.PreparedRuntimeManifest.headers

`attribute` · `python`

```python
headers: tuple[RuntimeHeader, ...]
```

Source: `src/helia_profiler/fixture_runtime.py:116`

### helia_profiler.PreparedRuntimeManifest.include_dirs

`attribute` · `python`

```python
include_dirs: tuple[str, ...]
```

Source: `src/helia_profiler/fixture_runtime.py:117`

### helia_profiler.PreparedRuntimeManifest.stack

`attribute` · `python`

```python
stack: str = 'upstream'
```

Source: `src/helia_profiler/fixture_runtime.py:118`

### helia_profiler.PreparedRuntimeManifest.consumer_defines

`attribute` · `python`

```python
consumer_defines: tuple[str, ...] = _UPSTREAM_DEFINES
```

Source: `src/helia_profiler/fixture_runtime.py:120`

### helia_profiler.PreparedRuntimeManifest.kernel_dir

`attribute` · `python`

```python
kernel_dir: str | None = None
```

Source: `src/helia_profiler/fixture_runtime.py:121`

## helia_profiler.TypedFixtureModelAnalysis

`class` · `python`

```python
TypedFixtureModelAnalysis(
    inputs: tuple[FixtureTensor, ...],
    outputs: tuple[FixtureTensor, ...],
    has_float16: bool,
    analysis: ModelAnalysis,
    resolver: ResolverPlan,
    has_internal_state: bool = False,
    has_initializer: bool = False,
    subgraph_count: int = 1,
    resource_variable_count: int = 0,
) -> None
```

`dataclass`

A model's public inputs and outputs in model order, with its graph facts.

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_analysis.py:127`

### helia_profiler.TypedFixtureModelAnalysis.inputs

`attribute` · `python`

```python
inputs: tuple[FixtureTensor, ...]
```

Source: `src/helia_profiler/fixture_analysis.py:131`

### helia_profiler.TypedFixtureModelAnalysis.outputs

`attribute` · `python`

```python
outputs: tuple[FixtureTensor, ...]
```

Source: `src/helia_profiler/fixture_analysis.py:132`

### helia_profiler.TypedFixtureModelAnalysis.has_float16

`attribute` · `python`

```python
has_float16: bool
```

Source: `src/helia_profiler/fixture_analysis.py:134`

### helia_profiler.TypedFixtureModelAnalysis.analysis

`attribute` · `python`

```python
analysis: ModelAnalysis
```

Source: `src/helia_profiler/fixture_analysis.py:135`

### helia_profiler.TypedFixtureModelAnalysis.resolver

`attribute` · `python`

```python
resolver: ResolverPlan
```

Source: `src/helia_profiler/fixture_analysis.py:136`

### helia_profiler.TypedFixtureModelAnalysis.has_internal_state

`attribute` · `python`

```python
has_internal_state: bool = False
```

Source: `src/helia_profiler/fixture_analysis.py:137`

### helia_profiler.TypedFixtureModelAnalysis.has_initializer

`attribute` · `python`

```python
has_initializer: bool = False
```

Source: `src/helia_profiler/fixture_analysis.py:138`

### helia_profiler.TypedFixtureModelAnalysis.subgraph_count

`attribute` · `python`

```python
subgraph_count: int = 1
```

Source: `src/helia_profiler/fixture_analysis.py:139`

### helia_profiler.TypedFixtureModelAnalysis.resource_variable_count

`attribute` · `python`

```python
resource_variable_count: int = 0
```

Source: `src/helia_profiler/fixture_analysis.py:140`

### helia_profiler.TypedFixtureModelAnalysis.input_bytes

`attribute` · `python`

```python
input_bytes: int
```

Source: `src/helia_profiler/fixture_analysis.py:143`

### helia_profiler.TypedFixtureModelAnalysis.output_bytes

`attribute` · `python`

```python
output_bytes: int
```

Source: `src/helia_profiler/fixture_analysis.py:147`

## helia_profiler.FixtureCapability

`class` · `python`

```python
FixtureCapability()
```

Producer-declared status of one engine and IO dtype for fixed fixtures.

**API tier:** `experimental`

Source: `src/helia_profiler/_fixture_build.py:150`

### helia_profiler.FixtureCapability.QUALIFIED

`constant` · `python`

```python
QUALIFIED = 'qualified'
```

Source: `src/helia_profiler/_fixture_build.py:153`

### helia_profiler.FixtureCapability.SUPPORTED

`constant` · `python`

```python
SUPPORTED = 'supported'
```

Source: `src/helia_profiler/_fixture_build.py:154`

### helia_profiler.FixtureCapability.UNSUPPORTED

`constant` · `python`

```python
UNSUPPORTED = 'unsupported'
```

Source: `src/helia_profiler/_fixture_build.py:155`

## helia_profiler.VerifiedPreparedRuntime

`class` · `python`

```python
VerifiedPreparedRuntime(archive: FixtureFile, header_root: Path, manifest: FixtureFile, record: RuntimeManifest) -> None
```

`dataclass`

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_runtime.py:174`

### helia_profiler.VerifiedPreparedRuntime.archive

`attribute` · `python`

```python
archive: FixtureFile
```

Source: `src/helia_profiler/fixture_runtime.py:176`

### helia_profiler.VerifiedPreparedRuntime.header_root

`attribute` · `python`

```python
header_root: Path
```

Source: `src/helia_profiler/fixture_runtime.py:177`

### helia_profiler.VerifiedPreparedRuntime.manifest

`attribute` · `python`

```python
manifest: FixtureFile
```

Source: `src/helia_profiler/fixture_runtime.py:178`

### helia_profiler.VerifiedPreparedRuntime.record

`attribute` · `python`

```python
record: RuntimeManifest
```

Source: `src/helia_profiler/fixture_runtime.py:179`

### helia_profiler.VerifiedPreparedRuntime.header_path

`method` · `python`

```python
header_path(name: str) -> Path
```

Source: `src/helia_profiler/fixture_runtime.py:181`

## helia_profiler.FixtureIO

`class` · `python`

```python
FixtureIO(tensor: FixtureTensor, data: FixtureFile, role: FixtureRole = FixtureRole.SIGNAL) -> None
```

`dataclass`

One model input with its fixed bytes, or one output with its expected bytes.

**API tier:** `experimental`

Source: `src/helia_profiler/_fixture_build.py:185`

### helia_profiler.FixtureIO.tensor

`attribute` · `python`

```python
tensor: FixtureTensor
```

Source: `src/helia_profiler/_fixture_build.py:189`

### helia_profiler.FixtureIO.data

`attribute` · `python`

```python
data: FixtureFile
```

Source: `src/helia_profiler/_fixture_build.py:190`

### helia_profiler.FixtureIO.role

`attribute` · `python`

```python
role: FixtureRole = FixtureRole.SIGNAL
```

Source: `src/helia_profiler/_fixture_build.py:191`

## helia_profiler.PreparedUpstreamRuntime

`class` · `python`

```python
PreparedUpstreamRuntime(archive: FixtureFile, header_root: Path, manifest: FixtureFile) -> None
```

`dataclass`

Explicit prebuilt provider with a pinned, vendored header closure.

Manifest schema 1 declares the upstream TFLM + CMSIS-NN stack; schema 2
declares its ``stack`` (``helia-rt``) and the consumer defines and kernel
directory it was built with.

**API tier:** `experimental`

Source: `src/helia_profiler/fixture_runtime.py:185`

### helia_profiler.PreparedUpstreamRuntime.archive

`attribute` · `python`

```python
archive: FixtureFile
```

Source: `src/helia_profiler/fixture_runtime.py:194`

### helia_profiler.PreparedUpstreamRuntime.header_root

`attribute` · `python`

```python
header_root: Path
```

Source: `src/helia_profiler/fixture_runtime.py:195`

### helia_profiler.PreparedUpstreamRuntime.manifest

`attribute` · `python`

```python
manifest: FixtureFile
```

Source: `src/helia_profiler/fixture_runtime.py:196`

### helia_profiler.PreparedUpstreamRuntime.verify

`method` · `python`

```python
verify() -> VerifiedPreparedRuntime
```

Source: `src/helia_profiler/fixture_runtime.py:198`

## helia_profiler.FixtureOutput

`class` · `python`

```python
FixtureOutput(tensor: FixtureTensor) -> None
```

`dataclass`

One complete output declaration, without a reference answer.

**API tier:** `experimental`

Source: `src/helia_profiler/_fixture_build.py:194`

### helia_profiler.FixtureOutput.tensor

`attribute` · `python`

```python
tensor: FixtureTensor
```

Source: `src/helia_profiler/_fixture_build.py:198`

## helia_profiler.TypedFixture

`class` · `python`

```python
TypedFixture(
    model: FixtureFile,
    inputs: tuple[FixtureIO, ...],
    outputs: tuple[FixtureIO, ...] | tuple[FixtureOutput, ...],
) -> None
```

`dataclass`

Fixed input bytes and complete ordered outputs for a model.

Outputs are descriptors, or legacy ``FixtureIO`` references. Reference
bytes are not needed for measurement; legacy identities remain unchanged.

**API tier:** `experimental`

Source: `src/helia_profiler/_fixture_build.py:201`

### helia_profiler.TypedFixture.model

`attribute` · `python`

```python
model: FixtureFile
```

Source: `src/helia_profiler/_fixture_build.py:209`

### helia_profiler.TypedFixture.inputs

`attribute` · `python`

```python
inputs: tuple[FixtureIO, ...]
```

Source: `src/helia_profiler/_fixture_build.py:210`

### helia_profiler.TypedFixture.outputs

`attribute` · `python`

```python
outputs: tuple[FixtureIO, ...] | tuple[FixtureOutput, ...]
```

Source: `src/helia_profiler/_fixture_build.py:211`

### helia_profiler.TypedFixture.expected

`attribute` · `python`

```python
expected: FixtureFile | None
```

Source: `src/helia_profiler/_fixture_build.py:231`

### helia_profiler.TypedFixture.identity

`attribute` · `python`

```python
identity: str
```

Source: `src/helia_profiler/_fixture_build.py:236`

### helia_profiler.TypedFixture.verify

`method` · `python`

```python
verify() -> None
```

Source: `src/helia_profiler/_fixture_build.py:213`
