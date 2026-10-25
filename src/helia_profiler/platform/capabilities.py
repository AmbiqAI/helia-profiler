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

from .memory_map import mram_app_origin
from .placement import Placement
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
    flag here: without lock-step the firmware free-runs its measured window
    straight out of reset, so *any* host-side reset latency can race the
    gate, regardless of family (issue #114).  The default keys on the wiring
    and the capture mode instead -- see
    :attr:`helia_profiler.config.PowerConfig.lockstep_resolved`, which is the
    single place that policy lives.
    """

    default_power_reset_strategy: str


@dataclass(frozen=True)
class TransportCapabilities:
    #: DWT->CYCCNT lives in the core debug power domain on the Cortex-M4F parts
    #: (Apollo3/3P, Apollo4/4P/4L); the released UART/USB readers must hold a
    #: probe attached for the whole capture or per-layer cycles read back 0.
    requires_attached_probe_for_cycles: bool
    #: Whether nsx-ambiq-usb supports this SoC (gates the usb_cdc transport).
    has_usb: bool
    #: Fixed SWO/ITM trace reference clock (MHz) when the TPIU is NOT core-clocked.
    swo_trace_clock_mhz: int | None


@dataclass(frozen=True)
class MemoryCapabilities:
    #: Cache-coherent Cortex-M55 (Apollo5) parts have a CPU D-cache and need
    #: explicit maintenance around host-shared RTT buffers.
    has_dcache: bool
    #: Apollo5 parts expose the shared SSRAM power domain that firmware
    #: powers on for SRAM-resident arenas.
    has_shared_ssram_power_domain: bool
    #: Physical base address of each arena/weights-eligible placement region.
    #: Sizes come from the SoC ``MemoryLayout``; only the bases are family-wide.
    placement_bases: Mapping[Placement, int]
    #: Address the NSX build programs application images at (the ``LoadFile``
    #: address in its generated ``flash_cmds.jlink``) -- the first MRAM/flash
    #: address above the part's bootloader-reserved region, NOT the MRAM
    #: *region* base in ``placement_bases``.  Resolved by
    #: :func:`resolve_app_flash_load_addr`; ``None`` when it has no value it
    #: can stand behind for this part -- a built-in with no linked memory
    #: map, or a ``target.custom_socs`` part that stated no address and named no
    #: ``based_on``.  The J-Link flash fallback refuses to guess an address then.
    app_flash_load_addr: int | None


@dataclass(frozen=True)
class ClockCapabilities:
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
    #: Whether firmware powers down IOM/UART/ADC/MSPI(-when-unused)/GFX/
    #: DISP/USB/PDM/I2S/SDIO/AUDADC/Crypto/VCOMP and the DEBUG power domain
    #: at boot. True on AP4 only.
    broad_peripheral_shutdown: bool
    #: Whether firmware disables the CRYPTO and OTP power domains and the
    #: voltage comparator (VCOMP) before running the model. True on
    #: AP5-family parts (AP510, AP330P). Narrower than
    #: ``broad_peripheral_shutdown``: no IOM/UART/GFX/etc, no full-SRAM
    #: power-off.
    crypto_otp_shutdown: bool


@dataclass(frozen=True)
class SocCapabilities:
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
          unwatched once flashed, so no debugger is holding it.

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
        -- never long, and never zero, which is why it looks plausible.  Measured
        impact of an unguarded window: issue #121.

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

# Per-SoC overrides for realizations whose memory map departs from the family
# baseline. The atomiq110 FPGA realization emulates MRAM/SRAM at dedicated
# windows instead of the Apollo5 silicon addresses (see the board's
# linker_script.ld: MCU_TCM 0x20000000, SHARED_SRAM 0x21000000,
# MCU_MRAM 0x22000000).
_SOC_MEMORY_BASES: dict[str, dict[Placement, int]] = {
    "atomiq110": {
        Placement.TCM: 0x20000000,
        Placement.SRAM: 0x21000000,
        Placement.MRAM: 0x22000000,
    },
}


def _placement_bases(soc: SocDef) -> Mapping[Placement, int]:
    """Return the frozen placement-base map for *soc* (empty if unknown).

    ``memory_bases_like`` (set for ``based_on`` custom derivatives) keys the
    per-SoC override table in place of the derivative's own name, so an
    Atomiq-derived part keeps Atomiq's emulated MRAM/SRAM windows instead of
    falling back to the AP5 family map.
    """
    key = soc.memory_bases_like or soc.name
    bases = _SOC_MEMORY_BASES.get(key) or _FAMILY_MEMORY_BASES.get(soc.family, {})
    return MappingProxyType(dict(bases))


def resolve_app_flash_load_addr(soc: SocDef) -> int | None:
    """Resolve *soc*'s app-image flash (``LoadFile``) address.

    ``soc.app_flash_load_addr`` wins when set: it is the only source a
    ``target.custom_socs`` entry can write.  Otherwise a **built-in** SoC
    (:attr:`~helia_profiler.platform.soc.SocDef.is_builtin`) loads at the
    start of its verified linked MRAM window from
    :mod:`~helia_profiler.platform.memory_map` -- the SBL-excluded app origin
    NSX's generated ``flash_cmds.jlink`` programs (the nbl origin on
    atomiq110, whose default profile has no bootloader).

    A custom SoC resolves to ``None`` unless it states an address (directly
    or inherited from its ``based_on`` part, which ``platform.custom``
    resolves through this same function): a user-chosen name must not reach
    a built-in's map, and nothing here was verified for a part hpx has never
    seen.  ``None`` means "nobody knows"; the J-Link flash fallback refuses
    to program rather than guess.
    """
    if soc.app_flash_load_addr is not None:
        return soc.app_flash_load_addr
    if not soc.is_builtin:
        return None
    return mram_app_origin(soc.name)


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
    )
    transport = TransportCapabilities(
        requires_attached_probe_for_cycles=is_cortex_m4f,
        has_usb=soc.has_usb,
        swo_trace_clock_mhz=soc.swo_trace_clock_mhz,
    )
    memory = MemoryCapabilities(
        has_dcache=is_ap5,
        has_shared_ssram_power_domain=is_ap5,
        placement_bases=_placement_bases(soc),
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
