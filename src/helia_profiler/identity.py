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
from typing import TypeVar

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
