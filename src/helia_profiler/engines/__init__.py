"""Inference engine types and adapter registry."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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

    @property
    def short_slug(self) -> str:
        """Compact identifier used in case IDs and report tables."""
        if self is EngineType.HELIA_RT:
            return "rt"
        if self is EngineType.HELIA_AOT:
            return "aot"
        return self.value


def get_adapter(engine_type: EngineType) -> "EngineAdapter":
    """Instantiate the engine adapter for ``engine_type``.

    Adapters are cheap to construct — shared stages may call this
    directly to query capabilities (e.g. preflight calls
    :meth:`EngineAdapter.supports_runtime_split` before ``prepare()``
    runs in stage 2).
    """
    # Local imports defer heavy module loads (e.g. heliaAOT pulls in
    # the AOT compiler) until the adapter is actually requested.
    if engine_type is EngineType.TFLM:
        from .tflm import TFLMAdapter

        return TFLMAdapter()
    if engine_type is EngineType.HELIA_RT:
        from .helia_rt import HeliaRTAdapter

        return HeliaRTAdapter()
    if engine_type is EngineType.HELIA_AOT:
        from .helia_aot import HeliaAOTAdapter

        return HeliaAOTAdapter()
    raise ValueError(f"Unknown engine type: {engine_type!r}")
