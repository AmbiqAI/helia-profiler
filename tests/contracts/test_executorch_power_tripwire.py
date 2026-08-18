"""Tripwire: preflight lifting the ExecuTorch+power rejection must not go
unnoticed by the render-contract matrix.

``main_executorch.cc.j2`` (added in #100) times its clean loop with
``DWT->CYCCNT`` and has none of the ``power_only`` machinery (window
bracketing, terminal record, ``SocCapabilities.power_window_timer``
consumption) that #106/#107 added to ``main.cc.j2``/``main_aot.cc.j2`` to
avoid the frozen/garbage-window bug class: on AP4, ``broad_peripheral_shutdown``
powers the debug domain (where DWT lives) down; on AP3, nothing asserts
``CDBGPWRUPREQ`` for a free-running binary, so the counter never advances.

Today this is dead code: ``stages.preflight._check_transport_support``
rejects ``engine.type == executorch`` combined with ``power.enabled``. But
the render-contract tests in ``test_firmware_render_snapshots.py`` iterate
``_ENGINES = ["tflm", "helia-rt", "helia-aot"]`` -- executorch is not in that
matrix, so nothing pins the DWT-timing invariant for it. If someone lifts the
preflight gate without also doing the ExecuTorch power work (extending
``_ENGINES``, regenerating snapshots, adding ``power_only`` machinery to
``main_executorch.cc.j2``), the bug class comes back with nothing watching.

This test does not implement the fix. It fails loudly at exactly the moment
described above, so the gap can no longer be silent.
"""

from __future__ import annotations

import pytest

from helia_profiler.config import load_config
from helia_profiler.errors import ConfigError
from helia_profiler.stages.preflight import _check_transport_support

from .test_firmware_render_snapshots import _ENGINES

_BUG_CLASS_MESSAGE = (
    "preflight now accepts engine.type=executorch with power.enabled=True, but "
    "'executorch' is still absent from the render-contract engine matrix "
    "(_ENGINES in tests/contracts/test_firmware_render_snapshots.py). Lifting "
    "the preflight rejection in stages/preflight.py resurrects the #106/#107 "
    "frozen/garbage-window DWT bug class for main_executorch.cc.j2: that "
    "template still times its clean loop with DWT->CYCCNT and has no "
    "power_only window bracketing, terminal record, or "
    "SocCapabilities.power_window_timer consumption. Before removing this "
    "rejection: add the power_only machinery to main_executorch.cc.j2 "
    "(mirroring main.cc.j2/main_aot.cc.j2), add 'executorch' to _ENGINES, "
    "regenerate tests/contracts/snapshots/firmware_render.json, and confirm "
    "test_window_is_never_timed_by_a_domain_the_binary_powers_down / "
    "test_free_running_power_binary_never_times_the_window_with_dwt cover it."
)


def _executorch_power_cfg(tmp_path):
    model = tmp_path / "model.pte"
    overrides = {
        "model": {"path": str(model)},
        "engine": {"type": "executorch"},
        "power": {"enabled": True},
    }
    return load_config(None, overrides)


def test_preflight_accepting_executorch_power_requires_engine_matrix_coverage(tmp_path):
    """Fails the moment preflight stops rejecting executorch+power while the
    render-contract matrix has not been extended to cover it.

    Today ``_check_transport_support`` raises ``ConfigError`` for this
    combination, so this test currently just confirms that (and stays green).
    The moment that rejection is removed -- without the matching render/test
    work -- this flips to a hard failure naming the bug class.
    """
    cfg = _executorch_power_cfg(tmp_path)

    try:
        _check_transport_support(cfg)
    except ConfigError:
        preflight_accepts_executorch_power = False
    else:
        preflight_accepts_executorch_power = True

    if preflight_accepts_executorch_power:
        assert "executorch" in _ENGINES, _BUG_CLASS_MESSAGE
    else:
        # Still gated -- nothing to pin yet, but assert the gate directly so
        # this test doesn't pass vacuously if _check_transport_support starts
        # raising for an unrelated reason (e.g. a bad tmp_path model).
        with pytest.raises(ConfigError, match="ExecuTorch profiling"):
            _check_transport_support(cfg)
