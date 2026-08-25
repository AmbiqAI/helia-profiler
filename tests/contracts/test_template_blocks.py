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

import re

import pytest

from helia_profiler.firmware import _jinja_env

BASE = "_main_base.cc.j2"
CHILDREN = ("main.cc.j2", "main_aot.cc.j2", "main_executorch.cc.j2")


def _blocks(name: str) -> set[str]:
    return set(_jinja_env.get_template(name).blocks)


def _source(name: str) -> str:
    """The template's raw source, from the production loader.

    Source-level, not render-level, on purpose: the whitespace rules below are
    properties of how a block is WRITTEN, and a render can only show the
    consequence (a line silently commented out) on the one variable
    combination that happens to reach it.
    """
    loader = _jinja_env.loader
    assert loader is not None
    source, _, _ = loader.get_source(_jinja_env, name)
    # Windows checkouts materialize the templates with CRLF endings
    # (core.autocrlf), which would make every "opens with its own newline"
    # check below see "\r" instead of "\n". The rules are about logical line
    # structure, not byte-level endings, so normalize before matching.
    return source.replace("\r\n", "\n")


_BLOCK_OPEN = re.compile(r"\{%-?\s*block\s+(\w+)(?:\s+scoped)?(?:\s+required)?\s*-?%\}")
_BLOCK_CLOSE = re.compile(r"\{%-?\s*endblock(?:\s+\w+)?\s*-?%\}")


def _block_bodies(name: str) -> dict[str, str]:
    """Map block name -> raw source text between its open and close tags.

    None of these templates nests one ``engine_*`` block inside another (the
    base's ``engine_clean_window`` contains other blocks, but no CHILD does),
    so pairing each open tag with the next close tag is exact for the children
    this is applied to.
    """
    text = _source(name)
    bodies: dict[str, str] = {}
    for match in _BLOCK_OPEN.finditer(text):
        close = _BLOCK_CLOSE.search(text, match.end())
        assert close, f"{name}: block {match.group(1)} is never closed"
        bodies[match.group(1)] = text[match.end() : close.start()]
    return bodies


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
        "engine_pmu_storage_sram_resident",
        "engine_pre_start",
        "engine_print_csv",
        "engine_profiled_summary",
        "engine_profiler_off",
        "engine_profiler_on",
        "engine_psram_metadata",
        "engine_reset_inputs",
        "engine_reset_inputs_warm",
        "engine_start_metadata",
        "engine_window_prologue",
        "engine_window_restore",
    }


def test_base_block_count_is_pinned():
    """Belt to the set assertion's braces: a rename that swaps one name for
    another keeps the count, but an accidental extra seam does not."""
    assert len(_blocks(BASE)) == 27


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
        # Overridden EMPTY: run_once_profiled() is load+execute, so the base's
        # HPX_PROFILED_INFER_* summary would publish a different measurement
        # under the keys every other engine uses for execute-only timing.
        "engine_profiled_summary",
        # Overridden EMPTY: ET has no PSRAM support (preflight rejects it)
        # and declares no psram_info — the base's metadata include made
        # test-rendered psram arms uncompilable (#187 gate finding).
        "engine_psram_metadata",
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


#: Seams the base defines INSIDE ``engine_clean_window``'s span. A child that
#: overrides the enclosing window replaces the text these sit in, so its own
#: overrides of them would never be rendered at all -- the engine's code
#: vanishes with no error, which is the exact failure this module exists to
#: catch, one level down.
BLOCKS_NESTED_IN_CLEAN_WINDOW = {
    "engine_profiler_on",
    "engine_window_prologue",
    "engine_window_restore",
}


