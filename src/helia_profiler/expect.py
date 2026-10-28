"""Expected outputs: compare a run's output tensors with reference outputs.

:class:`Expect` names where the reference comes from: a host LiteRT run of
the same model on the same input bytes, the expected outputs a fixture build
carries, or caller files. :func:`compare_outputs` returns facts for each
tensor (hashes, mismatched elements, largest error in stored and real units)
and a verdict only when a :class:`Tolerance` is given; numerical acceptance
policy stays with the caller.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, get_args

import numpy as np

from ._files import FixtureFile
from .errors import ConfigError, EngineError
from .fixtures.analysis import FixtureTensor, PerAxisQuantization, PerTensorQuantization

if TYPE_CHECKING:
    from .fixtures.capture import FixtureCaptureResult
    from .fixtures.types import FixtureBuild

#: LiteRT op resolvers a host reference can run with.
LiteRtResolver = Literal["BUILTIN_REF", "BUILTIN", "BUILTIN_WITHOUT_DEFAULT_DELEGATES"]
ReferenceKind = Literal["host-litert", "build", "files"]

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

    Integer tensors count quantization steps; float tensors count values. One
    tolerance applies to every output tensor.
    """

    max_abs_error: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_abs_error) or self.max_abs_error < 0:
            raise ValueError("Tolerance requires a finite, nonnegative max_abs_error")


@dataclass(frozen=True)
class ReferenceOrigin:
    """Where reference outputs came from.

    A host reference records the LiteRT package version, its op resolver and
    how many times the model was invoked on the inputs.
    """

    kind: ReferenceKind
    litert_version: str | None = None
    resolver: str | None = None
    invocations: int | None = None


@dataclass(frozen=True)
class ReferenceOutputs:
    """Reference bytes for every output, in model order, and their origin."""

    origin: ReferenceOrigin
    outputs: tuple[bytes, ...]


@dataclass(frozen=True)
class Expect:
    """What a run's outputs are compared against.

    Build with :meth:`host_litert`, :meth:`from_build` or :meth:`from_files`.
    """

    source: ReferenceKind
    files: tuple[FixtureFile | Path, ...] = ()
    resolver: LiteRtResolver = "BUILTIN_REF"
    tolerance: Tolerance | None = None

    @classmethod
    def host_litert(
        cls, *, resolver: LiteRtResolver = "BUILTIN_REF", tolerance: Tolerance | None = None
    ) -> Expect:
        """Run the model with host LiteRT on the run's input bytes (LiteRT models only)."""
        _check_resolver(resolver)
        return cls("host-litert", resolver=resolver, tolerance=tolerance)

    @classmethod
    def from_build(cls, *, tolerance: Tolerance | None = None) -> Expect:
        """Use the expected outputs the fixture build carries."""
        return cls("build", tolerance=tolerance)

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
    error after dequantization, ``None`` for an unquantized tensor. A NaN on
    one side only counts as a mismatch with infinite error. ``within_tolerance``
    is ``None`` when no tolerance was given.
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

    reference: ReferenceOutputs
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
) -> ReferenceOutputs:
    """Run a LiteRT model on the host and return its output bytes in *outputs* order.

    *inputs* must cover every model input. They are restored before every
    invocation, as the fixture firmware does, so internal state carries across
    *invocations* while inputs do not.
    """
    _check_resolver(resolver)
    if invocations < 1:
        raise ConfigError("A host reference needs at least one invocation")
    try:
        from ai_edge_litert import interpreter as litert
    except ImportError as exc:
        raise ConfigError(
            "A host reference needs the LiteRT interpreter",
            hint="Install helia-profiler with the 'analysis' extra, or use Expect.from_files().",
        ) from exc
    try:
        interpreter = litert.Interpreter(
            model_content=model,
            experimental_op_resolver_type=litert.OpResolverType[resolver],
        )
        interpreter.allocate_tensors()
    except (RuntimeError, ValueError) as exc:
        raise EngineError(f"Host LiteRT cannot load the model: {exc}") from exc
    input_details = {d["index"]: d for d in interpreter.get_input_details()}
    output_details = {d["index"]: d for d in interpreter.get_output_details()}
    given = [tensor.index for tensor, _ in inputs]
    if sorted(given) != sorted(input_details):
        raise ConfigError(
            f"Host reference inputs {sorted(given)} do not match the model inputs "
            f"{sorted(input_details)}"
        )
    for tensor in outputs:
        if tensor.index not in output_details:
            raise ConfigError(f"{tensor.name} (tensor {tensor.index}) is not a model output")
    for tensor in (*(t for t, _ in inputs), *outputs):
        detail = input_details.get(tensor.index) or output_details[tensor.index]
        dtype, shape = np.dtype(detail["dtype"]).name, tuple(int(n) for n in detail["shape"])
        if (dtype, shape) != (tensor.dtype, tensor.shape):
            raise ConfigError(
                f"{tensor.name} is {tensor.dtype}{list(tensor.shape)}; "
                f"host LiteRT has {dtype}{list(shape)}"
            )
    arrays = [(tensor.index, _array(tensor, data, "input")) for tensor, data in inputs]
    try:
        for _ in range(invocations):
            for index, array in arrays:
                interpreter.set_tensor(index, array)
            interpreter.invoke()
        results = tuple(
            np.asarray(interpreter.get_tensor(t.index), dtype=_NUMPY_DTYPES[t.dtype]).tobytes()
            for t in outputs
        )
    except (RuntimeError, ValueError) as exc:
        raise EngineError(f"Host LiteRT invocation failed: {exc}") from exc
    return ReferenceOutputs(
        ReferenceOrigin(
            "host-litert",
            litert_version=importlib.metadata.version("ai-edge-litert"),
            resolver=resolver,
            invocations=invocations,
        ),
        results,
    )


