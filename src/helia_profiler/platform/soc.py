"""Platform model — SoC families, capabilities, and clock/memory model.

The platform model is a two-level hierarchy: Board -> SoC.  SoC determines the
core architecture, PMU capabilities, memory layout, and supported clock modes.
Board selects a concrete EVB (or, in the future, a custom target) that maps to
exactly one SoC.

Architecture note: AP330 (apollo330P) is Cortex-M55 and belongs to the AP5
family despite the "3" in its name.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from ..placement import Placement

if TYPE_CHECKING:
    from .capabilities import SocCapabilities

# ---------------------------------------------------------------------------
# SoC family (determines core, PMU tier, and MVE availability)
# ---------------------------------------------------------------------------


class SocFamily(Enum):
    """Ambiq SoC generation families."""

    AP3 = "ap3"  # Apollo3 / Apollo3P — Cortex-M4F, DWT only
    AP4 = "ap4"  # Apollo4 / Apollo4P / Apollo4L — Cortex-M4F, DWT only
    AP5 = "ap5"  # Apollo5 / Apollo510 / Apollo330P — Cortex-M55, full PMU + MVE


class CoreArch(Enum):
    """ARM core architectures relevant to profiling capabilities."""

    CORTEX_M4 = "cortex-m4"
    CORTEX_M55 = "cortex-m55"


class PmuTier(Enum):
    """PMU capability tiers."""

    DWT_ONLY = "dwt"  # Cortex-M4: DWT cycle counter, limited event support
    ARMV8M_PMU = "pmu"  # Cortex-M55: Full Armv8-M PMU, 70+ events, 8 counters

# ---------------------------------------------------------------------------
# SoC definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryLayout:
    """Memory sizes in KB.  0 means not present on this SoC."""

    mram_kb: int = 0
    sram_kb: int = 0
    dtcm_kb: int = 0
    itcm_kb: int = 0
    psram_kb: int = 0
    nvm_kb: int = 0


@dataclass(frozen=True)
class MemoryRange:
    """A half-open physical address range ``[start, start+length)``."""

    start: int
    length: int

    @property
    def end(self) -> int:
        """Exclusive end address."""
        return self.start + self.length

    def contains(self, address: int) -> bool:
        """True if *address* falls inside this range."""
        return self.start <= address < self.end

class PerfTier(Enum):
    """NSX CPU performance tier — maps directly to ``nsx_perf_mode_e``."""

    LOW = "NSX_PERF_LOW"
    MEDIUM = "NSX_PERF_MEDIUM"
    HIGH = "NSX_PERF_HIGH"


@dataclass(frozen=True)
class ClockSpeed:
    """A single named operating point within a clock domain.

    ``name`` uses Ambiq datasheet terminology (``ulp`` / ``lp`` / ``hp``).
    ``perf_tier`` is the NSX firmware control value applied for CPU domains.
    """

    name: str
    mhz: int
    perf_tier: PerfTier | None = None


@dataclass(frozen=True)
class ClockDomain:
    """An independently selectable clock domain on a SoC (e.g. cpu)."""

    name: str
    speeds: tuple[ClockSpeed, ...]
    default: str  # name of the default speed

    def speed(self, name: str) -> ClockSpeed | None:
        return next((s for s in self.speeds if s.name == name), None)

    @property
    def speed_names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.speeds)

    @property
    def default_speed(self) -> ClockSpeed:
        speed = self.speed(self.default)
        if speed is None:
            raise ValueError(
                f"Clock domain '{self.name}' default '{self.default}' is not a declared speed."
            )
        return speed

@dataclass(frozen=True)
class SocDef:
    """Definition of an Ambiq SoC relevant to profiling."""

    name: str  # e.g. "apollo510"
    family: SocFamily
    core: CoreArch
    pmu_tier: PmuTier
    has_mve: bool  # Helium / MVE vector extensions
    memory: MemoryLayout
    clocks: tuple[ClockDomain, ...]
    c_define: str  # e.g. "AM_PART_APOLLO510"
    cmsis_header: str  # e.g. "apollo510.h"
    rtt_scan_ranges: tuple[tuple[int, int], ...]
    jlink_device: str = ""  # J-Link device string (e.g. "AP510NFA-CBR")
    pmu_max_ops: int = 2048  # Max PMU accumulator operations (layers)
    #: SWO/ITM trace reference clock (MHz), when the TPIU TRACECLKIN is NOT the
    #: CPU clock.  Apollo3 routes a dedicated, CPU-independent clock to the
    #: TPIU, so the SWO baud does not change with TurboSPOT burst — the host
    #: must always reference this fixed clock when programming J-Link's SWO
    #: prescaler.  ``None`` means SWO is core-clocked (Apollo4/5): use the
    #: selected CPU frequency.
    swo_trace_clock_mhz: int | None = None

    #: Whether nsx-ambiq-usb supports this SoC (gates the usb_cdc transport).
    #: Apollo3/3P has no compatible nsx-ambiq-usb module, so usb_cdc is rejected
    #: at preflight with a clear message instead of failing at nsx lock.
    has_usb: bool = True

    #: AmbiqSuite HAL enum literal for "power on the entire shared SSRAM
    #: array" (AM_HAL_PWRCTRL_SRAM_config's eSRAMCfg/eActiveWithMCU/
    #: eSRAMRetain full-power value). AP5-family only (see
    #: capabilities.has_shared_ssram_power_domain) -- the enum NAME varies
    #: by SoC because it encodes each part's actual SSRAM capacity, even
    #: though it maps to the same "power everything" register value
    #: (PWRENSSRAM_ALL) on every AP5 part: AP510/AP510B/AP5B have 3 MB of
    #: SSRAM (AM_HAL_PWRCTRL_SRAM_3M), while apollo330P has only ~1.75 MB
    #: (AM_HAL_PWRCTRL_SRAM_1P75M) -- confirmed 2026-07 against the real
    #: synced HAL headers for each part; the two are NOT interchangeable
    #: (apollo330P's am_hal_pwrctrl.h does not define SRAM_3M at all).
    ssram_full_power_enum: str = "AM_HAL_PWRCTRL_SRAM_3M"

    #: Whether this part's HAL exposes am_hal_pwrctrl_rss_pwroff() (the
    #: internal radio subsystem / BLE-radio power-down AutoDeploy calls in
    #: ns_power_platform_config() when the app doesn't need Bluetooth).
    #: Confirmed 2026-07 by checking the synced AmbiqSuite HAL headers
    #: directly: apollo330P and apollo510L define it; the plain apollo510
    #: (non-L) HAL this project's "apollo510"/"apollo510b" SocDefs use does
    #: NOT -- calling it there would be a link error, so this must stay
    #: per-part rather than assumed true for the whole AP5 family.
    has_radio_subsystem: bool = False

    def clock_domain(self, name: str) -> ClockDomain | None:
        """Return the named clock domain, or ``None`` if not present."""
        return next((d for d in self.clocks if d.name == name), None)

    @property
    def cpu_clock(self) -> ClockDomain:
        """The CPU clock domain (every SoC declares one)."""
        domain = self.clock_domain("cpu")
        if domain is None:
            raise ValueError(f"SoC '{self.name}' has no cpu clock domain.")
        return domain

    @property
    def capabilities(self) -> SocCapabilities:
        """Typed capability records resolved for this SoC.

        All SoC-family policy is expressed once, here (via
        :func:`~helia_profiler.platform.capabilities.build_soc_capabilities`),
        so consumers read a named field instead of branching on ``family``.
        """
        from .capabilities import build_soc_capabilities

        return build_soc_capabilities(self)

    @property
    def has_full_pmu(self) -> bool:
        return self.pmu_tier is PmuTier.ARMV8M_PMU

    @property
    def has_dwt(self) -> bool:
        """All supported Cortex-M targets expose the DWT cycle counter."""
        return True

    @property
    def requires_attached_probe_for_cycles(self) -> bool:
        """Whether DWT cycle counts require a debugger attached during capture.

        On the Cortex-M4F families (Apollo3/3P and Apollo4/4P/4L) the
        ``DWT->CYCCNT`` counter lives in the core debug power domain, which
        stays powered only while a debugger asserts the DAP's ``CDBGPWRUPREQ``
        — a signal firmware cannot set from the core.  The SWO/RTT readers keep
        a debugger attached incidentally, but the UART/USB readers release the
        probe, so per-layer cycles read back as 0.  When this is True those
        readers must hold a pylink session open for the whole capture (see
        ``attached_reset_session``).  AP3 gating was confirmed empirically
        (2026-06-27): AOT-over-UART read 0 cycles until the probe was held
        attached, after which it matched the RTT/SWO cycle counts.  AP5
        (Cortex-M55) uses the resettable Armv8-M PMU and its secure bootloader
        prefers the probe released, so it stays False.
        """
        return self.capabilities.transport.requires_attached_probe_for_cycles

    @property
    def profiling_backends(self) -> tuple[str, ...]:
        """Concrete profiling backends available on this SoC.

        This is intentionally more explicit than ``pmu_tier`` so callers do
        not flatten a CM55 target into a single boolean like
        ``has_full_pmu``.
        """
        backends = ["dwt"]
        if self.has_full_pmu:
            backends.append("armv8m-pmu")
        return tuple(backends)

    @property
    def profiling_domains(self) -> tuple[str, ...]:
        """High-level compute domains the profiler can target on this SoC."""
        domains = ["cpu"]
        if self.has_full_pmu:
            domains.append("memory")
        if self.has_mve:
            domains.append("mve")
        return tuple(domains)

    @property
    def feature_flags(self) -> tuple[str, ...]:
        """Short capability tags suitable for logs, metadata, and CLI output."""
        flags: list[str] = list(self.profiling_backends)
        if self.has_mve:
            flags.append("mve")
        return tuple(flags)


# ---------------------------------------------------------------------------
# Built-in SoC registry
# ---------------------------------------------------------------------------

_SOCS: dict[str, SocDef] = {}


def _register_soc(soc: SocDef) -> SocDef:
    _SOCS[soc.name] = soc
    return soc

# --- AP3 family (Cortex-M4F) ------------------------------------------------

_register_soc(
    SocDef(
        name="apollo3p",
        family=SocFamily.AP3,
        core=CoreArch.CORTEX_M4,
        pmu_tier=PmuTier.DWT_ONLY,
        has_mve=False,
        # Apollo3p (Blue Plus): 2 MB NOR flash (ROMEM, 2,048,000 B usable above
        # the 0xC000 bootloader region), a real 64 KB low-latency TCM at
        # 0x10000000, and 700 KB main SRAM ("RWMEM") at 0x10011000.
        #
        # The TCM is genuine tightly-coupled memory in silicon (datasheet: "64
        # kB TCM", zero-wait-state, DMA-excluded) — but the nsx linker's
        # default `.bss`/`.data` targets RWMEM, not TCM; historically only
        # `.tcm` *code* (NSX_MEM_FAST_CODE) reached the real TCM. Data placed
        # there via NSX_MEM_FAST_BSS silently fell back to RWMEM (a no-op
        # macro) until nsx-ambiq-sdk#29 added a dedicated NOLOAD `.tcm_bss`
        # section. dtcm_kb=64 here (and the Placement.TCM base below) reflect
        # that fix — hpx build against an nsx-ambiq-sdk revision without it
        # will silently place the "TCM" arena in RWMEM instead.
        memory=MemoryLayout(mram_kb=2000, sram_kb=700, dtcm_kb=64),
        clocks=(
            ClockDomain(
                "cpu",
                # Apollo3/3P run at 48 MHz HFRC in normal mode.  The 96 MHz
                # "burst" (TurboSPOT) tier is NOT reachable through NSX
                # (nsx_platform_set_perf_mode is a no-op on Apollo3), so the
                # firmware enables it directly via am_hal_burst_mode_enable()
                # when "hp" is selected and mirrors the real 96 MHz into
                # SystemCoreClock.  Host-side timing and the SWO trace-clock
                # (cpu_speed passed to JLink.swo_enable) follow the selected
                # ClockSpeed.mhz, so they stay matched to the actual device
                # clock for both tiers.  Default remains 48 MHz.
                (
                    ClockSpeed("lp", 48, PerfTier.LOW),
                    ClockSpeed("hp", 96, PerfTier.HIGH),
                ),
                default="lp",
            ),
        ),
        c_define="AM_PART_APOLLO3P",
        cmsis_header="apollo3p.h",
        rtt_scan_ranges=((0x10000000, 0x100000),),
        jlink_device="AMA3B2KK-KBR",
        # Apollo3's TPIU TRACECLKIN is a dedicated 48 MHz-domain clock, NOT the
        # core clock — TurboSPOT burst (hp/96 MHz) does not change the SWO baud,
        # so the host always programs J-Link's SWO prescaler against 48 MHz.
        swo_trace_clock_mhz=48,
        # No nsx-ambiq-usb support on Apollo3/3P — usb_cdc transport unavailable.
        has_usb=False,
    )
)

# --- AP4 family (Cortex-M4F) ------------------------------------------------

_register_soc(
    SocDef(
        name="apollo4p",
        family=SocFamily.AP4,
        core=CoreArch.CORTEX_M4,
        pmu_tier=PmuTier.DWT_ONLY,
        has_mve=False,
        memory=MemoryLayout(mram_kb=2000, sram_kb=1024, dtcm_kb=384),
        clocks=(
            ClockDomain(
                "cpu",
                (
                    ClockSpeed("lp", 96, PerfTier.LOW),
                    ClockSpeed("hp", 192, PerfTier.HIGH),
                ),
                default="lp",
            ),
        ),
        c_define="AM_PART_APOLLO4P",
        cmsis_header="apollo4p.h",
        rtt_scan_ranges=((0x10000000, 0x100000),),
        jlink_device="AMAP42KP-KBR",
    )
)

_register_soc(
    SocDef(
        name="apollo4l",
        family=SocFamily.AP4,
        core=CoreArch.CORTEX_M4,
        pmu_tier=PmuTier.DWT_ONLY,
        has_mve=False,
        memory=MemoryLayout(mram_kb=2000, sram_kb=1024, dtcm_kb=384),
        clocks=(
            ClockDomain(
                "cpu",
                (
                    ClockSpeed("lp", 96, PerfTier.LOW),
                    ClockSpeed("hp", 192, PerfTier.HIGH),
                ),
                default="lp",
            ),
        ),
        c_define="AM_PART_APOLLO4L",
        cmsis_header="apollo4l.h",
        rtt_scan_ranges=((0x10000000, 0x100000),),
        jlink_device="AMAP42KL-KBR",
    )
)

# --- AP5 family (Cortex-M55, full PMU + MVE) --------------------------------

_register_soc(
    SocDef(
        name="apollo510",
        family=SocFamily.AP5,
        core=CoreArch.CORTEX_M55,
        pmu_tier=PmuTier.ARMV8M_PMU,
        has_mve=True,
        memory=MemoryLayout(
            mram_kb=4096,
            sram_kb=3072,
            dtcm_kb=512,
            itcm_kb=256,
            # apollo510_evb populates an AP Memory APS512XXB (512 Mbit =
            # 64 MB) hex PSRAM on MSPI0 — proven on real hardware via XIP
            # address-aliasing (+32 MB holds distinct data) during the
            # 2026-07-05 non-B PSRAM validation, matching the 510B finding.
            psram_kb=65536,
            nvm_kb=8192,
        ),
        clocks=(
            ClockDomain(
                "cpu",
                (
                    ClockSpeed("lp", 96, PerfTier.LOW),
                    ClockSpeed("hp", 250, PerfTier.HIGH),
                ),
                default="lp",
            ),
        ),
        c_define="AM_PART_APOLLO510",
        cmsis_header="apollo510.h",
        # On the cache-coherent M55 parts RTT is pinned to non-cached TCM
        # (default .bss), NOT .sram_bss — see firmware/__init__.py and
        # SEGGER_RTT_Conf.h. DTCM is based at 0x20000000 (512 KB), so the
        # fallback scan covers that window. The known-address fast path (nm/map)
        # is the primary route and skips scanning entirely.
        rtt_scan_ranges=((0x20000000, 0x80000),),
        jlink_device="AP510NFA-CBR",
        pmu_max_ops=4096,
    )
)

_register_soc(
    SocDef(
        name="apollo510b",
        family=SocFamily.AP5,
        core=CoreArch.CORTEX_M55,
        pmu_tier=PmuTier.ARMV8M_PMU,
        has_mve=True,
        memory=MemoryLayout(
            mram_kb=4096,
            sram_kb=3072,
            dtcm_kb=512,
            itcm_kb=256,
            # apollo510b_evb populates an AP Memory APS512XXN (512 Mbit =
            # 64 MB) hex PSRAM on MSPI0 — proven by XIP address-aliasing on
            # real hardware (+32 MB is distinct storage, +64 MB wraps) during
            # the 2026-07-05 PSRAM bring-up. The 32 MB value was inherited
            # from the apollo510_evb assumption and under-reported capacity.
            psram_kb=65536,
        ),
        clocks=(
            ClockDomain(
                "cpu",
                (
                    ClockSpeed("lp", 96, PerfTier.LOW),
                    ClockSpeed("hp", 250, PerfTier.HIGH),
                ),
                default="lp",
            ),
        ),
        c_define="AM_PART_APOLLO510B",
        cmsis_header="apollo510.h",
        # See apollo510: M55 RTT lives in non-cached TCM (.bss), DTCM @ 0x20000000.
        rtt_scan_ranges=((0x20000000, 0x80000),),
        jlink_device="AP510BFA-CBR",
        pmu_max_ops=4096,
    )
)

_register_soc(
    SocDef(
        name="apollo5b",
        family=SocFamily.AP5,
        core=CoreArch.CORTEX_M55,
        pmu_tier=PmuTier.ARMV8M_PMU,
        has_mve=True,
        memory=MemoryLayout(
            mram_kb=4096,
            sram_kb=3072,
            dtcm_kb=512,
            itcm_kb=256,
            psram_kb=32768,
        ),
        clocks=(
            ClockDomain(
                "cpu",
                (
                    ClockSpeed("lp", 96, PerfTier.LOW),
                    ClockSpeed("hp", 250, PerfTier.HIGH),
                ),
                default="lp",
            ),
        ),
        c_define="AM_PART_APOLLO5B",
        cmsis_header="apollo510.h",
        # See apollo510: M55 RTT lives in non-cached TCM (.bss), DTCM @ 0x20000000.
        rtt_scan_ranges=((0x20000000, 0x80000),),
        jlink_device="AP510NFA-CBR",
        pmu_max_ops=4096,
    )
)

# AP330 — Cortex-M55, belongs to AP5 family despite the "3" in the name
_register_soc(
    SocDef(
        name="apollo330P",
        family=SocFamily.AP5,
        core=CoreArch.CORTEX_M55,
        pmu_tier=PmuTier.ARMV8M_PMU,
        has_mve=True,
        memory=MemoryLayout(
            # Corrected 2026-07 against the actual synced NSX linker script
            # (nsx-core/src/apollo330P/gcc/linker_script_sbl.ld) for this
            # Rev1 EVB -- the previous values were copy-pasted from
            # apollo510 (same address map/family) but this SoC's real
            # memory regions are substantially smaller:
            #   MCU_TCM     0x20000000, LENGTH=245760  ->  240 KB (was 512)
            #   SHARED_SRAM 0x20080000, LENGTH=1835008 -> 1792 KB (was 3072)
            #   MCU_MRAM    0x00410000, LENGTH=2031616 -> 1984 KB app-usable
            #               (post-SBL; was 4096, the AP510 full-part value)
            # This board's linker script does not declare a separate ITCM
            # region at all (unlike AP510's split ITCM/DTCM banks) -- TCM is
            # unified into the single MCU_TCM region above, so itcm_kb=0
            # rather than carrying over AP510's 256.
            # dtcm_kb/sram_kb/mram_kb are direct arena/weights placement
            # CAPACITY CHECKS (plan_memory.py, validation/matrix.py) --
            # the previous inflated values would have silently accepted
            # placements that overflow the real linked memory and only
            # fail at build/link time (confirmed on real hardware: a KWS
            # capture's .bss+.data overflowed the true 240 KB MCU_TCM by
            # 776 bytes while claiming to fit comfortably under 512 KB).
            mram_kb=1984,
            sram_kb=1792,
            dtcm_kb=240,
            itcm_kb=0,
            psram_kb=32768,
        ),
        clocks=(
            ClockDomain(
                "cpu",
                (
                    ClockSpeed("lp", 96, PerfTier.LOW),
                    ClockSpeed("hp", 250, PerfTier.HIGH),
                ),
                default="lp",
            ),
        ),
        c_define="AM_PART_APOLLO330P",
        cmsis_header="apollo330P.h",
        # See apollo510: M55 RTT lives in non-cached TCM (.bss), DTCM @
        # 0x20000000.  Scan length matches this part's REAL 240 KB MCU_TCM
        # (0x3C000, from the linker script — see the MemoryLayout comment
        # above), not AP510's 512 KB (0x80000): scanning past the end of
        # physical TCM risks bus-faulting the debug read or matching
        # garbage in unmapped space.
        rtt_scan_ranges=((0x20000000, 0x3C000),),
        # J-Link device string for AP330 is NOT in JLinkExe's stock device
        # database -- it's a custom Ambiq-provided entry
        # ("Apollo330P_510L", inheriting AP510NFA-CBR's debug/reset
        # sequence in ~/.config/SEGGER/JLinkDevices/AmbiqMicro/JLinkDevices.xml).
        # "AP330NFA-CBR" (the datasheet part number) is NOT a registered
        # device string at all and silently fails core identification
        # (JLinkExe still resets generically via -autoconnect without
        # full device-DB knowledge, but never prints the "Cortex-M55
        # identified" banner _inspect_probe_target()/probes match parses,
        # so probe resolution always reports "unknown target"). Confirmed
        # 2026-07 against real Apollo330mP Rev1 EVB hardware.
        jlink_device="Apollo330P_510L",
        # Corrected 2026-07 alongside the memory-layout fix above: heliaRT's
        # per-layer PMU profiler (HpxPmuProfiler/g_profiler) statically
        # reserves ~24 bytes per pmu_max_ops entry regardless of the actual
        # model's layer count -- at 4096 (copied from apollo510, which has
        # over 2x this board's real TCM) that alone is ~96 KB, over a third
        # of this board's real 240 KB MCU_TCM budget, for models that only
        # use a small fraction of it (KWS: 13 layers, VWW/heliaAOT: 31).
        # 512 keeps ~16x headroom over the largest layer count seen in this
        # profiler's own MLPerf Tiny fixtures while freeing ~84 KB of the
        # real budget back for the arena/model/RTT buffers that actually
        # need it on this more memory-constrained board.
        pmu_max_ops=512,
        # This board's real SSRAM capacity is ~1.75 MB (confirmed via the
        # linker script fix above), not AP510's 3 MB -- its HAL only
        # defines AM_HAL_PWRCTRL_SRAM_1P75M (does not have SRAM_3M at all).
        ssram_full_power_enum="AM_HAL_PWRCTRL_SRAM_1P75M",
        # AP330P's TPIU trace clock is a FIXED 48 MHz XTAL_HS, NOT the CPU
        # clock -- unlike apollo510/5B (core-clocked HFRC_96MHz path).
        # Ground truth: NSX's nsx_system_platform.c groups APOLLO330P with
        # APOLLO510L ("Trace clock on these parts = XTAL_HS 48 MHz.  JLink
        # SWO viewer: -cpufreq 48000000") and NSX's
        # cmake/socs/facts/apollo330P.cmake sets NSX_SEGGER_CPUFREQ=48000000.
        # Without this the host programs the SWO prescaler against the CPU
        # clock (96/250 MHz) and decodes garbage -> "no data" on the swo
        # transport even though the firmware is printing. Same quirk (and
        # same field/comment pattern) as apollo3p above.
        swo_trace_clock_mhz=48,
        # apollo330P's HAL defines am_hal_pwrctrl_rss_pwroff() (unlike the
        # plain apollo510/apollo510b HAL variants this project builds
        # against) -- see SocDef.has_radio_subsystem docstring.
        has_radio_subsystem=True,
    )
)

# ---------------------------------------------------------------------------
# Physical memory address ranges (for build-time placement verification)
# ---------------------------------------------------------------------------

# MemoryLayout size field backing each placement region.
_PLACEMENT_SIZE_FIELD: dict[Placement, str] = {
    Placement.TCM: "dtcm_kb",
    Placement.SRAM: "sram_kb",
    Placement.MRAM: "mram_kb",
    Placement.PSRAM: "psram_kb",
}


def soc_placement_ranges(soc: SocDef) -> dict[Placement, MemoryRange]:
    """Return physical address ranges for each arena/weights placement region.

    Maps each :class:`~helia_profiler.placement.Placement` to the concrete
    ``[start, start+length)`` window on *soc*, derived from the placement bases
    in ``soc.capabilities.memory`` and the SoC's ``MemoryLayout`` sizes.
    Regions the SoC does not have (size 0) are omitted.  Returns an empty
    mapping for SoC families whose memory model is not yet characterised, so
    callers treat verification as best-effort.
    """
    bases = soc.capabilities.memory.placement_bases
    if not bases:
        return {}
    ranges: dict[Placement, MemoryRange] = {}
    for placement, base in bases.items():
        size_kb = getattr(soc.memory, _PLACEMENT_SIZE_FIELD[placement], 0)
        if size_kb > 0:
            ranges[placement] = MemoryRange(base, size_kb * 1024)
    return ranges
