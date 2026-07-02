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
from ..placement import Placement
from ..platform import MemoryRange, soc_placement_ranges
from ..toolchain_probe import symbol_address

log = logging.getLogger("hpx")

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
        assert ctx.soc is not None  # narrowed by should_skip
        assert ctx.binary_path is not None
        assert ctx.arena_region is not None

        ranges = soc_placement_ranges(ctx.soc)
        expected = ranges.get(Placement(ctx.arena_region))
        if expected is None:
            log.debug(
                "No address range for %s on %s; skipping placement verify.",
                ctx.arena_region,
                ctx.soc.name,
            )
            return

        toolchain = ctx.config.target.toolchain
        resolved = symbol_address(
            ctx.binary_path,
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
                str(ctx.arena_region).upper(),
                address,
                expected.start,
                expected.end,
            )
            return

        actual = _classify(address, ranges)
        actual_label = actual.upper() if actual else "an unmapped region"
        raise BuildError(
            f"Arena landed in {actual_label} (0x{address:08X}) but the memory "
            f"plan placed it in {str(ctx.arena_region).upper()} "
            f"(0x{expected.start:08X}-0x{expected.end:08X}).",
            hint=(
                f"The {toolchain} linker script for {ctx.soc.name} is not "
                f"relocating the arena section to {str(ctx.arena_region).upper()}. "
                "Check that the scatter/linker script collects the arena's "
                "section (e.g. '.sram_bss' for SRAM) into the intended region — "
                "this is the armclang SHARED_SRAM scatter-gap class of bug."
            ),
        )


def _classify(address: int, ranges: dict[Placement, MemoryRange]) -> str | None:
    """Return the placement name whose range contains *address*, if any."""
    for placement, mrange in ranges.items():
        if mrange.contains(address):
            return str(placement)
    return None
