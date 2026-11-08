"""Check the linked interpreter arena against verified application intervals.

Custom targets retain their declared-range check. Unsupported layouts and
unreadable symbols produce no placement claim; a resolved out-of-range symbol
fails before deployment.
"""

from __future__ import annotations

import logging

from ..engines import EngineType
from ..errors import BuildError
from ..hostenv.toolchain_probe import symbol_address
from ..pipeline import PipelineContext
from ..placement import Placement
from ..platform import soc_placement_ranges

log = logging.getLogger("hpx")

#: Arena storage symbol emitted by the interpreter firmware template
#: (``main.cc.j2``).  Mangled to ``_ZL15g_arena_storage`` by C++ compilers;
#: matched as a suffix so both mangled and plain forms resolve.
_ARENA_SYMBOL = "g_arena_storage"


class VerifyPlacementStage:
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
        # interpreter arena guard does not apply.
        if ctx.config.engine.type == EngineType.HELIA_AOT:
            return True
        return False

    def run(self, ctx: PipelineContext) -> None:
        soc = ctx.resolved_soc
        binary_path = ctx.built_binary_path
        arena_region = ctx.planned_arena_region

        toolchain = ctx.config.target.toolchain
        if not soc.is_builtin:
            self._run_legacy(ctx, soc, arena_region)
            return
        decision = next(
            (v for v in ctx.run_metadata.linker_verifications if v.role == "profile"), None
        )
        layout = decision.layout if decision is not None else None
        if layout is None:
            log.info("Placement interval verification unavailable without a verified linker source")
            return
        if layout.soc_name != soc.name:
            raise BuildError("Verified linker source differs from the resolved SoC")
        entry = next((r for r in layout.regions if r.region is arena_region.region), None)
        if entry is None or entry.allowed is None:
            log.info("No verified application intervals for %s", arena_region)
            return
        resolved = symbol_address(
            binary_path,
            toolchain,
            _ARENA_SYMBOL,
            timeout_s=ctx.config.timeouts.binary_probe_s,
        )
        if resolved is None:
            log.debug(
                "Could not resolve %s address; placement verification unavailable", _ARENA_SYMBOL
            )
            return
        address, _nm_type = resolved
        sized = next(
            (
                value
                for value in (ctx.memory_symbols or ())
                if value.address == address
                and value.name.endswith(_ARENA_SYMBOL)
                and value.size > 0
            ),
            None,
        )
        size = sized.size if sized is not None else 1
        if any(r.start <= address and address + size <= r.end for r in entry.allowed):
            if sized is not None:
                log.info(
                    "Placement verified: arena in %s at 0x%08X (%d B)", arena_region, address, size
                )
            else:
                log.info(
                    "Arena start matches %s; full extent unavailable without a sized symbol",
                    arena_region,
                )
            return
        bounds = (
            ", ".join(f"[0x{r.start:08X},0x{r.end:08X})" for r in entry.allowed)
            or "no permitted intervals"
        )
        raise BuildError(
            f"Arena at 0x{address:08X} ({size} B checked) lies outside verified "
            f"{str(arena_region).upper()} application intervals: {bounds}.",
            hint=f"Check {layout.script.path} and the requested arena placement; "
            "an allocation cannot cross a reserved gap or fixed stack/heap region.",
        )

    def _run_legacy(self, ctx: PipelineContext, soc, arena_region) -> None:
        """Check against the user-declared placement table — used for
        custom SoCs, which the verified map does not cover but whose
        declared bases are still worth holding the linker to."""
        ranges = soc_placement_ranges(soc)
        expected = ranges.get(arena_region)
        if expected is None:
            log.debug(
                "No address range for %s on %s; skipping placement verify.",
                arena_region,
                soc.name,
            )
            return
        toolchain = ctx.config.target.toolchain
        resolved = symbol_address(
            ctx.built_binary_path,
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
                "Placement verified (declared ranges): arena in %s at 0x%08X.",
                str(arena_region).upper(),
                address,
            )
            return
        raise BuildError(
            f"Arena landed at 0x{address:08X} but the declared "
            f"{str(arena_region).upper()} range is "
            f"0x{expected.start:08X}-0x{expected.end:08X}.",
            hint=(
                f"The custom SoC's declared memory bases for {soc.name} do "
                "not contain the arena. Check the custom_socs memory "
                "declaration and the linker script."
            ),
        )
