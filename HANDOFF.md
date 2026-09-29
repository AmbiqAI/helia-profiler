## Hero caption follow-up

Removed the two visible illustrative captions from Layers and Energy at owner request. Branch codex/remove-hero-captions, based on merged main d2b3b151; issue #320. Build passed and both hero slides visually checked with captions removed. Local only, not published.

# Documentation follow-up

## Goal and state
Polish the published profiler docs following PR #332. Owner authorized a follow-up PR and merge, allowing unrelated CI to remain running. Worktree: agent-a5c6d2808dbc33deb; branch: codex/remove-landing-power-step; issue #320. Based on main ad8b71a1.

## Changes
- Remove the Power tab and its placeholder instructions from the Home demo. Five steps remain, ending at Compare; adjacent copy updated.
- Use landing-page neon green for dark-mode shared card icons and terminal success rows, darker green for light-mode contrast.
- Show fictional connected J-Link and serial-port examples, labeled illustrative. Keep real CLI columns and port kinds. Highlight stable board rows and mute headers.
- Replace repetitive next-step prose with direct profile and troubleshooting links.

## Verification
Site builds passed after edits. Rendered cards and terminal examples inspected in both themes. Verified five Home tabs, keyboard and automatic Compare-to-Check wrap, and no browser errors. Documentation CI passed on 187d6488, including browser coverage. Runtime code and dependencies unchanged.

## Next
PR #400 admin squash-merged as d2b3b151f3faab28572bb32353f813f23796618c. Documentation CI passed on 187d6488; final commit changed only this handoff. Full latest CI was not awaited, as authorized. Public deployment not yet verified. Verify remote merge and distinguish it from public deployment. PR #332 previously merged as ad8b71a1 with all CI green. Preserve this worktree and the running local preview at port 8764.

PR #400 opened. Eight browser tests pass. Docs CI exposed a source-link checker that truncated slash-containing branches; fixed matching against the full ref and source path, with regression tests.
