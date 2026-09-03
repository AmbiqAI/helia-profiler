"""Tripwire: lifting the ExecuTorch+power preflight rejection must not go
unnoticed by the render-contract matrix.

``main_executorch.cc.j2`` has none of the ``power_only`` machinery that
``main.cc.j2``/``main_aot.cc.j2`` carry against the frozen/garbage-window
bug class (#106/#107): its ``engine_clean_window`` override reports the
runtime's own ``execution_cycles`` and declares neither the STIMER bracket
nor ``clean_cycles``, so a ``power_only`` render would reach
``_power_terminal_success.j2`` with no duration source.  That is why the
matrix pairs executorch with neither ``_power_combos`` nor the busy-loop
probe, and why ``stages.preflight._check_transport_support`` rejects
executorch + power.

This test does not implement the fix; it fails the moment that gate is
lifted without the ExecuTorch power work.
"""

from __future__ import annotations

import pytest

from helia_profiler.config import load_config
from helia_profiler.errors import ConfigError
from helia_profiler.stages.preflight import _check_transport_support

from .test_firmware_render_snapshots import _ENGINES, _MATRIX_ENGINES

_BUG_CLASS_MESSAGE = (
    "stages.preflight._check_transport_support now accepts "
    "engine.type=executorch with power.enabled=True, but "
    "'executorch' is absent from the render-contract engine matrix "
    "(_ENGINES in tests/contracts/test_firmware_render_snapshots.py). Lifting "
    "the preflight rejection in stages/preflight.py resurrects the #106/#107 "
    "frozen/garbage-window bug class for main_executorch.cc.j2: it has no "
    "power_only window bracketing, no terminal record, and no "
    "SocCapabilities.power_window_timer consumption -- its engine_clean_window "
    "override reports HPX_CLEAN_INFER_* from the runtime's own "
    "execution_cycles and declares neither clean_stimer_total_us nor "
    "clean_cycles, so a power_only render reaches _power_terminal_success.j2 "
    "with no duration source in scope. Before removing this rejection: "
    "(1) add the power_only machinery to main_executorch.cc.j2's "
    "engine_clean_window override (mirroring what _main_base.cc.j2's default "
    "does for main.cc.j2/main_aot.cc.j2); (2) add 'executorch' to "
    "_MATRIX_ENGINES in tests/contracts/test_firmware_render_snapshots.py so "
    "it joins _power_combos()/the busy-loop matrices, and regenerate "
    "tests/contracts/snapshots/firmware_render.json; (3) confirm "
    "test_window_is_never_timed_by_a_domain_the_binary_powers_down / "
    "test_free_running_power_binary_never_times_the_window_with_dwt cover it "
    "(note _ENGINE_SOCS pairs executorch with apollo510 only, which is a "
    "Cortex-M55 part -- if ExecuTorch ever ships on AP3/AP4, both of those "
    "guards become load-bearing for it). "
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
        # Two matrices, because #154 phase 4 split them. ``_ENGINES`` is the
        # non-power render matrix and executorch joined it when
        # main_executorch.cc.j2 became a child of _main_base.cc.j2 -- so
        # keying only on that would leave this tripwire vacuous, passing the
        # moment the preflight gate is lifted while nothing pinned a single
        # power render. ``_MATRIX_ENGINES`` is the one that still excludes
        # executorch (power_only + busy-loop combos), so it is the live half
        # of the guard; ``_ENGINES`` is kept as the weaker precondition so a
        # future removal of executorch from the render matrix altogether also
        # trips here rather than silently satisfying the check.
        assert "executorch" in _ENGINES, _BUG_CLASS_MESSAGE
        assert "executorch" in _MATRIX_ENGINES, _BUG_CLASS_MESSAGE
    else:
        # Still gated -- nothing to pin yet, but assert the gate directly so
        # this test doesn't pass vacuously if _check_transport_support starts
        # raising for an unrelated reason (e.g. a bad tmp_path model).
        with pytest.raises(ConfigError, match="ExecuTorch profiling"):
            _check_transport_support(cfg)


@pytest.mark.parametrize("board", ["apollo3p_evb", "apollo4p_evb", "apollo510_evb"])
def test_preflight_rejects_executorch_with_the_busy_loop_clean_window_probe(
    tmp_path, board
):
    """The busy_loop probe is the second door into the same room.

    ``clean_window_probe=busy_loop`` replaces the model with a calibrated CPU
    spin so an external instrument has a known-shape window to gate on, and the
    firmware reports ``HPX_CLEAN_INFER_COUNT=1`` for that single unit of work.
    It exists only to serve a power capture, which ExecuTorch does not support.

    Before #154 phase 4 this combination was harmless by accident: the
    standalone template had no busy_loop branch, so the option simply did
    nothing. As a child of ``_main_base.cc.j2`` it now inherits one --
    ``engine_clean_window``'s override delegates the busy_loop case straight
    back to ``super()`` -- so the render would succeed and ship a nop-loop
    window under keys this engine defines as real execute-only inference
    timing. Power is off on this path, so none of the power-side integrity
    checks would ever look at it.

    Rejected in preflight instead, and pinned here per SoC family for the same
    reason the power tripwire above is: a narrowing that exempts one family is
    a likelier relaxation than a deletion.
    """
    model = tmp_path / "model.pte"
    cfg = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "executorch"},
            "target": {"board": board},
            "profiling": {"clean_window_probe": "busy_loop"},
        },
    )

    with pytest.raises(ConfigError, match="busy_loop"):
        _check_transport_support(cfg)

    # And the default probe is untouched -- the rejection must be about the
    # probe, not about ExecuTorch reaching this function at all.
    ok_cfg = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "executorch"},
            "target": {"board": board},
        },
    )
    _check_transport_support(ok_cfg)
