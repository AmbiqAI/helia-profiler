"""Platform model — SoC families, capabilities, and board definitions.

The platform model is a two-level hierarchy: Board → SoC.  SoC determines the
core architecture, PMU capabilities, memory layout, and supported clock modes.
Board selects a concrete EVB (or, in the future, a custom target) that maps to
exactly one SoC.

Architecture note: AP330 (apollo330P) is Cortex-M55 and belongs to the AP5
family despite the "3" in its name.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from types import MappingProxyType

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


class PerfTier(Enum):
    """NSX CPU performance tier — maps directly to ``nsx_perf_mode_e``."""

    LOW = "NSX_PERF_LOW"
    MEDIUM = "NSX_PERF_MEDIUM"
    HIGH = "NSX_PERF_HIGH"


DEFAULT_SYNC_GPIO_PIN = 10


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
    def has_full_pmu(self) -> bool:
        return self.pmu_tier is PmuTier.ARMV8M_PMU

    @property
    def has_dwt(self) -> bool:
        """All supported Cortex-M targets expose the DWT cycle counter."""
        return True

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
# Board definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoardDef:
    """Definition of an evaluation board."""

    name: str  # e.g. "apollo510_evb"
    soc: str  # SoC name key (matches SocDef.name)
    channel: str  # "stable" or "preview"
    psram_kb: int | None = None  # None = inherit SoC default
    default_sync_gpio_pin: int = DEFAULT_SYNC_GPIO_PIN
    starter_profile_board: str | None = None  # derive NSX profile/modules from this board
    description: str = ""

    @property
    def profile_source_board(self) -> str:
        """Board name used to derive starter-profile-owned NSX metadata."""
        return self.starter_profile_board or self.name


@dataclass(frozen=True)
class PlatformRegistry:
    """Resolved platform registry used for one config/run."""

    socs: Mapping[str, SocDef]
    boards: Mapping[str, BoardDef]


# ---------------------------------------------------------------------------
# Built-in registry — EVBs only for initial scope
# ---------------------------------------------------------------------------

_SOCS: dict[str, SocDef] = {}
_BOARDS: dict[str, BoardDef] = {}


def _register_soc(soc: SocDef) -> SocDef:
    _SOCS[soc.name] = soc
    return soc


def _register_board(board: BoardDef) -> BoardDef:
    _BOARDS[board.name] = board
    return board


def _freeze_registry(socs: Mapping[str, SocDef], boards: Mapping[str, BoardDef]) -> PlatformRegistry:
    soc_map = dict(socs)
    board_map = dict(boards)
    for board in board_map.values():
        if board.soc not in soc_map:
            raise ValueError(f"Board '{board.name}' references unknown SoC '{board.soc}'.")
        if board.starter_profile_board is not None and board.starter_profile_board not in board_map:
            raise ValueError(
                f"Board '{board.name}' references unknown starter-profile board "
                f"'{board.starter_profile_board}'."
            )
    return PlatformRegistry(
        socs=MappingProxyType(soc_map),
        boards=MappingProxyType(board_map),
    )


# --- AP3 family (Cortex-M4F) ------------------------------------------------

_register_soc(
    SocDef(
        name="apollo3p",
        family=SocFamily.AP3,
        core=CoreArch.CORTEX_M4,
        pmu_tier=PmuTier.DWT_ONLY,
        has_mve=False,
        memory=MemoryLayout(mram_kb=1024, sram_kb=384, dtcm_kb=64),
        clocks=(
            ClockDomain(
                "cpu",
                (ClockSpeed("lp", 96, PerfTier.LOW),),
                default="lp",
            ),
        ),
        c_define="AM_PART_APOLLO3P",
        cmsis_header="apollo3p.h",
        rtt_scan_ranges=((0x10000000, 0x100000),),
        jlink_device="AMA3B2KK-KBR",
    )
)

_register_board(BoardDef("apollo3p_evb", soc="apollo3p", channel="stable", psram_kb=8192))
_register_board(BoardDef("apollo3p_evb_cygnus", soc="apollo3p", channel="preview", psram_kb=8192))

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

_register_board(BoardDef("apollo4p_evb", soc="apollo4p", channel="preview", psram_kb=32768))
_register_board(BoardDef("apollo4l_evb", soc="apollo4l", channel="preview", psram_kb=32768))
_register_board(BoardDef("apollo4l_blue_evb", soc="apollo4l", channel="preview", psram_kb=32768))
_register_board(
    BoardDef("apollo4p_blue_kbr_evb", soc="apollo4p", channel="preview", psram_kb=32768)
)
_register_board(
    BoardDef("apollo4p_blue_kxr_evb", soc="apollo4p", channel="preview", psram_kb=32768)
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
            psram_kb=32768,
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
        rtt_scan_ranges=((0x20000000, 0x200000),),
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
        c_define="AM_PART_APOLLO510B",
        cmsis_header="apollo510.h",
        rtt_scan_ranges=((0x20000000, 0x200000),),
        jlink_device="AP510NFA-CBR",
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
        rtt_scan_ranges=((0x20000000, 0x200000),),
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
        c_define="AM_PART_APOLLO330P",
        cmsis_header="apollo330P.h",
        rtt_scan_ranges=((0x20000000, 0x200000),),
        jlink_device="AP330NFA-CBR",
        pmu_max_ops=4096,
    )
)

_register_board(
    BoardDef(
        "apollo510_evb",
        soc="apollo510",
        channel="stable",
        default_sync_gpio_pin=29,
    )
)
_register_board(
    BoardDef(
        "apollo510b_evb",
        soc="apollo510b",
        channel="preview",
        default_sync_gpio_pin=29,
    )
)
_register_board(BoardDef("apollo5b_evb", soc="apollo5b", channel="preview"))
_register_board(
    BoardDef(
        "apollo330mP_evb",
        soc="apollo330P",
        channel="preview",
        description="Apollo330 — Cortex-M55 (AP5 family)",
    )
)


# ---------------------------------------------------------------------------
# Public lookup API
# ---------------------------------------------------------------------------


def build_platform_registry(
    *,
    base: PlatformRegistry | None = None,
    socs: Mapping[str, SocDef] | None = None,
    boards: Mapping[str, BoardDef] | None = None,
) -> PlatformRegistry:
    """Return a frozen platform registry for one config/run."""
    if base is None:
        merged_socs = dict(_SOCS)
        merged_boards = dict(_BOARDS)
    else:
        merged_socs = dict(base.socs)
        merged_boards = dict(base.boards)
    if socs:
        merged_socs.update(socs)
    if boards:
        merged_boards.update(boards)
    return _freeze_registry(merged_socs, merged_boards)


def get_soc(name: str, *, registry: PlatformRegistry | None = None) -> SocDef:
    """Look up a SoC definition by name."""
    active = registry or build_platform_registry()
    if name not in active.socs:
        known = ", ".join(sorted(active.socs))
        raise ValueError(f"Unknown SoC '{name}'. Known SoCs: {known}")
    return active.socs[name]


def get_board(name: str, *, registry: PlatformRegistry | None = None) -> BoardDef:
    """Look up a board definition by name."""
    active = registry or build_platform_registry()
    if name not in active.boards:
        known = ", ".join(sorted(active.boards))
        raise ValueError(f"Unknown board '{name}'. Known boards: {known}")
    return active.boards[name]


def get_soc_for_board(board_name: str, *, registry: PlatformRegistry | None = None) -> SocDef:
    """Resolve the SoC definition for a given board."""
    board = get_board(board_name, registry=registry)
    soc = get_soc(board.soc, registry=registry)
    if board.psram_kb is None:
        return soc
    return replace(
        soc,
        memory=replace(soc.memory, psram_kb=board.psram_kb),
    )


def get_default_sync_gpio_pin(
    board_name: str,
    fallback: int = DEFAULT_SYNC_GPIO_PIN,
    *,
    registry: PlatformRegistry | None = None,
) -> int:
    """Return the board's default sync GPIO pin, or *fallback* if unknown."""
    active = registry or build_platform_registry()
    board = active.boards.get(board_name)
    if board is None:
        return fallback
    return board.default_sync_gpio_pin


def list_boards(*, registry: PlatformRegistry | None = None) -> list[BoardDef]:
    """Return all registered boards."""
    active = registry or build_platform_registry()
    return list(active.boards.values())


def list_socs(*, registry: PlatformRegistry | None = None) -> list[SocDef]:
    """Return all registered SoCs."""
    active = registry or build_platform_registry()
    return list(active.socs.values())
