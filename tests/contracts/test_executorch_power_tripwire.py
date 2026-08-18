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
    "stages.preflight._check_transport_support now accepts "
    "engine.type=executorch with power.enabled=True, but "
    "'executorch' is still absent from the render-contract engine matrix "
    "(_ENGINES in tests/contracts/test_firmware_render_snapshots.py). Lifting "
    "the preflight rejection in stages/preflight.py resurrects the #106/#107 "
    "frozen/garbage-window DWT bug class for main_executorch.cc.j2: that "
    "template still times its clean loop with DWT->CYCCNT and has no "
    "power_only window bracketing, terminal record, or "
    "SocCapabilities.power_window_timer consumption. Before removing this "
    "rejection: (1) add the power_only machinery to main_executorch.cc.j2 "
    "(mirroring main.cc.j2/main_aot.cc.j2); (2) give _render() in "
    "tests/contracts/test_firmware_render_snapshots.py an executorch branch "
    "that renders main_executorch.cc.j2 -- WITHOUT this, adding the engine to "
    "_ENGINES renders main.cc.j2 under an executorch key and writes snapshot "
    "entries byte-identical to tflm, so the DWT guards below pass while "
    "covering nothing; (3) add 'executorch' to _ENGINES and regenerate "
    "tests/contracts/snapshots/firmware_render.json; (4) confirm "
    "test_window_is_never_timed_by_a_domain_the_binary_powers_down / "
    "test_free_running_power_binary_never_times_the_window_with_dwt cover it. "
    "(If you MOVED the rejection to another function rather than removing it, "
    "production is fine and this test just needs to call the new one -- it "
    "names _check_transport_support deliberately so this stays a loud, "
    "fail-closed prompt to re-point it rather than a silent loss of the guard.)"
)


def _executorch_power_cfg(tmp_path, mode="external", board="apollo510_evb"):
    model = tmp_path / "model.pte"
    power = {"enabled": True, "mode": mode}
    if mode == "internal":
        # An on-device monitor is the only way to ask for internal mode, and
        # ina228 requires its own block (shunt value) to validate at all.
        power.update(driver="ina228", ina228={"shunt_ohms": 0.1})
    overrides = {
        "model": {"path": str(model)},
        "engine": {"type": "executorch"},
        "target": {"board": board},
        "power": power,
    }
    return load_config(None, overrides)


# Both modes, because narrowing the gate is a likelier relaxation than deleting
# it, and the two modes are not equally bad. Internal mode is the WORSE one to
# let through: the firmware's own clock is the denominator for average power
# and current (see power/diagnostics.py), so a frozen DWT scales both by the
# error -- where external mode's numbers come from the instrument and only
# elapsed_us is affected. A tripwire that only pinned the default (external)
# would stay green through exactly the relaxation that does the most damage.
#
# And one board per SoC family, because the bug class's two root causes ARE
# family-specific (AP4 powers the debug domain down via
# broad_peripheral_shutdown; AP3 simply has nothing asserting CDBGPWRUPREQ), so
# "exempt one family" is a plausible narrowing too. Adversarial review proved
# the gap: exempting AP3 from the gate left a mode-only tripwire fully green
# while the family this module's own docstring names as vulnerable walked
# straight through.
@pytest.mark.parametrize("board", ["apollo3p_evb", "apollo4p_evb", "apollo510_evb"])
@pytest.mark.parametrize("mode", ["external", "internal"])
def test_preflight_accepting_executorch_power_requires_engine_matrix_coverage(
    tmp_path, mode, board
):
    """Fails the moment preflight stops rejecting executorch+power while the
    render-contract matrix has not been extended to cover it.

    Today ``_check_transport_support`` raises ``ConfigError`` for this
    combination, so this test currently just confirms that (and stays green).
    The moment that rejection is removed -- or narrowed to only one power mode
    -- without the matching render/test work, this flips to a hard failure
    naming the bug class.
    """
    cfg = _executorch_power_cfg(tmp_path, mode, board)

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
