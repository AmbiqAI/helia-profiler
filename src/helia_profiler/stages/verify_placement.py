"""Stage 4b — Verify memory placement.

A static, build-time guard that the tensor arena actually landed in the
memory region the planner resolved.  It reads the arena symbol's link
address from the freshly built ELF and asserts it falls inside the
resolved placement region's physical address range.

This catches the class of silent linker mislocations — e.g. the armclang
``SHARED_SRAM`` scatter gap that left the arena in TCM while the planner
believed it was in SRAM — at build time, *before* flashing, instead of as
a confusing runtime failure several layers downstream.

Best-effort and non-destructive: any case we cannot verify (PSRAM runtime
pointer, heliaAOT's multi-region arenas, an uncharacterised SoC memory
model, or an unreadable symbol) is skipped with a debug log rather than
failing the build.
"""

from __future__ import annotations

import logging

from ..engines import EngineType
from ..errors import BuildError
from ..pipeline import PipelineContext
from ..placement import MemoryRegion, Placement
from ..platform import classify_address, linked_memory_map
from ..toolchain_probe import symbol_address

log = logging.getLogger("hpx")

#: Arena PLACEMENT -> verified-map region. The placement vocabulary calls
#: the tightly-coupled bank "TCM"; the measured map calls it DTCM.
_PLACEMENT_REGION = {
    Placement.TCM: MemoryRegion.DTCM,
    Placement.SRAM: MemoryRegion.SRAM,
    Placement.MRAM: MemoryRegion.MRAM,
    Placement.PSRAM: MemoryRegion.PSRAM,
}

#: Arena storage symbol emitted by the interpreter firmware template
#: (``main.cc.j2``).  Mangled to ``_ZL15g_arena_storage`` by C++ compilers;
#: matched as a suffix so both mangled and plain forms resolve.
_ARENA_SYMBOL = "g_arena_storage"


class VerifyPlacementStage:
    """Assert the arena symbol landed in its intended memory region."""

    @property
    def name(self) -> str:
        return "verify_placement"

    def should_skip(self, ctx: PipelineContext) -> bool:
        # Nothing to check without an ELF, SoC, and a resolved region.
        if ctx.binary_path is None or ctx.soc is None or ctx.arena_region is None:
            return True
        # PSRAM arenas are bound to a runtime pointer (no static storage
        # symbol), so there is nothing to verify statically.
        if ctx.arena_region == Placement.PSRAM:
            return True
        # heliaAOT emits per-region arena buffers with different symbols; the
        # interpreter arena guard does not apply.  Scope to TFLM / heliaRT.
        if ctx.config.engine.type == EngineType.HELIA_AOT:
            return True
        return False

    def run(self, ctx: PipelineContext) -> None:
        soc = ctx.resolved_soc
        binary_path = ctx.built_binary_path
        arena_region = ctx.planned_arena_region

        # #133 Phase 2 migration: verify against the characterized
        # linked-memory map, not the legacy family placement table (whose
        # AP5 MRAM base was entirely wrong — every divergence between the
        # two is pinned by tests/test_memory_map.py). Interpreter builds
        # (the only ones this stage checks) always link the DEFAULT
        # profile — the linker_profile knob is AOT-only, and AOT skips.
        windows = linked_memory_map(soc)
        expected_window = next(
            (
                w.window
                for w in windows
                if w.region is _PLACEMENT_REGION.get(arena_region)
            ),
            None,
        )
        if expected_window is None:
            log.debug(
                "No verified window for %s on %s; skipping placement verify.",
                arena_region,
                soc.name,
            )
            return
        expected = expected_window

        toolchain = ctx.config.target.toolchain
        resolved = symbol_address(
            binary_path,
            toolchain,
            _ARENA_SYMBOL,
            timeout_s=ctx.config.timeouts.binary_probe_s,
        )
        if resolved is None:
            log.debug(
                "Could not resolve %s address; skipping placement verify.",
                _ARENA_SYMBOL,
            )
            return

        address, _nm_type = resolved
        if expected.contains(address):
            log.info(
                "Placement verified: arena in %s at 0x%08X "
                "(0x%08X-0x%08X).",
                str(arena_region).upper(),
                address,
                expected.start,
                expected.end,
            )
            return

        actual = classify_address(address, windows)
        actual_label = str(actual) if actual else "an unmapped region"
        raise BuildError(
            f"Arena landed in {actual_label} (0x{address:08X}) but the memory "
            f"plan placed it in {str(arena_region).upper()} "
            f"(0x{expected.start:08X}-0x{expected.end:08X}).",
            hint=(
                f"The {toolchain} linker script for {soc.name} is not "
                f"relocating the arena section to {str(arena_region).upper()}. "
                "Check that the scatter/linker script collects the arena's "
                "section (e.g. '.sram_bss' for SRAM) into the intended region — "
                "this is the armclang SHARED_SRAM scatter-gap class of bug."
            ),
        )

