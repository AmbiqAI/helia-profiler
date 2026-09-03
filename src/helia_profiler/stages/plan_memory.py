"""Plan memory: choose placement and validate against capacity.

Two responsibilities:

1. **Resolve placement** — translate optional ``model.arena_location`` /
    ``model.weights_location`` controls plus the SoC memory layout into
    concrete ``arena_region`` and ``weights_region``
   values written to ``ctx``.  These drive the section attributes the
   firmware template applies to ``model_data[]`` and ``g_arena[]``.

2. **Build / validate the memory plan** — produce a ``MemoryPlan`` on
   ``ctx.memory_plan`` describing how much of each SoC memory region will
   be consumed.  Engines that know their layout (heliaAOT) supply the
   plan directly via ``EngineArtifacts.memory_plan``; otherwise we
   synthesise a single-arena plan from arena/model sizes and the
   resolved placement.  Each region is then sized against the SoC's
   ``MemoryLayout`` and any overflow raises ``PlatformError`` with an
   actionable hint *before* firmware is built.

Auto policy (greedy fastest-fit, arena prioritized over weights):

* both fit in TCM → both in TCM
* arena fits in TCM, weights fit in SRAM → arena=TCM, weights=SRAM
* arena fits in TCM, weights need MRAM → arena=TCM, weights=MRAM
* arena needs SRAM → arena=SRAM, weights=MRAM
* arena needs MRAM → arena=MRAM, weights=MRAM (rare; arena cannot be
  truly placed in non-volatile MRAM, so this case fails validation)

``auto`` never falls back to PSRAM; PSRAM requires explicit opt-in
because of the runtime upload handshake.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..config import DEFAULT_ARENA_SIZE_BYTES
from ..errors import PlatformError
from ..engines import EngineType, get_adapter
from ..engines.base import ExecutorchArtifacts
from ..pipeline import PipelineContext
from ..placement import MemoryRegion, Placement, resolve_fastest_fit_placement
from ..config import Transport
from ..platform import MemoryLayout, PmuTier, SocDef, SocFamily
from ..results import ConsumerKind, MemoryConsumer, MemoryPlan, MemoryRegionUsage

if TYPE_CHECKING:
    from ..config import ProfileConfig

log = logging.getLogger("hpx")


# Mapping of MemoryPlan region names to MemoryLayout fields.
_REGION_FIELDS: dict[MemoryRegion, str] = {
    MemoryRegion.MRAM: "mram_kb",
    MemoryRegion.SRAM: "sram_kb",
    MemoryRegion.DTCM: "dtcm_kb",
    MemoryRegion.ITCM: "itcm_kb",
    MemoryRegion.PSRAM: "psram_kb",
}

# Logical region (used by ctx.{arena,weights}_region) → physical region
# (used in MemoryPlan / NSX layout).  ``Placement.TCM`` means DTCM here —
# ITCM is a code-only region and not eligible for arena/weights.
_LOGICAL_TO_PHYSICAL: dict[Placement, MemoryRegion] = {
    Placement.TCM: MemoryRegion.DTCM,
    Placement.SRAM: MemoryRegion.SRAM,
    Placement.MRAM: MemoryRegion.MRAM,
    Placement.PSRAM: MemoryRegion.PSRAM,
}


class PlanMemoryStage:
    @property
    def name(self) -> str:
        return "plan_memory"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        # 1. Resolve logical placement first (used by both AOT and
        #    interpreter paths for downstream firmware rendering).
        arena_region, weights_region = _resolve_placement(ctx)
        ctx.arena_region = arena_region
        ctx.weights_region = weights_region
        log.info(
            "Placement: arena=%s, weights=%s",
            arena_region,
            weights_region,
        )

        # 2. Build / select the memory plan, then append the hpx-owned
        #    consumers every firmware reserves regardless of engine (#133
        #    Phase 3) so the overflow check finally sees them.
        plan = self._select_plan(ctx)
        plan = _add_hpx_owned_consumers(plan, ctx)
        plan = self._apply_capacities(plan, ctx)
        self._validate(plan)

        ctx.memory_plan = plan
        ctx.run_metadata.memory_plan = plan

        log.info("Memory plan (%s):", plan.engine)
        for r in plan.regions:
            if r.capacity > 0 or r.used > 0:
                pct = (r.used * 100 / r.capacity) if r.capacity else 0
                log.info(
                    "  %-6s %7d / %7d B (%5.1f%%)",
                    r.region,
                    r.used,
                    r.capacity,
                    pct,
                )

    # ------------------------------------------------------------------
    # Plan construction
    # ------------------------------------------------------------------

    def _select_plan(self, ctx: PipelineContext) -> MemoryPlan:
        """Prefer the engine-supplied plan; synthesise one otherwise.

        A FAILED heliaAOT extraction gets a capacities-only plan (#133): a
        TFLM-shaped synthesis would book a ``tensor_arena`` and a
        ``model_flatbuffer`` no AOT binary has. Wrong numbers with no
        warning are worse than none.
        """
        artifacts = ctx.engine_artifacts
        if artifacts is not None and artifacts.memory_plan is not None:
            return artifacts.memory_plan
        if ctx.config.engine.type is EngineType.HELIA_AOT:
            log.warning(
                "heliaAOT did not supply a memory plan (extraction failed "
                "or fields were missing); the plan records capacities and "
                "hpx-owned consumers only — no arena/weights figures."
            )
            return MemoryPlan(engine=ctx.config.engine.type)
        return self._synthesise_plan(ctx)

    def _synthesise_plan(self, ctx: PipelineContext) -> MemoryPlan:
        """Build a single-arena plan for engines (tflm/heliaRT) that don't
        expose per-region allocations themselves.

        Uses the resolved ``ctx.arena_region`` and ``ctx.weights_region``
        for placement, so the plan reflects what the firmware template
        will actually emit.
        """
        engine_type = ctx.config.engine.type
        artifacts = ctx.engine_artifacts
        arena = int(ctx.config.model.arena_size or DEFAULT_ARENA_SIZE_BYTES)

        try:
            model_bytes = int(ctx.config.model.path.stat().st_size)
        except OSError:
            model_bytes = 0

        weight_phys = _LOGICAL_TO_PHYSICAL.get(
            Placement(ctx.weights_region) if ctx.weights_region else Placement.MRAM,
            MemoryRegion.MRAM,
        )
        arena_phys = _LOGICAL_TO_PHYSICAL.get(
            Placement(ctx.arena_region) if ctx.arena_region else Placement.TCM,
            MemoryRegion.DTCM,
        )

        region_map: dict[MemoryRegion, list[MemoryConsumer]] = {}

        def add(region: MemoryRegion, name: str, size: int, kind: str) -> None:
            if size > 0:
                region_map.setdefault(region, []).append(
                    MemoryConsumer(name=name, size=size, kind=ConsumerKind(kind))
                )

        if model_bytes > 0:
            add(
                weight_phys,
                "pte_program" if engine_type is EngineType.EXECUTORCH else "model_flatbuffer",
                model_bytes,
                "weights",
            )

        if engine_type is EngineType.EXECUTORCH and isinstance(artifacts, ExecutorchArtifacts):
            # The generated ExecuTorch runner owns several explicit buffers in
            # addition to the PTE memory-planned arena. Each buffer follows
            # the run's arena region unless its engine.config *_location
            # override places it elsewhere; account them where the firmware
            # actually puts them.
            def executorch_region(override: str | None) -> MemoryRegion:
                if override == "tcm":
                    return MemoryRegion.DTCM
                if override == "sram":
                    return MemoryRegion.SRAM
                return arena_phys

            # Every size below is a required positive int on
            # ExecutorchArtifacts (the adapter resolves each through
            # _positive_int), so none of them needs a fallback.
            add(
                executorch_region(artifacts.executorch_planned_arena_region),
                "planned_arena",
                artifacts.executorch_planned_arena_size,
                "arena",
            )
            add(
                executorch_region(artifacts.executorch_method_arena_region),
                "method_arena",
                artifacts.executorch_method_arena_size,
                "other",
            )
            add(
                executorch_region(artifacts.executorch_temporary_arena_region),
                "temporary_arena",
                artifacts.executorch_temporary_arena_size,
                "other",
            )
            add(
                executorch_region(artifacts.executorch_io_region),
                "input_buffer",
                artifacts.executorch_input_size,
                "other",
            )
            add(
                executorch_region(artifacts.executorch_io_region),
                "output_buffer",
                artifacts.executorch_output_size,
                "other",
            )
            # pmu_layer_records moved to _add_hpx_owned_consumers (#133
            # Phase 3): every engine reserves the array, not just this one.
        else:
            add(arena_phys, "tensor_arena", arena, "arena")

        regions = tuple(
            MemoryRegionUsage(
                region=name,
                capacity=0,  # filled by _apply_capacities
                used=sum(c.size for c in consumers),
                consumers=tuple(consumers),
            )
            for name, consumers in region_map.items()
        )

        return MemoryPlan(
            engine=engine_type,
            regions=regions,
            model_weight_bytes=model_bytes,
        )

    # ------------------------------------------------------------------
    # Capacity + validation
    # ------------------------------------------------------------------

    def _apply_capacities(
        self,
        plan: MemoryPlan,
        ctx: PipelineContext,
    ) -> MemoryPlan:
        """Fill in per-region capacities from the resolved SoC layout."""
        if ctx.soc is None:
            return plan

        layout: MemoryLayout = ctx.soc.memory

        by_region = {r.region.upper(): r for r in plan.regions}

        rebuilt: list[MemoryRegionUsage] = []
        for region_name, field in _REGION_FIELDS.items():
            cap_kb = int(getattr(layout, field, 0))
            cap_bytes = cap_kb * 1024
            existing = by_region.pop(region_name, None)
            if existing is not None:
                if existing.capacity not in (0, cap_bytes):
                    # An engine-supplied capacity (heliaAOT's own view of
                    # the part) disagrees with SocDef.memory. hpx's table
                    # wins, but silently resolving the disagreement hid a
                    # real signal (#133 Phase 3 survey).
                    log.warning(
                        "%s capacity: engine says %d B, SoC layout says "
                        "%d B — using the SoC layout.",
                        region_name,
                        existing.capacity,
                        cap_bytes,
                    )
                rebuilt.append(
                    MemoryRegionUsage(
                        region=region_name,
                        capacity=cap_bytes,
                        used=existing.used,
                        consumers=existing.consumers,
                    )
                )
            elif cap_bytes > 0:
                rebuilt.append(
                    MemoryRegionUsage(
                        region=region_name,
                        capacity=cap_bytes,
                        used=0,
                    )
                )

        for leftover in by_region.values():
            rebuilt.append(leftover)

        return MemoryPlan(
            engine=plan.engine,
            regions=tuple(rebuilt),
            model_weight_bytes=plan.model_weight_bytes,
            has_overflow=any(r.overflow for r in rebuilt),
        )

    def _validate(self, plan: MemoryPlan) -> None:
        for r in plan.regions:
            if r.capacity == 0 and r.used > 0:
                # overflow cannot fire on a 0-capacity region (custom SoC
                # declared without this memory); say so instead of
                # validating clean and failing at link (#179 review m10).
                log.warning(
                    "%s: %d B planned into a region with no declared "
                    "capacity — the overflow check cannot see this.",
                    r.region,
                    r.used,
                )
        offenders = [r for r in plan.regions if r.overflow]
        if not offenders:
            return

        lines = [
            f"  {r.region}: {r.used} B used > {r.capacity} B capacity "
            f"(over by {r.used - r.capacity} B)"
            for r in offenders
        ]
        detail = "\n".join(lines)
        first = offenders[0]

        hint = (
            f"{first.region} is over capacity.  Try one of:\n"
            "  * shrink the tensor arena (--arena-size);\n"
            "  * pick a less-aggressive placement\n"
            "    (--weights-location mram);\n"
            "  * move weights to PSRAM (--weights-location psram) if the\n"
            "    board has PSRAM;\n"
            "  * reduce model size (quantise / prune); or\n"
            "  * pick a larger-memory board."
        )
        raise PlatformError(
            f"Memory plan does not fit:\n{detail}",
            hint=hint,
        )


# ---------------------------------------------------------------------------
# hpx-owned consumers (#133 Phase 3)
# ---------------------------------------------------------------------------
#
# Sizes the firmware reserves that hpx decides HOST-SIDE, a priori — they
# belong in the PLAN (the decision record), and their absence was exactly
# how a plan could "fit" while the link failed. Every constant below is a
# frozen mirror of a template/vendor fact; the citation is the contract and
# tests/test_plan_memory.py pins the values so drift is a reviewed edit.

#: sizeof of the per-layer record each engine's firmware reserves,
#: kMaxLayers (= soc.pmu_max_ops) times over. 32-bit target ABI:
#:   TFLM/heliaRT: {const char* tag; uint32 counters[4]; bool} -> 24
#:     (hpx_pmu_profiler.h.j2:78-82)
#:   heliaAOT:     {uint32 counters[4]; bool} padded          -> 20
#:     (main_aot.cc.j2:61-64)
#:   ExecuTorch:   {12-byte OperatorEvent; uint32[4]; bool}   -> 32
#:     (main_executorch.cc.j2:73-77; nsx_executorch.h OperatorEvent)
PMU_RECORD_SIZE_BYTES: dict[EngineType, int] = {
    EngineType.TFLM: 24,
    EngineType.HELIA_RT: 24,
    EngineType.HELIA_AOT: 20,
    EngineType.EXECUTORCH: 32,
}

#: TFLM/heliaRT reserve the records INSIDE the HpxPmuProfiler object
#: (g_profiler) — the linked symbol is the whole object, records plus a
#: fixed header. ARMV8M_PMU parts: vptr 4 + nsx_pmu_config_t 196 +
#: counter-name pointers and state 52 = 252. DWT_ONLY parts render the
#: class WITHOUT the config struct or its include (hpx_pmu_profiler.h.j2's
#: has_armv8m_pmu gate), leaving vptr 4 + state 52 = 56 (#180).
_PROFILER_OBJECT_OVERHEAD: dict[PmuTier, int] = {
    PmuTier.ARMV8M_PMU: 252,
    PmuTier.DWT_ONLY: 56,
}

#: SEGGER RTT statics beyond the up buffer itself: the 16-byte default
#: down buffer (SEGGER_RTT_ConfDefaults.h BUFFER_SIZE_DOWN) plus the
#: control block (SEGGER_RTT.h: acID[16] + 2 ints + 3 up + 3 down ring
#: descriptors of 24 B each = 168). hpx never overrides MAX_NUM_*_BUFFERS.
RTT_FIXED_OVERHEAD_BYTES = 16 + 168

#: usb_cdc transport statics: usb_tx_buf[4096] + usb_rx_buf[1024]
#: (_usb_config.j2:2-3; NSX_USB_MIN_CDC_RX_BUFSIZE). The small config
#: struct is deliberately ignored as noise.
USB_CDC_BUFFER_BYTES = 4096 + 1024

#: Boot stack, keyed on the STARTUP DECLARATION, not STACK_SIZE: AP4/AP5
#: startup files declare g_pui32Stack[STACK_SIZE] as uint32 (4096 words =
#: 16 KB); the AP3 family hardcodes g_pui32Stack[1024] = 4 KB and ignores
#: STACK_SIZE entirely (nsx-core startup_gcc.c per part; #133 Phase 3
#: survey). armlink reserves the same amounts as fixed scatter regions.
_BOOT_STACK_BYTES: dict[SocFamily, int] = {
    SocFamily.AP3: 4_096,
    SocFamily.AP4: 16_384,
    SocFamily.AP5: 16_384,
}


def _default_bss_region(family: SocFamily) -> MemoryRegion:
    """Where an unattributed static (plain ``.bss``) lands per family.

    AP3 is the exception (#179 review B-1): its gcc script sends ``.bss``
    to RWMEM — main SRAM at 0x10011000 — because TCM is only 64 KB
    (apollo3p/gcc/linker_script.ld). AP4/AP5 default ``.bss`` into
    MCU_TCM (DTCM)."""
    return MemoryRegion.SRAM if family is SocFamily.AP3 else MemoryRegion.DTCM


#: Parts whose nsx_mem.h sets NSX_MEM__HAS_SRAM_BSS=0 within a family
#: that otherwise has it: AM_PART_APOLLO5A/5B (nsx_mem.h:92-99) — their
#: linker scripts have no .sram_bss, so THAT macro expands to nothing and
#: falls to plain .bss -> MCU_TCM (DTCM). NB apollo5b still HAS
#: NSX_MEM_SRAM (".shared" -> SRAM): the two macros diverge on exactly
#: this part, which is why the records region is keyed on which macro the
#: ENGINE's template uses (#180).
_NO_SRAM_BSS_SOCS = frozenset({"apollo5b"})


def _nsx_mem_sram_region(soc: SocDef) -> MemoryRegion:
    """Where ``NSX_MEM_SRAM`` (initialized ``.shared``) lands — the macro
    TFLM/heliaRT's g_profiler uses. SRAM on every registered part: AP4/AP5
    (incl. apollo5b) via ``.shared``; AP3 via the documented fallback to
    the default data region, which on AP3 IS main SRAM."""
    return MemoryRegion.SRAM


def _nsx_mem_sram_bss_region(soc: SocDef) -> MemoryRegion:
    """Where ``NSX_MEM_SRAM_BSS`` lands — the macro heliaAOT/ExecuTorch's
    g_layers uses. ``.sram_bss`` -> SRAM on AP4 and most AP5 parts; the
    fallbacks diverge: AP3's plain ``.bss`` is main SRAM, apollo5b's is
    MCU_TCM -> DTCM."""
    if soc.name in _NO_SRAM_BSS_SOCS:
        return MemoryRegion.DTCM
    return MemoryRegion.SRAM


def _usb_region(family: SocFamily) -> MemoryRegion:
    """USB CDC buffers carry no NSX_MEM attribute -> plain ``.bss``."""
    return _default_bss_region(family)


def _rtt_region(family: SocFamily) -> MemoryRegion:
    """Where the RTT statics land per the SEGGER_RTT_SECTION snippet hpx
    writes (firmware/__init__.py): AP4 pins ``.sram_bss`` -> SRAM; AP3
    reaches SRAM through plain ``.bss`` (its default .bss IS main SRAM);
    the AP5 family (incl. apollo330P) leaves the default ``.bss`` ->
    DTCM — on exactly the parts where DTCM is scarcest."""
    return MemoryRegion.SRAM if family in (SocFamily.AP3, SocFamily.AP4) else MemoryRegion.DTCM


def _stack_region(family: SocFamily) -> MemoryRegion:
    """gcc's ``.stack`` goes to MCU_TCM (DTCM) everywhere except the AP3
    family, whose STACKMEM slot at 0x10010000 sits inside the verified
    SRAM window (platform/memory_map.py)."""
    return MemoryRegion.SRAM if family is SocFamily.AP3 else MemoryRegion.DTCM


def _add_hpx_owned_consumers(plan: MemoryPlan, ctx: PipelineContext) -> MemoryPlan:
    """Append the engine-independent consumers hpx itself configures.

    Additive and idempotent-by-name: a consumer name already present in
    the plan (e.g. a future engine-supplied records entry) is left alone.
    """
    soc = ctx.soc
    if soc is None:
        return plan
    family = soc.family
    engine_type = ctx.config.engine.type

    additions: list[tuple[MemoryRegion, MemoryConsumer]] = []

    record_size = PMU_RECORD_SIZE_BYTES.get(engine_type)
    if record_size is not None:
        records_bytes = int(soc.pmu_max_ops) * record_size
        if engine_type in (EngineType.TFLM, EngineType.HELIA_RT):
            # The records live inside g_profiler (NSX_MEM_SRAM); book the
            # whole object — both tier headers are derivable. NB the plan
            # describes the PROFILE binary (the power binary's records
            # fall to plain .bss under power_only=1) — same scope as
            # binary_sections and memory_regions.
            records_bytes += _PROFILER_OBJECT_OVERHEAD.get(soc.pmu_tier, 0)
            records_region = _nsx_mem_sram_region(soc)
        else:
            # AOT/ET declare g_layers under NSX_MEM_SRAM_BSS.
            records_region = _nsx_mem_sram_bss_region(soc)
        additions.append(
            (
                records_region,
                MemoryConsumer(
                    name="pmu_layer_records",
                    size=records_bytes,
                    kind=ConsumerKind.OTHER,
                ),
            )
        )

    transport = ctx.config.target.transport
    if transport == Transport.RTT:
        from ..firmware import rtt_buffer_size_up

        up = rtt_buffer_size_up(
            ctx.config.target.toolchain,
            transport,
            ctx.config.target.rtt_buffer_size_up,
        )
        additions.append(
            (
                _rtt_region(family),
                MemoryConsumer(
                    name="rtt_buffers",
                    size=up + RTT_FIXED_OVERHEAD_BYTES,
                    kind=ConsumerKind.OTHER,
                ),
            )
        )
    elif transport == Transport.USB_CDC:
        additions.append(
            (
                _usb_region(family),
                MemoryConsumer(
                    name="usb_buffers", size=USB_CDC_BUFFER_BYTES, kind=ConsumerKind.OTHER
                ),
            )
        )

    # SWO/UART transports reserve no comparable static buffers — their
    # absence here is deliberate scope, not an oversight.
    additions.append(
        (
            _stack_region(family),
            MemoryConsumer(
                # Loud KeyError if a new SocFamily appears unkeyed — that
                # is a characterization task, not a default.
                name="boot_stack",
                size=_BOOT_STACK_BYTES[family],
                kind=ConsumerKind.STACK,
            ),
        )
    )

    existing_names = {c.name for r in plan.regions for c in r.consumers}
    by_region: dict[MemoryRegion, MemoryRegionUsage] = {
        MemoryRegion(str(r.region).upper()): r for r in plan.regions
    }
    for region, consumer in additions:
        if consumer.name in existing_names or consumer.size <= 0:
            continue
        current = by_region.get(region)
        if current is None:
            by_region[region] = MemoryRegionUsage(
                region=region,
                capacity=0,
                used=consumer.size,
                consumers=(consumer,),
            )
        else:
            by_region[region] = MemoryRegionUsage(
                region=current.region,
                capacity=current.capacity,
                used=current.used + consumer.size,
                consumers=current.consumers + (consumer,),
            )

    return MemoryPlan(
        engine=plan.engine,
        regions=tuple(by_region.values()),
        model_weight_bytes=plan.model_weight_bytes,
        has_overflow=plan.has_overflow,
    )


# ---------------------------------------------------------------------------
# Placement resolver
# ---------------------------------------------------------------------------


def _resolve_placement(ctx: PipelineContext) -> tuple[Placement, Placement]:
    """Resolve ``(arena_region, weights_region)`` from the requested
    placement policy and any explicit per-object overrides.

    Returns a pair of :class:`Placement` members.

    Raises ``PlatformError`` if the user requested a region the SoC
    doesn't have (e.g. ``tcm`` on a board with no DTCM, or ``psram`` on a
    board without PSRAM).
    """
    cfg = ctx.config
    soc = ctx.soc
    # The engine adapter owns engine-specific placement policy.  Stage 2
    # populates ctx.engine_adapter; for the rare early-call path where
    # soc/adapter aren't yet available we fall back to a fresh adapter
    # via the registry.
    adapter = ctx.engine_adapter or get_adapter(cfg.engine.type)

    # Capacity probe (in bytes).  If soc is None (very early call), we
    # treat all regions as unbounded; the validate pass will catch real
    # overflow later.
    tcm_cap = (soc.memory.dtcm_kb * 1024) if soc else (1 << 31)
    sram_cap = (soc.memory.sram_kb * 1024) if soc else (1 << 31)
    psram_cap = (soc.memory.psram_kb * 1024) if soc else 0

    arena_size = int(cfg.model.arena_size or DEFAULT_ARENA_SIZE_BYTES)
    try:
        model_size = int(cfg.model.path.stat().st_size)
    except OSError:
        model_size = 0

    arena_region: Placement
    weights_region: Placement

    # Engine-specific auto policy (e.g. AOT pins arena=TCM, weights=MRAM).
    if (
        engine_default := adapter.default_auto_placement(tcm_cap=tcm_cap, sram_cap=sram_cap)
    ) is not None:
        arena_region, weights_region = engine_default
    else:
        arena_region, weights_region = resolve_fastest_fit_placement(
            arena_size=arena_size,
            weights_size=model_size,
            tcm_cap=tcm_cap,
            sram_cap=sram_cap,
        )

    arena_region, weights_region = _apply_explicit_overrides(
        cfg,
        arena_region,
        weights_region,
        tcm_cap=tcm_cap,
        psram_cap=psram_cap,
    )

    return (arena_region, weights_region)


def _apply_explicit_overrides(
    cfg,
    arena_region: Placement,
    weights_region: Placement,
    *,
    tcm_cap: int,
    psram_cap: int,
) -> tuple[Placement, Placement]:
    requested_arena = cfg.model.arena_location
    requested_weights = cfg.model.weights_location

    if requested_arena == Placement.TCM and tcm_cap == 0:
        raise PlatformError(
            f"model.arena_location=tcm requested, but board {cfg.target.board} has no DTCM.",
            hint="Use --arena-location sram, or pick a board with DTCM.",
        )

    if requested_arena == Placement.PSRAM and psram_cap == 0:
        raise PlatformError(
            f"model.arena_location=psram requested, but board {cfg.target.board} has no PSRAM.",
            hint="Use --arena-location tcm | sram, or pick a PSRAM-capable board.",
        )

    if requested_weights == Placement.TCM and tcm_cap == 0:
        raise PlatformError(
            f"model.weights_location=tcm requested, but board {cfg.target.board} has no DTCM.",
            hint="Use --weights-location sram | mram, or pick a board with DTCM.",
        )

    if requested_weights == Placement.PSRAM and psram_cap == 0:
        raise PlatformError(
            f"model.weights_location=psram requested, but board {cfg.target.board} has no PSRAM.",
            hint="Use --weights-location tcm | sram | mram, or pick a PSRAM-capable board.",
        )

    if requested_arena is not None:
        arena_region = Placement(requested_arena)
    if requested_weights is not None:
        weights_region = Placement(requested_weights)

    return arena_region, weights_region
