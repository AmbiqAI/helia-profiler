"""Typed capability records for platform targets.

Capabilities turn SoC-family policy into explicit, typed data so that consumers
outside this package read a named field (``soc.capabilities.reset...``) instead
of branching on :class:`~helia_profiler.platform.soc.SocFamily`.  All family
conditionals live *here*, at construction time; every value is identical to the
family branch it replaced.

The records are intentionally small and shaped around what consumers actually
ask for (reset strategy, transport probe/USB rules, cache/SSRAM memory rules,
power-capture pins), not a speculative mirror of the hardware.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping

from ..placement import Placement
from .soc import SocFamily

if TYPE_CHECKING:
    from .soc import SocDef

# Reset-strategy tokens.  These equal the corresponding ``ResetStrategy`` /
# ``ResetAction`` values in :mod:`helia_profiler.target.lifecycle`; kept as
# plain strings here so the platform package does not import the lifecycle layer
# (which imports the platform package).
_RESET_DEBUG = "debug_reset"
_RESET_DEBUG_THEN_SWPOI = "debug_reset+swpoi_reset"


@dataclass(frozen=True)
class ResetCapabilities:
    """How a target should be reset before a power-capture phase.

    ``default_power_reset_strategy`` is the ``auto`` policy resolution owned by
    ``target.lifecycle`` — ``debug_reset+swpoi_reset`` on Apollo5 (the RSTGEN
    SWPOI deep reset also clears PMU/power state), ``debug_reset`` on
    Apollo3/Apollo4.  The value equals a
    :class:`helia_profiler.target.lifecycle.ResetStrategy` member.

    There is deliberately no per-family "needs lock-step for gated power"
    flag here.  There used to be (``requires_lockstep_for_gated_power``, set
    for Apollo5 only, justified by ``debug_reset+swpoi_reset`` spending
    several seconds in *two* sequential JLinkExe invocations).  The hazard it
    described is not family-specific: without lock-step the firmware
    free-runs its measured window straight out of reset, so *any* host-side
    reset latency can race the gate.  Apollo4 Blue Plus reproduced the same
    degradation on a single-invocation ``debug_reset`` (issue #114), so the
    default now keys on the wiring and the capture mode instead -- see
    :attr:`helia_profiler.config.PowerConfig.lockstep_resolved`, which is the
    single place that policy lives.
    """

    default_power_reset_strategy: str
    supports_swpoi: bool


@dataclass(frozen=True)
class TransportCapabilities:
    """Transport-related policy that used to be a SoC-family branch."""

    #: DWT->CYCCNT lives in the core debug power domain on the Cortex-M4F parts
    #: (Apollo3/3P, Apollo4/4P/4L); the released UART/USB readers must hold a
    #: probe attached for the whole capture or per-layer cycles read back 0.
    requires_attached_probe_for_cycles: bool
    #: Whether nsx-ambiq-usb supports this SoC (gates the usb_cdc transport).
    has_usb: bool
    #: Fixed SWO/ITM trace reference clock (MHz) when the TPIU is NOT core-clocked.
    swo_trace_clock_mhz: int | None
    #: True when the SWO baud follows the selected CPU clock (Apollo4/5).
    swo_core_clocked: bool


@dataclass(frozen=True)
class MemoryCapabilities:
    """Memory/cache policy that used to be a SoC-family branch."""

    #: Cache-coherent Cortex-M55 (Apollo5) parts have a CPU D-cache and need
    #: explicit maintenance around host-shared RTT buffers.
    has_dcache: bool
    #: Apollo5 parts expose the 3 MB shared SSRAM power domain that firmware
    #: powers on for SRAM-resident arenas.
    has_shared_ssram_power_domain: bool
    #: Physical base address of each arena/weights-eligible placement region.
    #: Sizes come from the SoC ``MemoryLayout``; only the bases are family-wide.
    placement_bases: Mapping[Placement, int]
    #: Address the NSX build programs application images at (the ``LoadFile``
    #: address in its generated ``flash_cmds.jlink``) -- the first MRAM/flash
    #: address above the part's bootloader-reserved region, NOT the MRAM
    #: *region* base in ``placement_bases``.  Resolved by
    #: :func:`resolve_app_flash_load_addr`; ``None`` when no tier there has a
    #: value it can stand behind for this part -- an unmapped family, or a
    #: ``target.custom_socs`` part that stated no address and named no
    #: ``based_on``.  The J-Link flash fallback refuses to guess an address then.
    app_flash_load_addr: int | None


@dataclass(frozen=True)
class ClockCapabilities:
    """Clock/perf policy that used to be a SoC-family branch."""

    #: Base CPU clock (MHz) above which the firmware must enable burst directly
    #: via the AmbiqSuite HAL because NSX's perf-mode switch is a no-op
    #: (Apollo3/3P TurboSPOT).  ``None`` means NSX handles perf switching.
    direct_burst_base_mhz: int | None

    #: Timer source the firmware uses to time the silent clean/power window.
    #: ``"stimer"`` (Apollo5): DWT->CYCCNT lives in the CoreSight debug power
    #: domain (PD_DBG) on this family, so timing the window with it forces
    #: PD_DBG to stay powered for the whole measured window — a real,
    #: measurable current cost vs the reference baseline (PD_DBG never
    #: powered).
    #: STIMER (XTAL 32.768 kHz) is clock-mode- and debug-domain-independent,
    #: so it can be read with PD_DBG off.  ``"dwt"`` (Apollo3/Apollo4): DWT
    #: is *not* cheaper to hold there -- it lives in the core debug power
    #: domain on those Cortex-M4F parts too (see
    #: ``SocCapabilities.requires_attached_probe_for_cycles``).  What makes
    #: DWT usable is that the transport-attached profile binary runs with a
    #: debugger asserting CDBGPWRUPREQ, which keeps that domain alive for
    #: free.  A *free-running* binary has no such guarantee -- see the
    #: override note below.
    #:
    #: This is the family's *preference*, not the final choice.  It is the
    #: timer the *transport-attached profile* binary uses; the dedicated power
    #: binary resolves its own via
    #: :attr:`SocCapabilities.power_window_timer`, which overrides this to
    #: STIMER wherever that binary cannot rely on the debug domain being
    #: readable.  The invariant is pinned by
    #: ``test_window_is_never_timed_by_a_domain_the_binary_powers_down`` and
    #: ``test_free_running_power_binary_never_times_the_window_with_dwt``.
    clean_window_timer: str
    #: Whether the firmware should call am_hal_debug_disable()/enable() around
    #: the *default* ``infer`` clean-window probe (not just the opt-in
    #: ``busy_loop`` probe).  True only where ``clean_window_timer`` is
    #: debug-domain-independent (Apollo5) so gating PD_DBG off cannot freeze
    #: the in-window timer.
    #:
    #: Inert without ``has_armv8m_pmu``: both am_hal_debug_disable() call
    #: sites sit inside that guard, so setting this on a Cortex-M4 family
    #: renders nothing.  AP4 needs no in-window gating regardless --
    #: ``broad_peripheral_shutdown`` disables the same domain earlier and
    #: holds it off longer, which is strictly stronger.
    gate_debug_domain_in_window: bool
    #: Mirrors AutoDeploy's ns_power_down_peripherals(): AP4's implementation
    #: explicitly powers down IOM/UART/ADC/MSPI(-when-unused)/GFX/DISP/USB/
    #: PDM/I2S/SDIO/AUDADC/Crypto/VCOMP and the DEBUG power domain at boot;
    #: AP3's and AP5's implementations are near-empty (AP3:
    #: ns_power_down_peripherals() is a no-op; AP5 only clears XTAL/VCOMP) --
    #: those families already read close to the reference baseline without
    #: this,
    #: so this is scoped to AP4 only rather than applied everywhere
    #: speculatively (see AGENTS.md AP4 power-parity investigation, 2026-07).
    broad_peripheral_shutdown: bool
    #: Mirrors AutoDeploy's ns_power_platform_config(): on every AP5-family
    #: run (both AP510 and AP330P -- confirmed identical between
    #: neuralspot's apollo5/ns_power.c and apollo330/ns_power.c) AutoDeploy
    #: unconditionally disables the CRYPTO and OTP power domains and the
    #: voltage comparator (VCOMP) before running the model, since none of
    #: MLPerf-Tiny-style inference needs them. hpx never did this on any
    #: board -- confirmed by an audit showing NSX's own nsx_system_init()
    #: only *transiently* powers CRYPTO/OTP on/off during the SWO
    #: DCU-unlock handshake, never leaving them off persistently. This is
    #: deliberately narrower than ``broad_peripheral_shutdown`` (no
    #: IOM/UART/GFX/etc, no full-SRAM power-off -- those are either
    #: AP4-specific already-validated behavior or belong in extreme_mode,
    #: not a normal-use default) and only touches domains no user-facing
    #: hpx feature (any transport, any engine) ever needs powered.
    crypto_otp_shutdown: bool


@dataclass(frozen=True)
class SocCapabilities:
    """Bundle of the typed capability records for one SoC."""

    reset: ResetCapabilities
    transport: TransportCapabilities
    memory: MemoryCapabilities
    clock: ClockCapabilities

    @property
    def power_window_timer(self) -> str:
        """Timer the *dedicated power binary* must use for its measured window.

        The question is not "which clock is nicest" but "can this binary read
        ``DWT->CYCCNT`` while the window is open".  There are two independent
        ways the answer is no, and either one is sufficient:

        * The binary powers the CoreSight debug domain down itself —
          ``clock.broad_peripheral_shutdown`` disables
          ``AM_HAL_PWRCTRL_PERIPH_DEBUG`` (AP4 family, see
          ``_peripheral_power_down.j2``).
        * Nothing holds that domain up — on the Cortex-M4F families
          ``transport.requires_attached_probe_for_cycles`` records that DWT
          only stays powered while a debugger asserts ``CDBGPWRUPREQ``, a
          signal firmware cannot set.  The dedicated power binary free-runs
          unwatched once flashed (WP4), so no debugger is holding it.

        Either way an in-window DWT read is frozen or garbage, the accumulated
        cycle count is meaningless, and the terminal report's ``elapsed_us`` —
        derived from it — is wrong.  What that costs depends on who measures:
        with an on-device monitor (internal mode) that duration is also the
        denominator for average power and current, so both are scaled by the
        same error and only the hardware-integrated energy and charge survive;
        with an external instrument the host owns those numbers and only
        ``elapsed_us`` is affected.  STIMER runs off its own 32.768 kHz XTAL
        and is readable in both situations, so it is the answer whenever DWT is
        not trustworthy.

        The two mechanisms are kept as separate disjuncts rather than
        collapsed: AP4 happens to satisfy both, but a future family could
        acquire the broad shutdown without being Cortex-M4F, or vice versa.

        Consumed by ``main.cc.j2`` / ``main_aot.cc.j2`` through
        ``FirmwareRenderContext``; this is the single place the *per-SoC*
        predicate lives, so the two templates cannot drift apart (the class of
        bug that produced the original AP4 miss).

        One thing deliberately stays out of this property: the opt-in
        ``clean_window_probe: busy_loop`` diagnostic is pinned to STIMER on
        every family, power binary or not.  That is *not* because DWT is
        always unreadable for it -- on an AP3/AP4 *profile* binary it is
        readable, since ``am_hal_debug_disable()`` renders only under
        ``has_armv8m_pmu`` and a debugger is attached anyway.  It is one clock
        for every case by choice, trading DWT's cycle resolution for STIMER's
        ~30.5 us tick (negligible against a window measured in milliseconds to
        seconds) to avoid a second code path whose only reachable
        configuration is the one that does not need it.  The case that forces
        the issue is the AP3/AP4 *power* binary, where the calibration pass
        that sizes the loop was reading an already-dead DWT (issue #112).

        Either way it is a property of the *probe*, not of the SoC, so the
        templates apply it on top of this answer rather than this property
        second-guessing a profiling setting it is not given.
        """
        if self.clock.clean_window_timer == "stimer":
            return "stimer"
        if (
            self.clock.broad_peripheral_shutdown
            or self.transport.requires_attached_probe_for_cycles
        ):
            return "stimer"
        return "dwt"

    @property
    def clean_window_needs_probe_attach(self) -> bool:
        """Whether the *profile* binary must see the host attach before it may
        open its clean window.

        The sibling :attr:`power_window_timer` asks "can the dedicated power
        binary read DWT at all"; this asks the narrower question the
        transport-attached profile binary faces: *when* is DWT readable.  It
        keeps DWT (finer resolution, no XTAL dependency) precisely because a
        debugger holds the CoreSight debug domain up for it -- but the host
        does not hold that probe continuously across a run.  The J-Link reset
        is a separate ``JLinkExe`` subprocess; once it exits nothing asserts
        ``CDBGPWRUPREQ`` until the pylink attach completes, and for that span
        ``DWT->CYCCNT`` simply stops advancing.  Any per-iteration delta taken
        across it silently loses exactly that span, so the window reads SHORT
        -- never long, and never zero, which is why it looks plausible.

        Measured on Apollo4 Blue Plus KBR (#121): two of five otherwise
        identical runs lost ~204 ms of a ~950 ms window, 21% low, while the
        later profiled loop -- which runs with the host fully attached -- held
        at 875-876 us in all five.  Across 7 runs the missing cycles regress on
        that run's own ``sbl_settle + attach + api_probe`` host phases at
        r = 0.996, slope 1.04.  Runs whose firmware happened to burn >= 500 ms
        before the window lost 0-1 ms: the exposure is a race against boot, not
        a constant.

        True exactly when the window is DWT-timed AND DWT depends on an
        attached probe.  Both conjuncts are load-bearing: Apollo5 times the
        window with STIMER (its own XTAL, indifferent to the probe), and a
        hypothetical DWT family that kept the domain up unaided would need no
        wait either.

        This is the predicate only.  Whether a given build can *act* on it
        depends on the transport having an observable host-attach signal --
        RTT's up-buffer drain, see ``_clean_window_attach_wait.j2``.  Builds
        that cannot wait still carry the ``HPX_CLEAN_STALLED_ITERS`` detector,
        so an unfixed stall surfaces as a validity issue instead of a
        plausible-looking number.
        """
        return (
            self.clock.clean_window_timer == "dwt"
            and self.transport.requires_attached_probe_for_cycles
        )


# Base addresses of the arena/weights-eligible memory regions, per SoC family.
# Sizes come from each SoC's ``MemoryLayout``; only the bases are family-wide.
# DTCM and SRAM are contiguous on AP4/AP5 (SRAM begins right after DTCM), but
# the bases are listed explicitly rather than derived so the mapping stays
# obvious and robust to layout changes.
#
# AP3 does not share a single family base map the way AP4/AP5 do. apollo3p
# (Blue Plus) has a real 64 KB low-latency TCM at 0x10000000 (arena-eligible
# via NSX_MEM_FAST_BSS's dedicated `.tcm_bss` section, nsx-ambiq-sdk#29), a
# separate 700 KB main SRAM "RWMEM" at 0x10011000 (default .bss/.data home),
# and "MRAM" is read-only NOR flash (XIP) at 0x0000C000 used for weights.
# apollo3 (Blue) instead has a flat 384 KB SRAM at 0x10000000 and no separate
# TCM — it is not currently a registered target, so the AP3 entry below
# encodes apollo3p only.
_FAMILY_MEMORY_BASES: dict[SocFamily, dict[Placement, int]] = {
    SocFamily.AP3: {
        Placement.TCM: 0x10000000,  # real 64 KB low-latency TCM (NSX_MEM_FAST_BSS, nsx-ambiq-sdk#29)
        Placement.SRAM: 0x10011000,  # RWMEM main SRAM (default .bss/.data home)
        Placement.MRAM: 0x00000000,  # NOR flash XIP (ROMEM @ 0x0000C000), weights
    },
    SocFamily.AP4: {
        Placement.TCM: 0x10000000,  # DTCM
        Placement.SRAM: 0x10060000,  # SHARED_SRAM (right after 384 KB DTCM)
        Placement.MRAM: 0x00000000,  # MRAM (XIP)
        Placement.PSRAM: 0x60000000,
    },
    SocFamily.AP5: {
        Placement.TCM: 0x20000000,  # DTCM
        Placement.SRAM: 0x20080000,  # SSRAM (right after 512 KB DTCM)
        Placement.MRAM: 0x00000000,  # MRAM (XIP)
        Placement.PSRAM: 0x60000000,
    },
}


def _family_placement_bases(family: SocFamily) -> Mapping[Placement, int]:
    """Return the frozen placement-base map for *family* (empty if unknown)."""
    return MappingProxyType(dict(_FAMILY_MEMORY_BASES.get(family, {})))


# App-image flash (``LoadFile``) address per family: ``NSX_SEGGER_PF_ADDR`` from
# nsx-ambiq-sdk's per-SoC facts (``cmake/socs/facts/*.cmake``) -- the exact
# address the NSX-generated ``flash_cmds.jlink`` recipes program, i.e. the first
# MRAM/flash address above each part's bootloader-reserved region, NOT the MRAM
# region base.  Verified 2026-08 against the facts file of every registered SoC
# (apollo3p; apollo4p/4l; apollo510/510b/5b/330P) plus real generated recipes,
# and cross-checked against the AP5 linker script's MCU_MRAM origin (see the
# apollo330P memory note in soc.py).  Keying by family is safe today: no
# registered family mixes addresses across its parts.  Two unregistered parts
# also agree with their families, from the sources that exist for each:
# apollo510L from its facts file, apollo4b (which has no facts file) from a
# real generated recipe.
#
# Each value is whichever linker layout that part's DEFAULT NSX_LINKER_PROFILE
# resolves to -- not "the SBL layout" as a blanket rule, because that differs
# per part.  For the Apollo families here it happens to be SBL: `default` and
# `itcm` are both sbl-based, and the nbl script (which would move AP5 MRAM to
# 0x00400000) is not selectable from hpx.  Do NOT generalize that to new parts;
# atomiq110 below defaults to *nbl*.  Keying is also coarser than NSX's own SoC
# family axis -- NSX treats apollo330P and atomiq110 as separate families -- so
# a part whose flash window departs from its family needs a per-SoC entry.
#
# "Verified against every registered SoC" is the whole warrant for keying by
# family, and it does not extend past the registry: a ``target.custom_socs``
# part was never checked against any facts file, so it does NOT read this table
# (see ``resolve_app_flash_load_addr``).  Such a part states its own address or
# inherits one from the ``based_on`` part it was derived from.
_FAMILY_APP_FLASH_LOAD_ADDR: dict[SocFamily, int] = {
    SocFamily.AP3: 0x0000C000,
    SocFamily.AP4: 0x00018000,
    SocFamily.AP5: 0x00410000,
}

# Per-SoC overrides, which win over the family baseline for BUILT-IN SoCs only
# (see ``resolve_app_flash_load_addr``).  PR #98 adds the same shape for placement
# bases (``_SOC_MEMORY_BASES``); it is not on this branch yet, so do not expect
# to find it by grep until that lands.
#
# atomiq110 is the motivating case and is NOT an Apollo5 part.  It heads a
# separate Atomiq series (binned AP6 internally; the eventual family tag is
# expected to be something like AT1).  ``SocFamily`` has no such member yet, so
# it is provisionally tagged AP5 on the strength of the shared Cortex-M55/MVE
# core -- a placeholder for the core tier, not a claim about its memory map.
#
# 0x22000000 is the **nbl** (no-bootloader) origin, and it is correct because
# nsx's atomiq110.cmake makes nbl the default profile: the FPGA realization is
# flashed straight over J-Link with no secure bootloader.  Its *sbl* scripts sit
# at 0x22010000 and nsx keeps them "for a future silicon AT110" -- so when real
# AT110 silicon arrives this value very likely becomes 0x22010000.  Do not
# "correct" it to the sbl address for the FPGA part.  When a real Atomiq family
# member is added, move this there and re-check every other AP5-keyed
# capability for the same placeholder problem.
_SOC_APP_FLASH_LOAD_ADDR: dict[str, int] = {
    "atomiq110": 0x22000000,
}


def resolve_app_flash_load_addr(soc: SocDef) -> int | None:
    """Resolve *soc*'s app-image flash address from the three tiers below.

    1. ``soc.app_flash_load_addr`` -- what the ``SocDef`` itself states.  Wins
       over everything: it is the only tier a ``target.custom_socs`` entry can
       write, and a user describing their own silicon outranks any table here.
    2. ``_SOC_APP_FLASH_LOAD_ADDR`` -- the per-SoC override, for parts whose
       flash window departs from their family's.
    3. ``_FAMILY_APP_FLASH_LOAD_ADDR`` -- the family baseline.

    Tiers 2 and 3 apply to **built-in** ``SocDef``s only, by
    :attr:`~helia_profiler.platform.soc.SocDef.is_builtin` (which owns the
    definition of "built-in" and why it is not a bare origin check).  Two
    separate reasons for the gate itself, both load-bearing:

    * Tier 2 is keyed by name, and ``target.custom_socs`` lets a user pick any
      name -- those entries replace built-ins in the merged registry, so a name
      match alone would let a user-chosen string silently outrank the ``family``
      that same user declared, handing their part an address belonging to
      something else entirely (df34b6e).
    * Tier 3 is only as good as its verification, and that verification is
      per-part: the table's addresses were checked against the NSX facts file
      of every *registered* SoC.  Nothing was checked for a part hpx has never
      seen.  Worse, ``SocFamily`` here is a core-tier axis coarser than NSX's
      own -- ``AP5`` covers apollo330P and (provisionally) atomiq110, which
      NSX treats as separate families and which load 0x00410000 and
      0x22000000 respectively.  Reading a flash offset out of a tag that only
      claims "Cortex-M55" is a guess, and a *plausible* one, which is the
      dangerous kind: it is likely enough to be accepted by the part and land
      the image at the wrong offset.

    So a custom SoC resolves to ``None`` unless it states an address (directly
    or inherited from the ``based_on`` part it was derived from, which
    ``platform.custom`` resolves through this same function).  ``None`` is a
    real answer meaning "nobody knows"; the J-Link flash fallback refuses to
    program rather than guess.
    """
    if soc.app_flash_load_addr is not None:
        return soc.app_flash_load_addr
    if not soc.is_builtin:
        return None
    override = _SOC_APP_FLASH_LOAD_ADDR.get(soc.name)
    if override is not None:
        return override
    return _FAMILY_APP_FLASH_LOAD_ADDR.get(soc.family)


def build_soc_capabilities(soc: SocDef) -> SocCapabilities:
    """Resolve the typed capability records for *soc*.

    This is the single place SoC-family policy is expressed; every value equals
    the family branch it replaced elsewhere in the codebase.
    """
    family = soc.family
    is_ap5 = family is SocFamily.AP5
    is_cortex_m4f = family in (SocFamily.AP3, SocFamily.AP4)

    reset = ResetCapabilities(
        default_power_reset_strategy=(_RESET_DEBUG_THEN_SWPOI if is_ap5 else _RESET_DEBUG),
        supports_swpoi=is_ap5,
    )
    transport = TransportCapabilities(
        requires_attached_probe_for_cycles=is_cortex_m4f,
        has_usb=soc.has_usb,
        swo_trace_clock_mhz=soc.swo_trace_clock_mhz,
        swo_core_clocked=soc.swo_trace_clock_mhz is None,
    )
    memory = MemoryCapabilities(
        has_dcache=is_ap5,
        has_shared_ssram_power_domain=is_ap5,
        placement_bases=_family_placement_bases(family),
        app_flash_load_addr=resolve_app_flash_load_addr(soc),
    )
    clock = ClockCapabilities(
        direct_burst_base_mhz=48 if family is SocFamily.AP3 else None,
        clean_window_timer="stimer" if is_ap5 else "dwt",
        gate_debug_domain_in_window=is_ap5,
        broad_peripheral_shutdown=family is SocFamily.AP4,
        crypto_otp_shutdown=is_ap5,
    )
    return SocCapabilities(
        reset=reset,
        transport=transport,
        memory=memory,
        clock=clock,
    )
