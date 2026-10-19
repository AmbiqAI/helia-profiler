"""Shared identity vocabulary: canonical engine and kernel names, and reasoned unknowns.

The canonical names are the ones heliaPROFILER, heliaESTIMATOR and the
benchmark harness share. Each component's existing spellings stay accepted
as aliases: the engine selector, the TFLM backend, the ExecuTorch kernel
provider, the NSX module and the library project name. Case, ``_`` and
``-`` are interchangeable.

These functions translate names, not evidence: a configured backend label
need not be what a build links (heliaRT always links heliaCORE), so derive
a build's kernel library from the module it linked.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar

_Name = TypeVar("_Name", bound=StrEnum)


class CanonicalEngine(StrEnum):
    """An inference engine, by its shared name."""

    LITERT = "litert"
    HELIA_RT = "helia-rt"
    HELIA_AOT = "helia-aot"
    EXECUTORCH = "executorch"


class CanonicalKernel(StrEnum):
    """A kernel library, by its shared name.

    ``CMSIS_NN`` is Arm's CMSIS-NN. ``HELIA_CORE`` is heliaCORE, the product
    name of Ambiq's ``ns-cmsis-nn`` library.
    """

    REFERENCE = "reference"
    CMSIS_NN = "cmsis-nn"
    HELIA_CORE = "helia-core"


@dataclass(frozen=True)
class Unknown:
    """A fact that could not be determined, with the reason."""

    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("Unknown needs a reason")


_ENGINE_ALIASES = {
    "litert": CanonicalEngine.LITERT,
    "tflm": CanonicalEngine.LITERT,
    "tflite-micro": CanonicalEngine.LITERT,
    "nsx-tflite-micro": CanonicalEngine.LITERT,
    "upstream": CanonicalEngine.LITERT,
    "helia-rt": CanonicalEngine.HELIA_RT,
    "heliart": CanonicalEngine.HELIA_RT,
    "nsx-helia-rt": CanonicalEngine.HELIA_RT,
    "helia-aot": CanonicalEngine.HELIA_AOT,
    "heliaaot": CanonicalEngine.HELIA_AOT,
    "executorch": CanonicalEngine.EXECUTORCH,
    "nsx-executorch": CanonicalEngine.EXECUTORCH,
}

_KERNEL_ALIASES = {
    "reference": CanonicalKernel.REFERENCE,
    "cmsis-nn": CanonicalKernel.CMSIS_NN,
    "arm-cmsis-nn": CanonicalKernel.CMSIS_NN,
    "arm": CanonicalKernel.CMSIS_NN,  # ExecuTorch's kernel provider
    "helia-core": CanonicalKernel.HELIA_CORE,
    "heliacore": CanonicalKernel.HELIA_CORE,
    "ns-cmsis-nn": CanonicalKernel.HELIA_CORE,
    "nsx-cmsis-nn": CanonicalKernel.HELIA_CORE,
    "ns": CanonicalKernel.HELIA_CORE,  # ExecuTorch's kernel provider
    "helia": CanonicalKernel.HELIA_CORE,  # heliaRT's backend
}


def canonical_engine(name: str) -> CanonicalEngine:
    """The shared name for an engine, from its canonical name or any alias."""
    return _canonical(name, _ENGINE_ALIASES, "engine")


def canonical_kernel(name: str) -> CanonicalKernel:
    """The shared name for a kernel library, from its canonical name or any alias."""
    return _canonical(name, _KERNEL_ALIASES, "kernel library")


def _canonical(name: str, aliases: Mapping[str, _Name], what: str) -> _Name:
    if not isinstance(name, str):
        raise ValueError(f"A {what} name must be a string, not {type(name).__name__}")
    try:
        return aliases[name.strip().lower().replace("_", "-")]
    except KeyError:
        raise ValueError(
            f"Unrecognised {what} name {name!r}; known: {', '.join(sorted(aliases))}"
        ) from None


@dataclass(frozen=True)
class RuntimeIdentity:
    """The runtime a build linked: its engine, source repository, version and commit."""

    engine: CanonicalEngine
    repository: str | Unknown
    version: str | Unknown
    commit: str | Unknown


@dataclass(frozen=True)
class KernelIdentity:
    """The kernel library a build linked, taken from the module or archive it linked."""

    library: CanonicalKernel | Unknown
    repository: str | Unknown
    version: str | Unknown
    commit: str | Unknown


@dataclass(frozen=True)
class BuildFacts:
    """How the image was built and placed."""

    toolchain: str | Unknown
    toolchain_version: str | Unknown
    board: str
    cpu_hz: int | Unknown
    clock_profile: str | Unknown
    arena_location: str | Unknown
    weights_location: str | Unknown
    arena_bytes: int | Unknown


_UNRECORDED = Unknown("the record predates this fact")


@dataclass(frozen=True)
class RunFacts:
    """How the model was run: state handling, invocation counts, the timed span and its clock.

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
    """

    lifecycle: str
    iterations: int
    warmups: int
    timing_scope: str
    instance_boundary: str | Unknown = _UNRECORDED
    first_invoke_initializer: bool | Unknown = _UNRECORDED
    first_invoke_window: str | Unknown = _UNRECORDED
    timing_clock: str | Unknown = _UNRECORDED
    timing_clock_hz: int | Unknown = _UNRECORDED
    timing_statistic: str | Unknown = _UNRECORDED


@dataclass(frozen=True)
class RunIdentity:
    """Everything a comparison needs to know about what ran.

    ``build_hash`` covers the flashed image, the dependency lock, the linked
    runtime archive and the model, and never the HPX release (``producer``),
    so it changes only when what runs changes. ``observation_hash`` covers a
    capture's measured facts, so the same firmware measured twice gives two.
    ``intent_hash`` is the :attr:`~helia_profiler.FrozenRun.intent_hash` of
    the plan a run was built from, and ``None`` for a run built without one.
    """

    runtime: RuntimeIdentity
    kernels: KernelIdentity
    prepared_archive_sha256: str | None
    build: BuildFacts
    baseline_id: str | Unknown
    dependency_lock_sha256: str | Unknown
    model_sha256: str | Unknown
    inputs_sha256: str | Unknown
    run: RunFacts
    producer: str | Unknown
    build_hash: str | Unknown
    observation_hash: str | None = None
    intent_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form; an unknown fact becomes ``{"unknown": reason}``."""
        return _plain(self)


def _plain(value: Any) -> Any:
    if isinstance(value, Unknown):
        return {"unknown": value.reason}
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {name: _plain(getattr(value, name)) for name in value.__dataclass_fields__}
    return value
