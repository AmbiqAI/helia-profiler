"""Inference engine types and adapter registry."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from .base import EngineAdapter


TFLM_ENGINE_HEADER = "tensorflow/lite/micro/micro_interpreter.h"


class EngineType(StrEnum):
    """Supported inference engine identifiers.

    ``StrEnum`` so values are interchangeable with raw strings — Jinja
    templates and YAML configs can compare against the canonical hyphen
    form (``"helia-aot"``) without manually unwrapping ``.value``.
    """

    TFLM = "tflm"
    HELIA_RT = "helia-rt"
    HELIA_AOT = "helia-aot"
    EXECUTORCH = "executorch"

    @property
    def wire_name(self) -> str:
        """Identifier the firmware emits on the wire (``HPX_ENGINE=``).

        The wire protocol predates the hyphenated config spelling and uses
        C-identifier-safe names, so ``helia-aot`` goes out as ``helia_aot``.
        Owned here rather than derived inline at the render boundary so the
        two spellings cannot drift apart.
        """
        return self.value.replace("-", "_")

    @property
    def short_slug(self) -> str:
        """Compact identifier used in case IDs and report tables."""
        if self is EngineType.HELIA_RT:
            return "rt"
        if self is EngineType.HELIA_AOT:
            return "aot"
        return self.value


def _load_tflm_adapter() -> "EngineAdapter":
    from .tflm import TFLMAdapter

    return TFLMAdapter()


def _load_helia_rt_adapter() -> "EngineAdapter":
    from .helia_rt import HeliaRTAdapter

    return HeliaRTAdapter()


def _load_helia_aot_adapter() -> "EngineAdapter":
    from .helia_aot import HeliaAOTAdapter

    return HeliaAOTAdapter()


def _load_executorch_adapter() -> "EngineAdapter":
    from .executorch import ExecuTorchAdapter

    return ExecuTorchAdapter()


# ---------------------------------------------------------------------------
# Engine adapter registry
# ---------------------------------------------------------------------------
# One factory per EngineType — the sole dispatch point for "which adapter
# implements this engine".  Factories are deferred (not adapter instances)
# so registering an engine doesn't force-import its heavy module (e.g.
# heliaAOT pulls in the AOT compiler) until it's actually requested.

_ADAPTER_FACTORIES: dict[EngineType, "Callable[[], EngineAdapter]"] = {
    EngineType.TFLM: _load_tflm_adapter,
    EngineType.HELIA_RT: _load_helia_rt_adapter,
    EngineType.HELIA_AOT: _load_helia_aot_adapter,
    EngineType.EXECUTORCH: _load_executorch_adapter,
}


def register_engine_adapter(engine_type: EngineType, factory: "Callable[[], EngineAdapter]") -> None:
    """Register (or override) the adapter factory for ``engine_type``.

    Exposed mainly for tests that need to stub an engine adapter without
    monkeypatching the underlying module.
    """
    _ADAPTER_FACTORIES[engine_type] = factory


def get_adapter(engine_type: EngineType) -> "EngineAdapter":
    """Instantiate the engine adapter for ``engine_type``.

    Adapters are cheap to construct — shared stages may call this
    directly to query capabilities (e.g. preflight calls
    :meth:`EngineAdapter.supports_runtime_split` before ``prepare()``
    runs in stage 2).
    """
    try:
        factory = _ADAPTER_FACTORIES[EngineType(engine_type)]
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Unknown engine type: {engine_type!r}") from exc
    return factory()
