"""heliaAOT engine adapter package.

``HeliaAOTAdapter`` lives in :mod:`.adapter`; board/platform mapping and AOT
compiler invocation live in :mod:`.compile`; operator-manifest and
memory-plan extraction live in :mod:`.manifest`.

ns-cmsis-nn resolution is NOT here: three engines need it (heliaAOT, heliaRT
and ExecuTorch, with TFLM to follow under the source-build plan), so it lives
in the neutral :mod:`helia_profiler.engines.cmsis_nn` rather than having the
other engines import out of this one's package (issue #7).
"""

from __future__ import annotations

from .adapter import HeliaAOTAdapter
from .compile import HELIAAOT_MIN_VERSION

__all__ = [
    "HeliaAOTAdapter",
    "HELIAAOT_MIN_VERSION",
]
