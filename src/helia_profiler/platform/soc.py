"""Platform model — SoC families, capabilities, and clock/memory model.

The platform model is a two-level hierarchy: Board -> SoC.  SoC determines the
core architecture, PMU capabilities, memory layout, and supported clock modes.
Board selects a concrete target that references exactly one SoC.

Architecture note: AP330 (apollo330P) is Cortex-M55 and belongs to the AP5
family despite the "3" in its name.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from .placement import Placement

if TYPE_CHECKING:
    from .capabilities import SocCapabilities


class SocFamily(Enum):
    AP3 = "ap3"  # Apollo3 / Apollo3P — Cortex-M4F, DWT only
    AP4 = "ap4"  # Apollo4 / Apollo4P / Apollo4L — Cortex-M4F, DWT only
    AP5 = "ap5"  # Apollo5 / Apollo510 / Apollo510L / Apollo330P — Cortex-M55, full PMU + MVE


class CoreArch(Enum):
    CORTEX_M4 = "cortex-m4"
    CORTEX_M55 = "cortex-m55"


class PmuTier(Enum):
    DWT_ONLY = "dwt"  # Cortex-M4: DWT cycle counter, limited event support
    ARMV8M_PMU = "pmu"  # Cortex-M55: Full Armv8-M PMU, 70+ events, 8 counters


class SocOrigin(Enum):
    """Whether a view was registered as a supported built-in or supplied by a caller."""

    BUILTIN = "builtin"
    CUSTOM = "custom"


@dataclass(frozen=True)
class MemoryLayout:
    """Declared hardware/board capacities in KiB; linker admission is a separate view."""

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
        return self.start + self.length

    def contains(self, address: int) -> bool:
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
    name: str
    speeds: tuple[ClockSpeed, ...]
    default: str  # name of the default speed

    def speed(self, name: str) -> ClockSpeed | None:
        return next((s for s in self.speeds if s.name == name), None)

    @property
    def speed_names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.speeds)


@dataclass(frozen=True)
class SocDef:
    """Definition of an Ambiq SoC relevant to profiling."""

    name: str
    family: SocFamily
    core: CoreArch
    pmu_tier: PmuTier
    has_mve: bool  # Helium / MVE vector extensions
    memory: MemoryLayout
    clocks: tuple[ClockDomain, ...]
    c_define: str
    cmsis_header: str
    rtt_scan_ranges: tuple[tuple[int, int], ...]
    jlink_device: str = ""
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
    #: (AM_HAL_PWRCTRL_SRAM_1P75M); the two are NOT interchangeable
    #: (apollo330P's am_hal_pwrctrl.h does not define SRAM_3M at all).
    ssram_full_power_enum: str = "AM_HAL_PWRCTRL_SRAM_3M"

    #: Whether the declared radio subsystem can use am_hal_pwrctrl_rss_pwroff() (the
    #: internal radio subsystem / BLE-radio power-down AutoDeploy calls in
    #: ns_power_platform_config() when the app doesn't need Bluetooth).
    #: apollo330P and apollo510L define it; the plain apollo510 (non-L) HAL
    #: this project's "apollo510"/"apollo510b" SocDefs use does NOT --
    #: calling it there would be a link error, so this must stay per-part
    #: rather than assumed true for the whole AP5 family.
    has_radio_subsystem: bool = False

    #: Attached microNPU accelerator config string (Vela's
    #: ``--accelerator-config`` spelling, e.g. ``"ethos-u85-256"``), or
    #: ``None`` when the SoC has no NPU. Gates the ``ethos_u`` engine
    #: backend and the ``ethos_npu`` PMU counter group.
    npu: str | None = None

    #: Explicit user-provided application address. Registered catalog facts are
    #: separate so a renamed copy cannot inherit another target's boot address.
    app_flash_load_addr: int | None = None

    #: Name of the part whose resolved placement-base map this SoC's memory
    #: map follows.  Set only by ``platform.custom`` for ``based_on``
    #: derivatives (to the base chain's built-in name) so that
    #: ``capabilities._placement_bases`` reaches per-SoC base overrides —
    #: atomiq110's emulated MRAM/SRAM windows — that its name-keyed lookup
    #: would otherwise miss, exactly as ``based_on`` inherits the app-flash
    #: address.  Never user-declarable: a custom part states a novel memory
    #: map through its own family/name, not by claiming another part's.
    memory_bases_like: str | None = None

    #: Registration provenance; caller-constructed views remain custom.
    origin: SocOrigin = SocOrigin.CUSTOM
    #: Original registered spelling, retained by a board-specific copy.
    registered_name: str | None = None
    #: Catalog selection behind a registered view; absent on the SDK descriptor.
    catalog_target_id: str | None = None
    #: Boot fact of the registered catalog target; not an explicit user override.
    catalog_app_flash_load_addr: int | None = None
    #: Catalog-backed hardware input, absent on caller-constructed definitions.
    catalog_has_dcache: bool | None = None

    @property
    def is_builtin(self) -> bool:
        """Whether this view still names the built-in it was registered as."""
        return self.origin is SocOrigin.BUILTIN and self.registered_name == self.name

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
    def requires_attached_probe_for_cycles(self) -> bool:
        """Whether DWT cycle counts require a debugger attached during capture.

        On the Cortex-M4F families (Apollo3/3P and Apollo4/4P/4L) the
        ``DWT->CYCCNT`` counter lives in the core debug power domain, which
        stays powered only while a debugger asserts the DAP's ``CDBGPWRUPREQ``
        — a signal firmware cannot set from the core.  The SWO/RTT readers keep
        a debugger attached incidentally, but the UART/USB readers release the
        probe, so per-layer cycles read back as 0.  When this is True those
        readers must hold a pylink session open for the whole capture (see
        ``attached_reset_session``).  AP5 (Cortex-M55) uses the resettable
        Armv8-M PMU and its secure bootloader prefers the probe released, so
        it stays False.
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
        if self.npu is not None:
            domains.append("ethos_npu")
        return tuple(domains)


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
