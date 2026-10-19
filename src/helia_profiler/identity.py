"""Shared identity vocabulary: canonical engine and kernel names, and reasoned unknowns.

The canonical names are the ones heliaPROFILER, heliaESTIMATOR and the
benchmark harness share. Each component's existing spellings stay accepted
as aliases: the engine selector, the TFLM backend, the ExecuTorch kernel
provider, the NSX module and the library project name.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


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
    "upstream": CanonicalEngine.LITERT,
    "helia-rt": CanonicalEngine.HELIA_RT,
    "helia_rt": CanonicalEngine.HELIA_RT,
    "heliart": CanonicalEngine.HELIA_RT,
    "helia-aot": CanonicalEngine.HELIA_AOT,
    "helia_aot": CanonicalEngine.HELIA_AOT,
    "executorch": CanonicalEngine.EXECUTORCH,
}

_KERNEL_ALIASES = {
    "reference": CanonicalKernel.REFERENCE,
    "cmsis-nn": CanonicalKernel.CMSIS_NN,
    "cmsis_nn": CanonicalKernel.CMSIS_NN,
    "arm-cmsis-nn": CanonicalKernel.CMSIS_NN,
    "arm": CanonicalKernel.CMSIS_NN,
    "helia-core": CanonicalKernel.HELIA_CORE,
    "ns-cmsis-nn": CanonicalKernel.HELIA_CORE,
    "nsx-cmsis-nn": CanonicalKernel.HELIA_CORE,
    "ns": CanonicalKernel.HELIA_CORE,
    "helia": CanonicalKernel.HELIA_CORE,
}


def canonical_engine(name: str) -> CanonicalEngine:
    """The shared name for an engine, from its canonical name or any alias."""
    return _canonical(name, _ENGINE_ALIASES, "engine")


def canonical_kernel(name: str) -> CanonicalKernel:
    """The shared name for a kernel library, from its canonical name or any alias."""
    return _canonical(name, _KERNEL_ALIASES, "kernel library")


def _canonical(name: str, aliases: dict, what: str):
    try:
        return aliases[str(name).strip().lower()]
    except KeyError:
        raise ValueError(
            f"Unknown {what} name {name!r}; known: {', '.join(sorted(aliases))}"
        ) from None
