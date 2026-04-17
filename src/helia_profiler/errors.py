"""Typed error hierarchy for heliaPROFILER.

Every error carries a human-readable message and optional context so failures
are never silent or obscure.  CLI and pipeline code should catch ``HpxError``
as the common base; individual stages raise the specific subclass.
"""

from __future__ import annotations


class HpxError(Exception):
    """Base exception for all heliaPROFILER errors."""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        self.hint = hint
        super().__init__(message)

    def __str__(self) -> str:
        base = super().__str__()
        if self.hint:
            return f"{base}\n  Hint: {self.hint}"
        return base


class ConfigError(HpxError):
    """Bad configuration — missing model path, invalid YAML, unknown board."""


class PlatformError(HpxError):
    """Unsupported board/SoC combination or missing platform capability."""


class EngineError(HpxError):
    """Engine adapter failure — AOT compile error, missing static lib, etc."""


class FirmwareError(HpxError):
    """Firmware generation failure — template rendering, file I/O."""


class BuildError(HpxError):
    """NSX configure / build / flash subprocess failure."""

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        returncode: int | None = None,
        stderr: str | None = None,
    ) -> None:
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(message, hint=hint)


class CaptureError(HpxError):
    """Data capture failure — serial timeout, corrupt data, SWO framing."""


class PowerError(HpxError):
    """Power measurement failure — Joulescope not found, calibration error."""


class ReportError(HpxError):
    """Report generation failure — output path not writable, format error."""
