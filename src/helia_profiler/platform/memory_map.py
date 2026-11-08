"""HPX linked-memory classification windows and script application extents.

The committed generated data records the SDK revision, script selection and
source hashes. GNU covers GCC/ATfE; armlink covers armclang. This public view
characterizes a builtin profile, while a build must separately establish that
its actual source and script match before using it for accounting.

Classification apertures and admission budgets have different owners. An
aperture identifies the physical bank containing an ELF address; an app extent
is the script's region within that bank. Catalog application intervals are
intersected separately for admission, never inferred from the aperture's size.
MRAM classification starts at the linked app origin, excluding the bootloader.
Only script-addressable RAM banks are characterized. Runtime-uploaded PSRAM is
not section-attributable and its occupancy comes from the plan.

Fixed heap/stack regions outside the app extent count as reserved. GNU's
floating stack counts as live occupancy; its fill-to-end heap is reserved.
Load-image bytes are attributed by segment physical addresses, not sections.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from .placement import MemoryRegion, Placement
from .soc import MemoryRange, SocDef


class LinkFamily(StrEnum):
    """Which linker laid the image out — the axis app_window keys on."""

    #: GNU ld / LLD linking the gcc ``*.ld`` scripts (gcc AND ATfE builds).
    GNU = "gnu"
    #: armlink with the ``*.sct`` scatter files (armclang builds).
    ARMLINK = "armlink"


#: Every supported toolchain name, classified deliberately: an unknown name
#: raises instead of silently guessing GNU, so a future armlink-based
#: toolchain must be added here on purpose. Kept in lockstep with
#: :class:`helia_profiler.vocab.Toolchain` by a contract test — platform
#: deliberately does not import the config layer.
_LINK_FAMILY_BY_TOOLCHAIN: dict[str, LinkFamily] = {
    "arm-none-eabi-gcc": LinkFamily.GNU,
    "gcc": LinkFamily.GNU,
    "armclang": LinkFamily.ARMLINK,
    "atfe": LinkFamily.GNU,
}


def link_family_for_toolchain(toolchain: str) -> LinkFamily:
    """The link family a toolchain builds with (``ValueError`` on unknown)."""
    try:
        return _LINK_FAMILY_BY_TOOLCHAIN[toolchain]
    except KeyError:
        known = ", ".join(sorted(_LINK_FAMILY_BY_TOOLCHAIN))
        raise ValueError(f"{toolchain!r} is not a supported toolchain (known: {known}).") from None


@dataclass(frozen=True)
class LinkedRegionWindow:
    """One region's verified window plus its per-link-family app sub-window.

    ``window`` is the CLASSIFICATION aperture — an allocated section whose
    address falls inside it belongs to this region. ``app_window`` is the
    address EXTENT of the linked region the app's image occupies under
    each link family. Honest free math is
    ``app_window.length − Σ(allocated sections inside app_window,
    excluding linker_reserved ones)``.

    Reservations are handled by TWO mechanisms, matched to how each link
    family expresses them:

    * FIXED script regions are carved out by EXTENT: armlink's scatter
      pins ``ARM_LIB_HEAP``/``ARM_LIB_STACK`` above ``MCU_TCM`` (so the
      armlink extent is ``MCU_TCM`` itself), and apollo3p's gcc script
      pins ``STACKMEM`` below ``RWMEM``. Sections inside ``window`` but
      outside ``app_window`` are these fixed reservations — report them
      as reserved, never as app usage or free space.
    * FLOATING sections are handled by the INVENTORY: gcc's ``.stack`` is
      an ordinary output section whose position depends on what else the
      script places first (apollo330P's ``.dtcm_text`` — which hpx's own
      AOT engine emits via ``HELIAAOT_PUT_IN_ITCM`` — precedes it; a
      fixed carve-out would exclude app code there). The gcc DTCM extent is therefore
      the FULL script region, and ``.stack`` counts as occupancy — it is
      live memory the firmware needs. gcc's fill-to-end ``.heap`` is the one
      ``linker_reserved`` exclusion (its size states what was LEFT, so
      counting it would make free identically zero).

    A region absent from a SoC's map simply has no window entry.
    """

    region: MemoryRegion
    window: MemoryRange
    #: ``hash=False``: a MappingProxyType is unhashable, and frozen dataclass
    #: hashing would otherwise make ``hash(window)`` raise. Equality still
    #: compares it.
    app_window: Mapping[LinkFamily, MemoryRange] = field(hash=False)
    #: Where the WINDOW bounds came from: "hardware-aperture" (SDK regs
    #: headers — the RAM regions), "linker-app-origin" (MRAM: app link
    #: origin to hardware flash top), or "board-knowledge" (PSRAM). The
    #: window and app extents have separate provenance because hardware
    #: bounds and linker reservations come from different sources.
    window_provenance: str = "hardware-aperture"
    #: Where the app extents came from — linker-script characterization
    #: or board knowledge (PSRAM).
    app_provenance: str = "linker-script"
    #: False when the characterized linker scripts provide no section region
    #: here (PSRAM). Consumers must reconcile such regions from
    #: the PLAN, never report "used 0, free capacity" from an inventory
    #: that structurally cannot see them.
    section_attributable: bool = True

    def __post_init__(self) -> None:
        # Freeze the mapping so the construction-time validation below is
        # an invariant, not a snapshot (a caller-supplied plain dict could
        # otherwise be mutated behind the frozen dataclass).
        object.__setattr__(self, "app_window", MappingProxyType(dict(self.app_window)))
        for family in LinkFamily:
            extent = self.app_window.get(family)
            if extent is None:
                raise ValueError(f"{self.region}: app_window missing {family!r}")
            if extent.start < self.window.start or extent.end > self.window.end:
                raise ValueError(f"{self.region}: app_window {family!r} lies outside the window")


def _window(
    region: MemoryRegion,
    start: int,
    length: int,
    *,
    gnu: int,
    armlink: int,
    gnu_start: int | None = None,
    armlink_start: int | None = None,
    window_provenance: str = "hardware-aperture",
    app_provenance: str = "linker-script",
    section_attributable: bool = True,
) -> LinkedRegionWindow:
    """``gnu``/``armlink`` are app-extent LENGTHS; ``gnu_start``/
    ``armlink_start`` default to the window start (they differ only where
    the script pins a FIXED region below the app one — apollo3p's
    STACKMEM slot under RWMEM)."""
    return LinkedRegionWindow(
        region=region,
        window=MemoryRange(start, length),
        app_window=MappingProxyType(
            {
                LinkFamily.GNU: MemoryRange(start if gnu_start is None else gnu_start, gnu),
                LinkFamily.ARMLINK: MemoryRange(
                    start if armlink_start is None else armlink_start, armlink
                ),
            }
        ),
        window_provenance=window_provenance,
        app_provenance=app_provenance,
        section_attributable=section_attributable,
    )


def linked_memory_map(
    soc: SocDef,
    *,
    linker_profile: str = "default",
) -> tuple[LinkedRegionWindow, ...]:
    """The verified region windows for *soc*, PSRAM appended when present.

    Returns an empty tuple for any non-builtin SoC — a ``target.custom_socs``
    part was never checked against these scripts, EVEN IF its name collides
    with a registered part's (same rule and rationale as
    ``capabilities.resolve_app_flash_load_addr``; see ``SocDef.is_builtin``).
    Every registered SoC is currently characterized, so builtins never hit
    the empty branch today. Callers treat empty as unavailable, never
    guessed.

    ``linker_profile`` is the third axis of the real layout: these tables
    characterize NSX's DEFAULT profile ONLY (sbl-based on Apollo parts,
    nbl-based on atomiq110). ``itcm`` is a
    documented engine knob (the ``guide/engines`` page) forwarded straight
    to CMake, and its scripts declare DIFFERENT regions (apollo330P and
    apollo510L have none) — so any
    profile other than ``default`` returns empty: the honest "unavailable"
    instead of a confidently wrong map.
    """
    if linker_profile != "default":
        return ()
    if not soc.is_builtin:
        return ()
    from .linker.data import characterized_windows

    windows = characterized_windows(soc.name)
    if not windows:
        return ()
    psram_kb = soc.memory.psram_kb
    psram_base = soc.capabilities.memory.placement_bases.get(Placement.PSRAM)
    if psram_kb > 0 and psram_base is not None:
        windows = windows + (
            _window(
                MemoryRegion.PSRAM,
                psram_base,
                psram_kb * 1024,
                gnu=psram_kb * 1024,
                armlink=psram_kb * 1024,
                window_provenance="board-knowledge",
                app_provenance="board-knowledge",
                section_attributable=False,
            ),
        )
    return windows


def mram_app_origin(soc_name: str) -> int | None:
    """Start of *soc_name*'s linked MRAM window, if mapped."""
    from .linker.data import characterized_windows

    for entry in characterized_windows(soc_name):
        if entry.region is MemoryRegion.MRAM:
            return entry.window.start
    return None


def classify_address(address: int, windows: tuple[LinkedRegionWindow, ...]) -> MemoryRegion | None:
    """The region whose window contains *address*, or None (the honest flag
    for an occupant outside every verified window — including the table
    itself being wrong, which is exactly what the measurement's police check
    surfaces)."""
    for entry in windows:
        if entry.window.contains(address):
            return entry.region
    return None
