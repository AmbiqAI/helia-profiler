# helia_profiler.run_inputs

What a run is built for and captured under: the typed fixture that pins a model's input bytes and declares its outputs, the tensors' element types and quantization, how the run is timed, the prepared runtime a build links, the board guard a capture answers to, the model analysis that lists a model's inputs and outputs, and the targets and IO types this heliaPROFILER builds.

Every name on this page is imported from `helia_profiler`.

**API tier:** `experimental`

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__`.

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

## helia_profiler.FixtureDType

`class` · `python`

```python
FixtureDType()
```

IO tensor element types a fixture can declare.

**API tier:** `experimental`

Source: `src/helia_profiler/_fixture_io.py:15`

### helia_profiler.FixtureDType.INT8

`constant` · `python`

```python
INT8 = 'int8'
```

Source: `src/helia_profiler/_fixture_io.py:18`

### helia_profiler.FixtureDType.INT16

`constant` · `python`

```python
INT16 = 'int16'
```

Source: `src/helia_profiler/_fixture_io.py:19`

### helia_profiler.FixtureDType.INT32

`constant` · `python`

```python
INT32 = 'int32'
```

Source: `src/helia_profiler/_fixture_io.py:20`

### helia_profiler.FixtureDType.FLOAT16

`constant` · `python`

```python
FLOAT16 = 'float16'
```

Source: `src/helia_profiler/_fixture_io.py:21`

### helia_profiler.FixtureDType.FLOAT32

`constant` · `python`

```python
FLOAT32 = 'float32'
```

Source: `src/helia_profiler/_fixture_io.py:22`

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

Source: `src/helia_profiler/_fixture_build.py:60`

### helia_profiler.FixtureTimingScope.INVOKE_ONLY

`constant` · `python`

```python
INVOKE_ONLY = 'invoke_only'
```

Source: `src/helia_profiler/_fixture_build.py:68`

### helia_profiler.FixtureTimingScope.RESTORE_AND_INVOKE

`constant` · `python`

```python
RESTORE_AND_INVOKE = 'restore_and_invoke'
```

Source: `src/helia_profiler/_fixture_build.py:69`

## helia_profiler.FixtureStatePolicy

`class` · `python`

```python
FixtureStatePolicy()
```

Internal state behavior; input restoration is independent of this policy.

**API tier:** `experimental`

Source: `src/helia_profiler/_fixture_build.py:72`

### helia_profiler.FixtureStatePolicy.CARRY

`constant` · `python`

```python
CARRY = 'carry'
```

Source: `src/helia_profiler/_fixture_build.py:75`

## helia_profiler.FixtureRole

`class` · `python`

```python
FixtureRole()
```

Role of one fixture tensor; outputs are always ``SIGNAL``.

**API tier:** `experimental`

Source: `src/helia_profiler/_fixture_build.py:78`

### helia_profiler.FixtureRole.SIGNAL

`constant` · `python`

```python
SIGNAL = 'signal'
```

Source: `src/helia_profiler/_fixture_build.py:81`

### helia_profiler.FixtureRole.AUX

`constant` · `python`

```python
AUX = 'aux'
```

Source: `src/helia_profiler/_fixture_build.py:82`

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

Source: `src/helia_profiler/_fixture_build.py:85`

### helia_profiler.FixtureMethod.timing_scope

`attribute` · `python`

```python
timing_scope: FixtureTimingScope
```

Source: `src/helia_profiler/_fixture_build.py:96`

### helia_profiler.FixtureMethod.state_policy

`attribute` · `python`

```python
state_policy: FixtureStatePolicy | None = None
```

Source: `src/helia_profiler/_fixture_build.py:97`

### helia_profiler.FixtureMethod.power_sync

`attribute` · `python`

```python
power_sync: FixturePowerSync | None = None
```

Source: `src/helia_profiler/_fixture_build.py:98`

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

## helia_profiler.FixtureIO

`class` · `python`

```python
FixtureIO(tensor: FixtureTensor, data: FixtureFile, role: FixtureRole = FixtureRole.SIGNAL) -> None
```

`dataclass`

One model input with its fixed bytes, or one output with its expected bytes.

**API tier:** `experimental`

Source: `src/helia_profiler/_fixture_build.py:151`

### helia_profiler.FixtureIO.tensor

`attribute` · `python`

```python
tensor: FixtureTensor
```

Source: `src/helia_profiler/_fixture_build.py:155`

### helia_profiler.FixtureIO.data

`attribute` · `python`

```python
data: FixtureFile
```

Source: `src/helia_profiler/_fixture_build.py:156`

### helia_profiler.FixtureIO.role

`attribute` · `python`

```python
role: FixtureRole = FixtureRole.SIGNAL
```

Source: `src/helia_profiler/_fixture_build.py:157`

## helia_profiler.FixtureOutput

`class` · `python`

```python
FixtureOutput(tensor: FixtureTensor) -> None
```

`dataclass`

One complete output declaration, without a reference answer.

**API tier:** `experimental`

Source: `src/helia_profiler/_fixture_build.py:160`

### helia_profiler.FixtureOutput.tensor

`attribute` · `python`

```python
tensor: FixtureTensor
```

Source: `src/helia_profiler/_fixture_build.py:164`

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

Source: `src/helia_profiler/_fixture_build.py:167`

### helia_profiler.TypedFixture.model

`attribute` · `python`

```python
model: FixtureFile
```

Source: `src/helia_profiler/_fixture_build.py:175`

### helia_profiler.TypedFixture.inputs

`attribute` · `python`

```python
inputs: tuple[FixtureIO, ...]
```

Source: `src/helia_profiler/_fixture_build.py:176`

### helia_profiler.TypedFixture.outputs

`attribute` · `python`

```python
outputs: tuple[FixtureIO, ...] | tuple[FixtureOutput, ...]
```

Source: `src/helia_profiler/_fixture_build.py:177`

### helia_profiler.TypedFixture.expected

`attribute` · `python`

```python
expected: FixtureFile | None
```

Source: `src/helia_profiler/_fixture_build.py:197`

### helia_profiler.TypedFixture.identity

`attribute` · `python`

```python
identity: str
```

Source: `src/helia_profiler/_fixture_build.py:202`

### helia_profiler.TypedFixture.verify

`method` · `python`

```python
verify() -> None
```

Source: `src/helia_profiler/_fixture_build.py:179`
