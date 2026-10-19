# helia_profiler.expect

Checking a run's outputs: reference outputs from host LiteRT or caller files, compared tensor by tensor, with a verdict only when a tolerance is given.

Every name on this page is imported from `helia_profiler`.

**API tier:** `experimental`

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__`.

## helia_profiler.Tolerance

`class` · `python`

```python
Tolerance(max_abs_error: float = 0.0) -> None
```

`dataclass`

Largest accepted absolute error per element, in each tensor's stored units.

Integer tensors count quantization steps; float tensors count values.

**API tier:** `experimental`

Source: `src/helia_profiler/expect.py:42`

### helia_profiler.Tolerance.max_abs_error

`attribute` · `python`

```python
max_abs_error: float = 0.0
```

Source: `src/helia_profiler/expect.py:49`

## helia_profiler.ReferenceOrigin

`class` · `python`

```python
ReferenceOrigin(
    kind: str,
    litert_version: str | None = None,
    resolver: str | None = None,
    invocations: int | None = None,
) -> None
```

`dataclass`

Where reference bytes came from.

``kind`` is ``"host-litert"`` or ``"files"``. A host reference records the
LiteRT package version, its op resolver and how many times the model was
invoked on the inputs, matching the firmware when state carries.

**API tier:** `experimental`

Source: `src/helia_profiler/expect.py:56`

### helia_profiler.ReferenceOrigin.kind

`attribute` · `python`

```python
kind: str
```

Source: `src/helia_profiler/expect.py:65`

### helia_profiler.ReferenceOrigin.litert_version

`attribute` · `python`

```python
litert_version: str | None = None
```

Source: `src/helia_profiler/expect.py:66`

### helia_profiler.ReferenceOrigin.resolver

`attribute` · `python`

```python
resolver: str | None = None
```

Source: `src/helia_profiler/expect.py:67`

### helia_profiler.ReferenceOrigin.invocations

`attribute` · `python`

```python
invocations: int | None = None
```

Source: `src/helia_profiler/expect.py:68`

## helia_profiler.Reference

`class` · `python`

```python
Reference(origin: ReferenceOrigin, outputs: tuple[bytes, ...]) -> None
```

`dataclass`

Reference bytes for every output, in model order, and their origin.

**API tier:** `experimental`

Source: `src/helia_profiler/expect.py:71`

### helia_profiler.Reference.origin

`attribute` · `python`

```python
origin: ReferenceOrigin
```

Source: `src/helia_profiler/expect.py:75`

### helia_profiler.Reference.outputs

`attribute` · `python`

```python
outputs: tuple[bytes, ...]
```

Source: `src/helia_profiler/expect.py:76`

## helia_profiler.Expect

`class` · `python`

```python
Expect(
    source: Literal['host-litert', 'files'],
    files: tuple[FixtureFile | Path, ...] = (),
    resolver: LiteRtResolver = 'BUILTIN_REF',
    tolerance: Tolerance | None = None,
) -> None
```

`dataclass`

What a run's outputs are compared against. Build with :meth:`reference` or :meth:`from_files`.

**API tier:** `experimental`

Source: `src/helia_profiler/expect.py:79`

### helia_profiler.Expect.source

`attribute` · `python`

```python
source: Literal['host-litert', 'files']
```

Source: `src/helia_profiler/expect.py:83`

### helia_profiler.Expect.files

`attribute` · `python`

```python
files: tuple[FixtureFile | Path, ...] = ()
```

Source: `src/helia_profiler/expect.py:84`

### helia_profiler.Expect.resolver

`attribute` · `python`

```python
resolver: LiteRtResolver = 'BUILTIN_REF'
```

Source: `src/helia_profiler/expect.py:85`

### helia_profiler.Expect.tolerance

`attribute` · `python`

```python
tolerance: Tolerance | None = None
```

Source: `src/helia_profiler/expect.py:86`

### helia_profiler.Expect.reference

`method` · `python`

```python
reference(*, resolver: LiteRtResolver = 'BUILTIN_REF', tolerance: Tolerance | None = None) -> Expect
```

`classmethod`

Run the model with host LiteRT on the run's inputs (LiteRT models only).

Source: `src/helia_profiler/expect.py:88`

### helia_profiler.Expect.from_files

`method` · `python`

```python
from_files(*files: FixtureFile | str | Path = (), tolerance: Tolerance | None = None) -> Expect
```

`classmethod`

Compare with caller bytes, one file per output in model order.

Source: `src/helia_profiler/expect.py:95`

## helia_profiler.TensorComparison

`class` · `python`

```python
TensorComparison(
    name: str,
    index: int,
    dtype: str,
    shape: tuple[int, ...],
    reference_sha256: str,
    observed_sha256: str,
    elements: int,
    mismatched_elements: int,
    max_abs_error: float,
    max_abs_error_real: float | None,
    within_tolerance: bool | None,
) -> None
```

`dataclass`

Comparison facts for one output tensor.

``max_abs_error`` is in stored units; ``max_abs_error_real`` is the same
error after dequantization, ``None`` for float tensors. A NaN on one side
only counts as a mismatch with infinite error. ``within_tolerance`` is
``None`` when no tolerance was given.

**API tier:** `experimental`

Source: `src/helia_profiler/expect.py:106`

### helia_profiler.TensorComparison.name

`attribute` · `python`

```python
name: str
```

Source: `src/helia_profiler/expect.py:116`

### helia_profiler.TensorComparison.index

`attribute` · `python`

```python
index: int
```

Source: `src/helia_profiler/expect.py:117`

### helia_profiler.TensorComparison.dtype

`attribute` · `python`

```python
dtype: str
```

Source: `src/helia_profiler/expect.py:118`

### helia_profiler.TensorComparison.shape

`attribute` · `python`

```python
shape: tuple[int, ...]
```

Source: `src/helia_profiler/expect.py:119`

### helia_profiler.TensorComparison.reference_sha256

`attribute` · `python`

```python
reference_sha256: str
```

Source: `src/helia_profiler/expect.py:120`

### helia_profiler.TensorComparison.observed_sha256

`attribute` · `python`

```python
observed_sha256: str
```

Source: `src/helia_profiler/expect.py:121`

### helia_profiler.TensorComparison.elements

`attribute` · `python`

```python
elements: int
```

Source: `src/helia_profiler/expect.py:122`

### helia_profiler.TensorComparison.mismatched_elements

`attribute` · `python`

```python
mismatched_elements: int
```

Source: `src/helia_profiler/expect.py:123`

### helia_profiler.TensorComparison.max_abs_error

`attribute` · `python`

```python
max_abs_error: float
```

Source: `src/helia_profiler/expect.py:124`

### helia_profiler.TensorComparison.max_abs_error_real

`attribute` · `python`

```python
max_abs_error_real: float | None
```

Source: `src/helia_profiler/expect.py:125`

### helia_profiler.TensorComparison.within_tolerance

`attribute` · `python`

```python
within_tolerance: bool | None
```

Source: `src/helia_profiler/expect.py:126`

### helia_profiler.TensorComparison.identical

`attribute` · `python`

```python
identical: bool
```

Source: `src/helia_profiler/expect.py:129`

## helia_profiler.Verification

`class` · `python`

```python
Verification(reference: Reference, tensors: tuple[TensorComparison, ...], tolerance: Tolerance | None) -> None
```

`dataclass`

Per-tensor comparison facts, the reference they used and an optional verdict.

**API tier:** `experimental`

Source: `src/helia_profiler/expect.py:133`

### helia_profiler.Verification.reference

`attribute` · `python`

```python
reference: Reference
```

Source: `src/helia_profiler/expect.py:137`

### helia_profiler.Verification.tensors

`attribute` · `python`

```python
tensors: tuple[TensorComparison, ...]
```

Source: `src/helia_profiler/expect.py:138`

### helia_profiler.Verification.tolerance

`attribute` · `python`

```python
tolerance: Tolerance | None
```

Source: `src/helia_profiler/expect.py:139`

### helia_profiler.Verification.matched

`attribute` · `python`

```python
matched: bool | None
```

Whether every tensor is within tolerance; ``None`` when no tolerance was given.

Source: `src/helia_profiler/expect.py:142`

## helia_profiler.host_reference

`function` · `python`

```python
host_reference(
    model: bytes,
    inputs: Sequence[tuple[FixtureTensor, bytes]],
    outputs: Sequence[FixtureTensor],
    *,
    resolver: LiteRtResolver = 'BUILTIN_REF',
    invocations: int = 1,
) -> Reference
```

Run a LiteRT model on the host and return its output bytes in *outputs* order.

Inputs are restored before every invocation, as the fixture firmware does,
so internal state carries across *invocations* while inputs do not.

**API tier:** `experimental`

Source: `src/helia_profiler/expect.py:149`

## helia_profiler.compare_outputs

`function` · `python`

```python
compare_outputs(
    tensors: Sequence[FixtureTensor],
    reference: Reference,
    observed: Sequence[bytes],
    tolerance: Tolerance | None = None,
) -> Verification
```

Compare observed output bytes with a reference, tensor by tensor.

**API tier:** `experimental`

Source: `src/helia_profiler/expect.py:204`

## helia_profiler.verify_fixture_outputs

`function` · `python`

```python
verify_fixture_outputs(
    fixture: TypedFixture,
    build: FixtureBuild,
    capture: FixtureCaptureResult,
    expect: Expect,
) -> Verification
```

Compare a fixture capture's outputs with *expect*.

A host reference runs the fixture's model on its pinned input bytes, as
many times as the firmware invoked it when internal state carries.

**API tier:** `experimental`

Source: `src/helia_profiler/expect.py:226`
