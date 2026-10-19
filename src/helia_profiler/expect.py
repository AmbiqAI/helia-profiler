"""Expected outputs: compare a run's output tensors with reference bytes.

:class:`Expect` names where the reference comes from: a host LiteRT run of
the same model on the same inputs, or caller files. :func:`compare_outputs`
returns facts for each tensor (hashes, mismatched elements, largest error in
stored and real units) and a verdict only when a :class:`Tolerance` is given;
numerical acceptance policy stays with the caller.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import numpy as np

from .errors import ConfigError
from .fixture_analysis import FixtureTensor, PerAxisQuantization, PerTensorQuantization
from .fixture_runtime import FixtureFile

if TYPE_CHECKING:
    from ._fixture_build import FixtureBuild, TypedFixture
    from .fixture_capture import FixtureCaptureResult

#: LiteRT op resolvers a host reference can run with.
LiteRtResolver = Literal["BUILTIN_REF", "BUILTIN", "BUILTIN_WITHOUT_DEFAULT_DELEGATES"]

_NUMPY_DTYPES = {
    "int8": np.dtype("<i1"),
    "int16": np.dtype("<i2"),
    "int32": np.dtype("<i4"),
    "float16": np.dtype("<f2"),
    "float32": np.dtype("<f4"),
}


@dataclass(frozen=True)
class Tolerance:
    """Largest accepted absolute error per element, in each tensor's stored units.

    Integer tensors count quantization steps; float tensors count values.
    """

    max_abs_error: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_abs_error) or self.max_abs_error < 0:
            raise ValueError("Tolerance requires a finite, nonnegative max_abs_error")


@dataclass(frozen=True)
class ReferenceOrigin:
    """Where reference bytes came from.

    ``kind`` is ``"host-litert"`` or ``"files"``. A host reference records the
    LiteRT package version, its op resolver and how many times the model was
    invoked on the inputs, matching the firmware when state carries.
    """

    kind: str
    litert_version: str | None = None
    resolver: str | None = None
    invocations: int | None = None


@dataclass(frozen=True)
class Reference:
    """Reference bytes for every output, in model order, and their origin."""

    origin: ReferenceOrigin
    outputs: tuple[bytes, ...]


@dataclass(frozen=True)
class Expect:
    """What a run's outputs are compared against. Build with :meth:`reference` or :meth:`from_files`."""

    source: Literal["host-litert", "files"]
    files: tuple[FixtureFile | Path, ...] = ()
    resolver: LiteRtResolver = "BUILTIN_REF"
    tolerance: Tolerance | None = None

    @classmethod
    def reference(
        cls, *, resolver: LiteRtResolver = "BUILTIN_REF", tolerance: Tolerance | None = None
    ) -> Expect:
        """Run the model with host LiteRT on the run's inputs (LiteRT models only)."""
        return cls("host-litert", resolver=resolver, tolerance=tolerance)

    @classmethod
    def from_files(
        cls, *files: FixtureFile | str | Path, tolerance: Tolerance | None = None
    ) -> Expect:
        """Compare with caller bytes, one file per output in model order."""
        if not files:
            raise ValueError("Expect.from_files requires one file per output")
        resolved = tuple(item if isinstance(item, FixtureFile) else Path(item) for item in files)
        return cls("files", files=resolved, tolerance=tolerance)


@dataclass(frozen=True)
class TensorComparison:
    """Comparison facts for one output tensor.

    ``max_abs_error`` is in stored units; ``max_abs_error_real`` is the same
    error after dequantization, ``None`` for float tensors. A NaN on one side
    only counts as a mismatch with infinite error. ``within_tolerance`` is
    ``None`` when no tolerance was given.
    """

    name: str
    index: int
    dtype: str
    shape: tuple[int, ...]
    reference_sha256: str
    observed_sha256: str
    elements: int
    mismatched_elements: int
    max_abs_error: float
    max_abs_error_real: float | None
    within_tolerance: bool | None

    @property
    def identical(self) -> bool:
        return self.reference_sha256 == self.observed_sha256


@dataclass(frozen=True)
class Verification:
    """Per-tensor comparison facts, the reference they used and an optional verdict."""

    reference: Reference
    tensors: tuple[TensorComparison, ...]
    tolerance: Tolerance | None

    @property
    def matched(self) -> bool | None:
        """Whether every tensor is within tolerance; ``None`` when no tolerance was given."""
        if self.tolerance is None:
            return None
        return all(tensor.within_tolerance for tensor in self.tensors)


def host_reference(
    model: bytes,
    inputs: Sequence[tuple[FixtureTensor, bytes]],
    outputs: Sequence[FixtureTensor],
    *,
    resolver: LiteRtResolver = "BUILTIN_REF",
    invocations: int = 1,
) -> Reference:
    """Run a LiteRT model on the host and return its output bytes in *outputs* order.

    Inputs are restored before every invocation, as the fixture firmware does,
    so internal state carries across *invocations* while inputs do not.
    """
    try:
        from ai_edge_litert import interpreter as litert
    except ImportError as exc:
        raise ConfigError(
            "A host reference needs the LiteRT interpreter",
            hint="Install helia-profiler with the 'analysis' extra, or use Expect.from_files().",
        ) from exc
    if invocations < 1:
        raise ValueError("A host reference needs at least one invocation")
    interpreter = litert.Interpreter(
        model_content=model,
        experimental_op_resolver_type=litert.OpResolverType[resolver],
    )
    interpreter.allocate_tensors()
    known = {detail["index"] for detail in interpreter.get_input_details()}
    known |= {detail["index"] for detail in interpreter.get_output_details()}
    missing = [t.name for t, _ in inputs if t.index not in known]
    missing += [t.name for t in outputs if t.index not in known]
    if missing:
        raise ConfigError(f"Host LiteRT model has no IO tensor for: {', '.join(missing)}")
    arrays = [
        (tensor.index, np.frombuffer(data, dtype=_NUMPY_DTYPES[tensor.dtype]).reshape(tensor.shape))
        for tensor, data in inputs
    ]
    for _ in range(invocations):
        for index, array in arrays:
            interpreter.set_tensor(index, array)
        interpreter.invoke()
    return Reference(
        ReferenceOrigin(
            "host-litert",
            litert_version=importlib.metadata.version("ai-edge-litert"),
            resolver=resolver,
            invocations=invocations,
        ),
        tuple(
            np.ascontiguousarray(interpreter.get_tensor(tensor.index)).tobytes()
            for tensor in outputs
        ),
    )


