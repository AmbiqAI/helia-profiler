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
    from .board import BoardDef
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


@dataclass(frozen=True)
class ClockCapabilities:
    """Clock/perf policy that used to be a SoC-family branch."""

    #: Base CPU clock (MHz) above which the firmware must enable burst directly
    #: via the AmbiqSuite HAL because NSX's perf-mode switch is a no-op
    #: (Apollo3/3P TurboSPOT).  ``None`` means NSX handles perf switching.
    direct_burst_base_mhz: int | None


@dataclass(frozen=True)
class SocCapabilities:
    """Bundle of the typed capability records for one SoC."""

    reset: ResetCapabilities
    transport: TransportCapabilities
    memory: MemoryCapabilities
    clock: ClockCapabilities


@dataclass(frozen=True)
class PowerCaptureCapabilities:
    """Board-level power-capture wiring defaults (3-wire lock-step sync)."""

    sync_gpio_pin: int
    state_gpio_pin: int
    go_gpio_pin: int


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
        Placement.TCM: 0x10000000,   # real 64 KB low-latency TCM (NSX_MEM_FAST_BSS, nsx-ambiq-sdk#29)
        Placement.SRAM: 0x10011000,  # RWMEM main SRAM (default .bss/.data home)
        Placement.MRAM: 0x00000000,  # NOR flash XIP (ROMEM @ 0x0000C000), weights
    },
    SocFamily.AP4: {
        Placement.TCM: 0x10000000,   # DTCM
        Placement.SRAM: 0x10060000,  # SHARED_SRAM (right after 384 KB DTCM)
        Placement.MRAM: 0x00000000,  # MRAM (XIP)
        Placement.PSRAM: 0x60000000,
    },
    SocFamily.AP5: {
        Placement.TCM: 0x20000000,   # DTCM
        Placement.SRAM: 0x20080000,  # SSRAM (right after 512 KB DTCM)
        Placement.MRAM: 0x00000000,  # MRAM (XIP)
        Placement.PSRAM: 0x60000000,
    },
}


def _family_placement_bases(family: SocFamily) -> Mapping[Placement, int]:
    """Return the frozen placement-base map for *family* (empty if unknown)."""
    return MappingProxyType(dict(_FAMILY_MEMORY_BASES.get(family, {})))


def build_soc_capabilities(soc: SocDef) -> SocCapabilities:
    """Resolve the typed capability records for *soc*.

    This is the single place SoC-family policy is expressed; every value equals
    the family branch it replaced elsewhere in the codebase.
    """
    family = soc.family
    is_ap5 = family is SocFamily.AP5
    is_cortex_m4f = family in (SocFamily.AP3, SocFamily.AP4)

    reset = ResetCapabilities(
        default_power_reset_strategy=(
            _RESET_DEBUG_THEN_SWPOI if is_ap5 else _RESET_DEBUG
        ),
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
    )
    clock = ClockCapabilities(
        direct_burst_base_mhz=48 if family is SocFamily.AP3 else None,
    )
    return SocCapabilities(
        reset=reset,
        transport=transport,
        memory=memory,
        clock=clock,
    )


def build_power_capture_capabilities(board: BoardDef) -> PowerCaptureCapabilities:
    """Resolve the power-capture wiring defaults for *board*."""
    return PowerCaptureCapabilities(
        sync_gpio_pin=board.default_sync_gpio_pin,
        state_gpio_pin=board.default_state_gpio_pin,
        go_gpio_pin=board.default_go_gpio_pin,
    )