def test_the_nested_seams_really_are_inside_the_clean_window():
    """The nesting rule below is only worth anything if its premise holds; read
    it out of the base source rather than trusting this file's memory of it."""
    text = _source(BASE)
    open_match = _BLOCK_OPEN.search(text)
    while open_match and open_match.group(1) != "engine_clean_window":
        open_match = _BLOCK_OPEN.search(text, open_match.end())
    assert open_match, "engine_clean_window is gone from the base"
    # The window's own endblock is the LAST one before the block that follows
    # it in the base; find it by counting opens/closes from the anchor.
    depth = 0
    pos = open_match.end()
    span_end = None
    while pos < len(text):
        nxt_open = _BLOCK_OPEN.search(text, pos)
        nxt_close = _BLOCK_CLOSE.search(text, pos)
        assert nxt_close, "engine_clean_window is never closed"
        if nxt_open and nxt_open.start() < nxt_close.start():
            depth += 1
            pos = nxt_open.end()
            continue
        if depth == 0:
            span_end = nxt_close.start()
            break
        depth -= 1
        pos = nxt_close.end()
    assert span_end is not None
    span = text[open_match.end() : span_end]
    for name in BLOCKS_NESTED_IN_CLEAN_WINDOW:
        assert f"block {name} " in span, (
            f"{name} is no longer inside engine_clean_window's span in the "
            "base -- BLOCKS_NESTED_IN_CLEAN_WINDOW is stale, and the nesting "
            "rule it feeds is now either vacuous or wrong"
        )


@pytest.mark.parametrize("child", CHILDREN)
def test_a_child_owning_the_clean_window_owns_all_of_it(child: str):
    """Overriding ``engine_clean_window`` replaces everything inside it.

    Jinja resolves each block independently, so a child that overrides both the
    window and one of the seams nested in it renders the window override and
    silently DROPS the nested one -- no error, no missing-name diagnostic, just
    firmware without that engine's prologue/restore/profiler re-arm. An engine
    that owns the window has to inline whatever it needs from them.
    """
    blocks = _blocks(child)
    if "engine_clean_window" not in blocks:
        return
    swallowed = sorted(blocks & BLOCKS_NESTED_IN_CLEAN_WINDOW)
    assert not swallowed, (
        f"{child} overrides engine_clean_window AND {swallowed}, which live "
        "inside it -- those overrides would never render. Inline what they do "
        "into the window override instead."
    )


#: Overrides whose content MUST begin with its own newline: the base anchors
#: them to the END of a preceding line, so content that does not lead with a
#: newline is appended to that line.
LEADING_NEWLINE_BLOCKS = {
    "engine_clean_window",
    "engine_early_globals",
    "engine_file_header",
    "engine_globals",
    "engine_heartbeat_arm",
    "engine_includes",
    "engine_io_metadata",
    "engine_iteration_setup",
    "engine_model_setup",
    "engine_model_storage",
    "engine_pass_init",
    "engine_pass_preamble",
    "engine_pass_profile_arm",
    "engine_pass_warmup_arm",
    "engine_pre_start",
    "engine_profiled_summary",
    "engine_profiler_on",
    "engine_psram_metadata",
    "engine_start_metadata",
    "engine_window_prologue",
}

#: Overrides whose content must NOT begin with a newline. Two shapes, both
#: documented in the base's prelude: the single-line blocks (the base owns the
#: line, the override owns the statement) and the two reset-inputs blocks,
#: which are multi-line but reused at several call sites through ``self.``, so
#: the base supplies every surrounding newline. ``engine_window_restore`` is
#: the documented inversion: its anchor is at the START of a line, so it
#: carries the TRAILING newline instead of the leading one.
GLUED_BLOCKS = {
    "engine_invoke",
    # Declared inside a set-capture at the top of the base (#161): the
    # override is the bare literal "true"/"false"; any newline it adds is
    # swallowed by the capture's `| trim`, but the single-line shape keeps
    # the declaration site legible.
    "engine_pmu_storage_sram_resident",
    "engine_print_csv",
    "engine_profiler_off",
    "engine_reset_inputs",
    "engine_reset_inputs_warm",
    "engine_window_restore",
}

#: Base anchors glued to the end of a ``//`` COMMENT line. These are the ones
#: with teeth: an override that does not lead with a newline is not merely
#: mis-indented, it is commented out of the firmware, with no render error and
#: no compile error. Verified against the base source by the test below so the
#: set cannot go stale.
COMMENT_GLUED_ANCHORS = {
    "engine_pass_preamble",
    "engine_pass_profile_arm",
    "engine_pass_warmup_arm",
}

