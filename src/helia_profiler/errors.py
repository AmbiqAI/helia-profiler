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
    """NSX configure / build / flash / lock / sync failure.

    Carries the underlying tool's diagnostic output (cmake / ninja /
    SEGGER commander / git stderr, NSX exception message, etc.) in
    :attr:`details`.  When the source is a real subprocess, the original
    return code is also captured in :attr:`returncode`.

    The legacy ``stderr=`` kwarg / attribute is retained as an alias
    for back-compat; new code should use ``details=`` instead.
    """

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        returncode: int | None = None,
        details: str | None = None,
        stderr: str | None = None,
    ) -> None:
        # Accept either spelling.  ``stderr`` wins only when ``details``
        # is not supplied so callers migrating to the new kwarg get the
        # value they passed.
        resolved = details if details is not None else stderr
        self.returncode = returncode
        self.details = resolved
        super().__init__(message, hint=hint)

    @property
    def stderr(self) -> str | None:
        """Back-compat alias for :attr:`details`."""
        return self.details


class CaptureError(HpxError):
    """Data capture failure — serial timeout, corrupt data, SWO framing."""


class NetworkError(BuildError):
    """Transient network failure during sync/lock (git fetch, module download).

    Subclass of :class:`BuildError` so existing ``except BuildError`` handlers
    still catch it, but callers that want to retry can specifically catch this.
    """


class PowerError(HpxError):
    """Power measurement failure — Joulescope not found, calibration error."""


class ReportError(HpxError):
    """Report generation failure — output path not writable, format error."""


class ValidationBundleError(ReportError):
    """Malformed, unsupported, or unsafe validation bundle."""
