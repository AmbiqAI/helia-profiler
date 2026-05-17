"""Platform model — SoC families, capabilities, and board definitions.

The platform model is a two-level hierarchy: Board → SoC.  SoC determines the
core architecture, PMU capabilities, memory layout, and supported clock modes.
Board selects a concrete EVB (or, in the future, a custom target) that maps to
exactly one SoC.

Architecture note: AP330 (apollo330P) is Cortex-M55 and belongs to the AP5
family despite the "3" in its name.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

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
class ClockConfig:
    """Supported clock modes in MHz."""

    lp_mhz: int = 96
    hp_mhz: int | None = None  # None = HP mode not available


@dataclass(frozen=True)
class SocDef:
    """Definition of an Ambiq SoC relevant to profiling."""

    name: str  # e.g. "apollo510"
    family: SocFamily
    core: CoreArch
    pmu_tier: PmuTier
    has_mve: bool  # Helium / MVE vector extensions
    memory: MemoryLayout
    clock: ClockConfig
    sdk_tier: str  # "r3", "r4", or "r5" — maps to nsx-ambiqsuite-r*
    c_define: str  # e.g. "AM_PART_APOLLO510"
    jlink_device: str = ""  # J-Link device string (e.g. "AP510NFA-CBR")
    pmu_max_ops: int = 2048  # Max PMU accumulator operations (layers)

    @property
    def has_full_pmu(self) -> bool:
        return self.pmu_tier is PmuTier.ARMV8M_PMU


# ---------------------------------------------------------------------------
# Board definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoardDef:
    """Definition of an evaluation board."""

    name: str  # e.g. "apollo510_evb"
    soc: str  # SoC name key (matches SocDef.name)
    channel: str  # "stable" or "preview"
    description: str = ""


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


# --- AP3 family (Cortex-M4F) ------------------------------------------------

_register_soc(
    SocDef(
        name="apollo3p",
        family=SocFamily.AP3,
        core=CoreArch.CORTEX_M4,
        pmu_tier=PmuTier.DWT_ONLY,
        has_mve=False,
        memory=MemoryLayout(mram_kb=1024, sram_kb=384, dtcm_kb=64),
        clock=ClockConfig(lp_mhz=96),
        sdk_tier="r3",
        c_define="AM_PART_APOLLO3P",
        jlink_device="AMA3B2KK-KBR",
    )
)

_register_board(BoardDef("apollo3p_evb", soc="apollo3p", channel="stable"))

# --- AP4 family (Cortex-M4F) ------------------------------------------------

_register_soc(
    SocDef(
        name="apollo4p",
        family=SocFamily.AP4,
        core=CoreArch.CORTEX_M4,
        pmu_tier=PmuTier.DWT_ONLY,
        has_mve=False,
        memory=MemoryLayout(mram_kb=2000, sram_kb=1024, dtcm_kb=384),
        clock=ClockConfig(lp_mhz=96, hp_mhz=192),
        sdk_tier="r4",
        c_define="AM_PART_APOLLO4P",
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
        clock=ClockConfig(lp_mhz=96, hp_mhz=192),
        sdk_tier="r4",
        c_define="AM_PART_APOLLO4L",
        jlink_device="AMAP42KL-KBR",
    )
)

_register_board(BoardDef("apollo4p_evb", soc="apollo4p", channel="preview"))

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
            psram_kb=32168,
            nvm_kb=8192,
        ),
        clock=ClockConfig(lp_mhz=96, hp_mhz=250),
        sdk_tier="r5",
        c_define="AM_PART_APOLLO510",
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
            psram_kb=32168,
        ),
        clock=ClockConfig(lp_mhz=96, hp_mhz=250),
        sdk_tier="r5",
        c_define="AM_PART_APOLLO510B",
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
            psram_kb=32168,
        ),
        clock=ClockConfig(lp_mhz=96, hp_mhz=250),
        sdk_tier="r5",
        c_define="AM_PART_APOLLO5B",
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
            psram_kb=32168,
        ),
        clock=ClockConfig(lp_mhz=96, hp_mhz=250),
        sdk_tier="r5",
        c_define="AM_PART_APOLLO330P",
        jlink_device="AP330NFA-CBR",
        pmu_max_ops=4096,
    )
)

_register_board(BoardDef("apollo510_evb", soc="apollo510", channel="stable"))
_register_board(BoardDef("apollo510b_evb", soc="apollo510b", channel="preview"))
_register_board(BoardDef("apollo5b_evb", soc="apollo5b", channel="preview"))
_register_board(
    BoardDef(
        "apollo330mP_evb",
        soc="apollo330P",
        channel="preview",
        description="Apollo330 — Cortex-M55 (AP5 family)",
    )
)

# --- Atomiq family (Cortex-M55 + Ethos-U85 NPU) ------------------------------

_register_soc(
    SocDef(
        name="atomiq110",
        family=SocFamily.AP5,  # CM55 + MVE + full PMU, same as AP5
        core=CoreArch.CORTEX_M55,
        pmu_tier=PmuTier.ARMV8M_PMU,
        has_mve=True,
        memory=MemoryLayout(
            sram_kb=3072,
            dtcm_kb=512,
            itcm_kb=256,
        ),
        clock=ClockConfig(lp_mhz=25, hp_mhz=25),  # FPGA: 1/10 speed
        sdk_tier="r6",
        c_define="AM_PART_ATOMIQ110",
        jlink_device="AT110NFA-CBR",
        pmu_max_ops=4096,
    )
)

_register_board(
    BoardDef(
        "atomiq110_fpga_turbo",
        soc="atomiq110",
        channel="preview",
        description="Atomiq110 FPGA turbo — Cortex-M55 + Ethos-U85 (1/10 speed)",
    )
)


# ---------------------------------------------------------------------------
# Public lookup API
# ---------------------------------------------------------------------------


def get_soc(name: str) -> SocDef:
    """Look up a SoC definition by name."""
    if name not in _SOCS:
        known = ", ".join(sorted(_SOCS))
        raise ValueError(f"Unknown SoC '{name}'. Known SoCs: {known}")
    return _SOCS[name]


def get_board(name: str) -> BoardDef:
    """Look up a board definition by name."""
    if name not in _BOARDS:
        known = ", ".join(sorted(_BOARDS))
        raise ValueError(f"Unknown board '{name}'. Known boards: {known}")
    return _BOARDS[name]


def get_soc_for_board(board_name: str) -> SocDef:
    """Resolve the SoC definition for a given board."""
    board = get_board(board_name)
    return get_soc(board.soc)


def list_boards() -> list[BoardDef]:
    """Return all registered boards."""
    return list(_BOARDS.values())


def list_socs() -> list[SocDef]:
    """Return all registered SoCs."""
    return list(_SOCS.values())
