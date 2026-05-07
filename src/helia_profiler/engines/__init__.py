"""Inference engine types and adapter registry."""

from __future__ import annotations

from enum import StrEnum


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
