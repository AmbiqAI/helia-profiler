"""Rich console output for heliaPROFILER — public package facade.

The :class:`HpxConsole` implementation is split across submodules by concern
(``progress``, ``results``, ``compare``, ``analysis``, ``doctor``,
``tables``, plus the interactive ``presentation`` repr tables); this package re-exports the stable public surface so callers
can keep doing ``from helia_profiler.console import HpxConsole``.
"""

from __future__ import annotations

# NB: status_console is a LIVE re-export surface — profiler.py routes the
# stage-status stream through it (promoted from _status_console in #229 D5).
from .base import HpxConsole, _console, status_console

__all__ = ["HpxConsole"]