def compare_outputs(
    tensors: Sequence[FixtureTensor],
    reference: Reference,
    observed: Sequence[bytes],
    tolerance: Tolerance | None = None,
) -> Verification:
    """Compare observed output bytes with a reference, tensor by tensor."""
    if not len(tensors) == len(reference.outputs) == len(observed):
        raise ConfigError(
            f"{len(tensors)} output tensors, {len(reference.outputs)} reference outputs "
            f"and {len(observed)} observed outputs"
        )
    return Verification(
        reference,
        tuple(
            _compare(tensor, expected, actual, tolerance)
            for tensor, expected, actual in zip(tensors, reference.outputs, observed, strict=True)
        ),
        tolerance,
    )


def verify_fixture_outputs(
    fixture: TypedFixture,
    build: FixtureBuild,
    capture: FixtureCaptureResult,
    expect: Expect,
) -> Verification:
    """Compare a fixture capture's outputs with *expect*.

    A host reference runs the fixture's model on its pinned input bytes, as
    many times as the firmware invoked it when internal state carries.
    """
    from .engines import EngineType

    tensors = tuple(io.tensor for io in fixture.outputs)
    if len(capture.outputs) != len(tensors):
        raise ConfigError(
            f"Capture has {len(capture.outputs)} outputs; the fixture declares {len(tensors)}",
            hint="Compare only a capture that completed and read back every output.",
        )
    observed = tuple(item.read() for item in capture.outputs)
    if expect.source == "files":
        reference = Reference(ReferenceOrigin("files"), tuple(_read(f) for f in expect.files))
    elif build.engine is EngineType.EXECUTORCH:
        raise ConfigError(
            "A host LiteRT reference cannot run an ExecuTorch model",
            hint="Use Expect.from_files() with reference outputs.",
        )
    else:
        reference = host_reference(
            fixture.model.read(),
            [(io.tensor, io.data.read()) for io in fixture.inputs],
            tensors,
            resolver=expect.resolver,
            invocations=build.warmups + build.iterations if build.state_policy is not None else 1,
        )
    return compare_outputs(tensors, reference, observed, expect.tolerance)


def _read(item: FixtureFile | Path) -> bytes:
    if isinstance(item, FixtureFile):
        return item.read()
    try:
        return item.read_bytes()
    except OSError as exc:
        raise ConfigError(f"Cannot read reference output {item}: {exc}") from exc


def _compare(
    tensor: FixtureTensor, expected: bytes, actual: bytes, tolerance: Tolerance | None
) -> TensorComparison:
    for label, data in (("reference", expected), ("observed", actual)):
        if len(data) != tensor.size_bytes:
            raise ConfigError(
                f"Output {tensor.name}: {label} has {len(data)} B, the tensor {tensor.size_bytes} B"
            )
    dtype = _NUMPY_DTYPES[tensor.dtype]
    ref = np.frombuffer(expected, dtype=dtype).reshape(tensor.shape)
    obs = np.frombuffer(actual, dtype=dtype).reshape(tensor.shape)
    if dtype.kind == "f":
        ref64, obs64 = ref.astype(np.float64), obs.astype(np.float64)
        both_nan = np.isnan(ref64) & np.isnan(obs64)
        error = np.where(both_nan, 0.0, np.abs(ref64 - obs64))
        error = np.where(np.isnan(error), np.inf, error)
    else:
        error = np.abs(ref.astype(np.int64) - obs.astype(np.int64)).astype(np.float64)
    worst = float(error.max(initial=0.0))
    return TensorComparison(
        name=tensor.name,
        index=tensor.index,
        dtype=tensor.dtype,
        shape=tensor.shape,
        reference_sha256=hashlib.sha256(expected).hexdigest(),
        observed_sha256=hashlib.sha256(actual).hexdigest(),
        elements=int(error.size),
        mismatched_elements=int(np.count_nonzero(error)),
        max_abs_error=worst,
        max_abs_error_real=_real_error(tensor, error),
        within_tolerance=None if tolerance is None else worst <= tolerance.max_abs_error,
    )


def _real_error(tensor: FixtureTensor, error: np.ndarray) -> float | None:
    quant = tensor.quantization
    if isinstance(quant, PerTensorQuantization):
        return float((error * quant.scale).max(initial=0.0))
    if isinstance(quant, PerAxisQuantization):
        shape = [1] * error.ndim
        shape[quant.axis] = len(quant.scales)
        scales = np.asarray(quant.scales, dtype=np.float64).reshape(shape)
        return float((error * scales).max(initial=0.0))
    return None