#: (child, block) overrides whose content opens with a Jinja tag rather than
#: literal text, so the rule cannot be read off the first character. Each is
#: listed with the property asserted in its place.
CONDITIONAL_OVERRIDES = {
    ("main_executorch.cc.j2", "engine_clean_window"): (
        "both branches lead with a newline: the busy_loop branch delegates to "
        "super() (whose base content opens with one) and the else branch opens "
        "one itself"
    ),
}


def test_every_base_block_is_classified_by_shape():
    """A new seam must be given a shape here before it can be used, or the
    newline rule below silently stops covering it."""
    assert LEADING_NEWLINE_BLOCKS | GLUED_BLOCKS == _blocks(BASE)
    assert not (LEADING_NEWLINE_BLOCKS & GLUED_BLOCKS)
    assert COMMENT_GLUED_ANCHORS <= LEADING_NEWLINE_BLOCKS


def test_comment_glued_anchors_are_still_glued_to_comments():
    """Keep COMMENT_GLUED_ANCHORS honest against the base source.

    If one of these anchors is ever moved onto its own line the rule stops
    being load-bearing for it; if a NEW anchor is glued to a comment and not
    listed, this fails too -- so the set tracks the base rather than drifting
    from it.
    """
    text = _source(BASE)
    glued: set[str] = set()
    for match in _BLOCK_OPEN.finditer(text):
        line_start = text.rfind("\n", 0, match.start()) + 1
        preceding = text[line_start : match.start()]
        if "//" in preceding:
            glued.add(match.group(1))
    assert glued == COMMENT_GLUED_ANCHORS, (
        "the set of base anchors glued to a `//` comment line changed: "
        f"source says {sorted(glued)}, this file pins "
        f"{sorted(COMMENT_GLUED_ANCHORS)}"
    )


@pytest.mark.parametrize("child", CHILDREN)
def test_child_block_overrides_follow_the_whitespace_contract(child: str):
    """Source-level enforcement of the base prelude's whitespace contract.

    The render env has ``trim_blocks``/``lstrip_blocks`` OFF, so every newline
    around a tag is real output and the first character of an override decides
    whether its content starts a new line or is appended to the previous one.
    For the comment-glued anchors that difference is the whole bug: appended
    content lands after ``//`` and is commented out of the firmware silently.
    """
    for name, body in _block_bodies(child).items():
        key = (child, name)
        if key in CONDITIONAL_OVERRIDES:
            assert body.lstrip().startswith("{%"), (
                f"{child}:{name} is listed in CONDITIONAL_OVERRIDES but no "
                "longer opens with a Jinja tag -- classify it normally instead"
            )
            continue
        if body == "":
            # Deleting a region is always legal and carries no whitespace.
            continue
        if name in LEADING_NEWLINE_BLOCKS:
            assert body.startswith("\n"), (
                f"{child}:{name} must open with its own newline -- the base "
                "anchors it to the end of the preceding line"
                + (
                    ", which is a `//` comment, so this content is silently "
                    "commented out of the generated firmware"
                    if name in COMMENT_GLUED_ANCHORS
                    else ""
                )
            )
        else:
            assert not body.startswith("\n"), (
                f"{child}:{name} must NOT open with a newline -- the base "
                "supplies the newlines around this seam, so a leading one "
                "emits a stray blank line (and, at the sites reached through "
                "self.{name}(), breaks the indentation the base wrote)"
            )


def test_reserved_default_blocks_are_the_documented_set():
    # Seams no child currently claims: the base's default content stands for
    # every engine. Shrinkage means a child claimed one (update the child
    # pins above); growth means a new seam was added (update the base pin).
    base_blocks = _blocks(BASE)
    claimed: set[str] = set()
    for child in CHILDREN:
        claimed |= _blocks(child)
    assert base_blocks - claimed == {
        "engine_pass_preamble",
        # No engine keeps its per-layer storage in TCM yet, so the "true"
        # default stands for all three children (#161).
        "engine_pmu_storage_sram_resident",
    }