def compare_outputs(
    tensors: Sequence[FixtureTensor],
    reference: ReferenceOutputs,
    observed: Sequence[bytes],
    tolerance: Tolerance | None = None,
) -> Verification:
    """Compare observed output bytes with reference outputs, tensor by tensor."""
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


def check_fixture_expectation(build: FixtureBuild, expect: Expect) -> None:
    """Refuse *expect* when it cannot be compared with *build*'s outputs.

    Run before a capture, so a missing or mis-sized reference costs no
    device time.
    """
    from .engines import EngineType

    tensors = build.output_tensors
    if build.model is None or not build.inputs or not tensors:
        raise ConfigError(
            "This fixture build does not record its measurement inputs and outputs",
            hint="Build the fixture again with this HPX.",
        )
    if expect.source == "host-litert":
        if build.engine is EngineType.EXECUTORCH:
            raise ConfigError(
                "A host LiteRT reference cannot run an ExecuTorch model",
                hint="Use Expect.from_build() or Expect.from_files() with reference outputs.",
            )
        if importlib.util.find_spec("ai_edge_litert") is None:
            raise ConfigError(
                "A host reference needs the LiteRT interpreter",
                hint="Install helia-profiler with the 'analysis' extra, or use Expect.from_files().",
            )
        return
    if expect.source == "files":
        references: Sequence[FixtureFile | Path] = expect.files
    elif expect.source == "build":
        if len(build.outputs) != len(tensors):
            raise ConfigError("This fixture build carries no expected outputs")
        references = build.outputs
    else:
        raise ConfigError(f"Unknown reference source {expect.source!r}")
    if len(references) != len(tensors):
        raise ConfigError(
            f"{len(tensors)} output tensors, {len(references)} reference outputs",
            hint="Give one reference file per output, in model order.",
        )
    for tensor, item in zip(tensors, references, strict=True):
        size = len(_read(item))
        if size != tensor.size_bytes:
            raise ConfigError(
                f"{tensor.name}: reference has {size} B, the tensor {tensor.size_bytes} B"
            )


def compare_fixture_outputs(
    build: FixtureBuild,
    capture: FixtureCaptureResult,
    expect: Expect,
) -> Verification:
    """Compare a fixture capture's outputs with *expect*.

    A host reference runs the build's model on its pinned input bytes as many
    times as the firmware invokes it (warmups, then timed iterations).
    """
    check_fixture_expectation(build, expect)
    tensors = build.output_tensors
    if capture.state != "success" or len(capture.outputs) != len(tensors):
        raise ConfigError(
            f"Capture {capture.state} with {len(capture.outputs)} of {len(tensors)} outputs",
            hint="Compare only a capture that succeeded and read back every output.",
        )
    observed = tuple(_read(item) for item in capture.outputs)
    if expect.source == "files":
        reference = ReferenceOutputs(
            ReferenceOrigin("files"), tuple(_read(f) for f in expect.files)
        )
    elif expect.source == "build":
        reference = ReferenceOutputs(
            ReferenceOrigin("build"), tuple(_read(f) for f in build.outputs)
        )
    else:
        # check_fixture_expectation refused a build without a model.
        assert build.model is not None
        reference = host_reference(
            _read(build.model),
            [(io.tensor, _read(io.data)) for io in build.inputs],
            tensors,
            resolver=expect.resolver,
            invocations=build.warmups + build.iterations,
        )
    return compare_outputs(tensors, reference, observed, expect.tolerance)


def _check_resolver(resolver: str) -> None:
    if resolver not in get_args(LiteRtResolver):
        raise ConfigError(
            f"Unknown LiteRT resolver {resolver!r}",
            hint=f"Use one of: {', '.join(get_args(LiteRtResolver))}.",
        )


def _read(item: FixtureFile | Path) -> bytes:
    try:
        return item.read() if isinstance(item, FixtureFile) else item.read_bytes()
    except (OSError, ValueError) as exc:
        raise ConfigError(f"Cannot read {getattr(item, 'path', item)}: {exc}") from exc


def _array(tensor: FixtureTensor, data: bytes, label: str) -> np.ndarray:
    if len(data) != tensor.size_bytes:
        raise ConfigError(
            f"{tensor.name}: {label} has {len(data)} B, the tensor {tensor.size_bytes} B"
        )
    return np.frombuffer(data, dtype=_NUMPY_DTYPES[tensor.dtype]).reshape(tensor.shape)


def _compare(
    tensor: FixtureTensor, expected: bytes, actual: bytes, tolerance: Tolerance | None
) -> TensorComparison:
    ref = _array(tensor, expected, "reference")
    obs = _array(tensor, actual, "observed")
    if ref.dtype.kind == "f":
        ref64, obs64 = ref.astype(np.float64), obs.astype(np.float64)
        same = (ref64 == obs64) | (np.isnan(ref64) & np.isnan(obs64))
        with np.errstate(invalid="ignore"):
            error = np.where(same, 0.0, np.abs(ref64 - obs64))
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
