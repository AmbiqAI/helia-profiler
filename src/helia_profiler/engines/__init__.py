"""Inference engine types and adapter registry."""

from __future__ import annotations

from enum import Enum


class EngineType(Enum):
    """Supported inference engine identifiers."""

    TFLM = "tflm"
    HELIA_RT = "helia-rt"
    HELIA_AOT = "helia-aot"
