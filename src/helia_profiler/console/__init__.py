"""Rich console output for heliaPROFILER — public package facade.

The :class:`HpxConsole` implementation is split across submodules by concern
(``progress``, ``results``, ``compare``, ``analysis``, ``doctor``,
``tables``); this package re-exports the stable public surface so callers
can keep doing ``from helia_profiler.console import HpxConsole``.
"""

from __future__ import annotations

from .base import HpxConsole, _console, _status_console

__all__ = ["HpxConsole"]
