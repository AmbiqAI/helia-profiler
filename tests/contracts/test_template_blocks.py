"""Contracts binding the firmware template children to _main_base.cc.j2 (#154 Phase 4).

Template inheritance introduces one failure mode the render itself cannot
catch: a child block whose name matches nothing in the base is silently
ignored, and the base's default (often empty) renders in its place — the
engine's code simply vanishes from the firmware with no error. These
contracts make that a test failure, the same move Phase 1-3 made for
issue codes and comparability dimensions: the base's block set is the
registry, the children's override sets are the views, and every set is
pinned literally so drift is a reviewed decision rather than an accident.
"""

from __future__ import annotations

import pytest

from helia_profiler.firmware import _jinja_env

BASE = "_main_base.cc.j2"
CHILDREN = ("main.cc.j2", "main_aot.cc.j2", "main_executorch.cc.j2")


def _blocks(name: str) -> set[str]:
    return set(_jinja_env.get_template(name).blocks)


def test_every_child_block_matches_a_base_block():
    """The typo guard: an override of a nonexistent block renders nothing,
    silently. Any child block name outside the base's set is a bug by
    construction."""
    base_blocks = _blocks(BASE)
    for child in CHILDREN:
        stray = _blocks(child) - base_blocks
        assert not stray, f"{child} overrides blocks the base does not define: {sorted(stray)}"


def test_base_blocks_are_the_documented_set():
    # The literal pin, independent of introspection order: adding, renaming,
    # or removing a seam must show up here as a reviewed edit.
    assert _blocks(BASE) == {
        "engine_clean_window",
        "engine_early_globals",
        "engine_file_header",
        "engine_globals",
        "engine_heartbeat_arm",
        "engine_includes",
        "engine_invoke",
        "engine_io_metadata",
        "engine_iteration_setup",
        "engine_model_setup",
        "engine_model_storage",
        "engine_pass_init",
        "engine_pass_preamble",
        "engine_pass_profile_arm",
        "engine_pass_warmup_arm",
        "engine_pre_start",
        "engine_print_csv",
        "engine_profiler_off",
        "engine_profiler_on",
        "engine_psram_metadata",
        "engine_reset_inputs",
        "engine_reset_inputs_warm",
        "engine_start_metadata",
        "engine_window_prologue",
        "engine_window_restore",
    }


#: Blocks every engine must supply: the base renders nothing (or nothing
#: meaningful) for them, so a child that misses one ships firmware with the
#: engine's own code absent.
REQUIRED_ENGINE_BLOCKS = {
    "engine_file_header",
    "engine_globals",
    "engine_heartbeat_arm",
    "engine_includes",
    "engine_invoke",
    "engine_iteration_setup",
    "engine_pass_init",
    "engine_print_csv",
    "engine_profiler_off",
    "engine_reset_inputs",
    "engine_reset_inputs_warm",
    "engine_start_metadata",
}


@pytest.mark.parametrize("child", CHILDREN)
def test_child_supplies_every_required_engine_block(child: str):
    missing = REQUIRED_ENGINE_BLOCKS - _blocks(child)
    assert not missing, f"{child} is missing required engine blocks: {sorted(missing)}"


def test_child_override_sets_are_the_documented_ones():
    """Membership pins per child. A block moving in or out of a child's
    override set changes what that engine's firmware contains — reviewed
    here, never silent."""
    assert _blocks("main.cc.j2") == REQUIRED_ENGINE_BLOCKS | {
        "engine_model_setup",
        "engine_model_storage",
        "engine_pre_start",
        "engine_profiler_on",
    }
    assert _blocks("main_aot.cc.j2") == REQUIRED_ENGINE_BLOCKS | {
        "engine_early_globals",
        "engine_io_metadata",
        "engine_model_setup",
        "engine_pass_profile_arm",
        "engine_pass_warmup_arm",
        "engine_profiler_on",
        "engine_psram_metadata",
        "engine_window_prologue",
        "engine_window_restore",
    }
    assert _blocks("main_executorch.cc.j2") == REQUIRED_ENGINE_BLOCKS | {
        "engine_clean_window",
        "engine_model_storage",
        "engine_pass_profile_arm",
        "engine_pass_warmup_arm",
    }


def test_only_executorch_owns_its_clean_window():
    """The skeleton owns the measured window for every engine whose invoke IS
    the inference. engine_clean_window exists solely because ExecuTorch's
    run_once_profiled() reloads the model per call and reports its own
    execute-only cycle count (see the seam comment in _main_base.cc.j2). An
    override appearing on another child means its HPX_CLEAN_INFER_* semantics
    changed — that must be a deliberate, reviewed decision."""
    assert "engine_clean_window" not in _blocks("main.cc.j2")
    assert "engine_clean_window" not in _blocks("main_aot.cc.j2")
    assert "engine_clean_window" in _blocks("main_executorch.cc.j2")


def test_reserved_default_blocks_are_the_documented_set():
    # Seams no child currently claims: the base's default content stands for
    # every engine. Shrinkage means a child claimed one (update the child
    # pins above); growth means a new seam was added (update the base pin).
    base_blocks = _blocks(BASE)
    claimed: set[str] = set()
    for child in CHILDREN:
        claimed |= _blocks(child)
    assert base_blocks - claimed == {"engine_pass_preamble"}
