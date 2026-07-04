"""heliaRT engine adapter package.

``HeliaRTAdapter`` lives in :mod:`.adapter`; distribution resolution and
version pinning live in :mod:`.artifacts` (``HELIART_VERSION`` is re-exported
here so ``helia_profiler.engines.helia_rt.HELIART_VERSION`` keeps working);
the generated NSX wrapper (the "heliaRT NSX Wrapper" shim) lives in
:mod:`.nsx_module`.
"""

from __future__ import annotations

from .adapter import HeliaRTAdapter
from .artifacts import (
    HELIART_GH_REPO,
    HELIART_MIN_VERSION,
    HELIART_MODULE,
    HELIART_PROJECT,
    HELIART_RELEASE_TAG,
    HELIART_VERSION,
    _resolve_source_path,
)
from .nsx_module import _install_nsx_module, _install_nsx_module_source

__all__ = [
    "HeliaRTAdapter",
    "HELIART_VERSION",
    "HELIART_MIN_VERSION",
    "HELIART_GH_REPO",
    "HELIART_RELEASE_TAG",
    "HELIART_PROJECT",
    "HELIART_MODULE",
]
