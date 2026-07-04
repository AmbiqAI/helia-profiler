"""heliaAOT engine adapter package.

``HeliaAOTAdapter`` lives in :mod:`.adapter`; board/platform mapping and AOT
compiler invocation live in :mod:`.compile`; operator-manifest and
memory-plan extraction live in :mod:`.manifest`; ns-cmsis-nn resolution and
NSX module wrapping live in :mod:`.cmsis_nn` (also used by the heliaRT
adapter's source-build path).
"""

from __future__ import annotations

from .adapter import HeliaAOTAdapter
from .cmsis_nn import CMSIS_NN_MODULE, CMSIS_NN_PROJECT, cmsis_nn_module_ref
from .compile import HELIAAOT_MIN_VERSION

__all__ = [
    "HeliaAOTAdapter",
    "HELIAAOT_MIN_VERSION",
    "CMSIS_NN_PROJECT",
    "CMSIS_NN_MODULE",
    "cmsis_nn_module_ref",
]
