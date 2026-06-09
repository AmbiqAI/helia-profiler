"""PMU counter registry — catalogue of ARM PMU events by compute unit.

Every counter the profiler firmware can measure is declared here with its
ARM event ID, human-readable name, and *compute-unit group*.  The registry
supports three selection modes:

* ``"default"`` — a curated subset most users care about.
* ``"all"``     — every counter in the group (requires multiple passes).
* ``[name, …]`` — explicit list of counter names.

Pass planning (splitting N selected counters into batches of
``MAX_COUNTERS_PER_PASS``) is also handled here so firmware generation
receives a ready-made list of passes.

Future compute units can be added by extending ``_COUNTERS`` and
``_GROUPS`` with a new group key.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Collection, Mapping, Sequence

# ---------------------------------------------------------------------------
# Counter descriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PmuCounter:
    """A single PMU event that the hardware can count."""

    name: str
    event_id: int
    group: str  # "cpu", "memory", "mve", …
    description: str = ""


# ---------------------------------------------------------------------------
# M55 / ARMv8-M counter catalogue
#
# Source: nsx_pmu_map[] in nsx-pmu-armv8m/src/armv8m/nsx_pmu_utils.c
# ---------------------------------------------------------------------------

_COUNTERS: dict[str, PmuCounter] = {}


def _c(name: str, eid: int, group: str, desc: str = "") -> None:
    _COUNTERS[name] = PmuCounter(name=name, event_id=eid, group=group, description=desc)


# --- CPU core events ---
_c("ARM_PMU_SW_INCR", 0x0000, "cpu", "Software increment")
_c("ARM_PMU_L1I_CACHE_REFILL", 0x0001, "cpu", "L1 I-Cache refill")
_c("ARM_PMU_L1D_CACHE_REFILL", 0x0003, "memory", "L1 D-Cache refill")
_c("ARM_PMU_L1D_CACHE", 0x0004, "memory", "L1 D-Cache access")
_c("ARM_PMU_LD_RETIRED", 0x0006, "cpu", "Memory-reading instruction retired")
_c("ARM_PMU_ST_RETIRED", 0x0007, "cpu", "Memory-writing instruction retired")
_c("ARM_PMU_INST_RETIRED", 0x0008, "cpu", "Instruction retired")
_c("ARM_PMU_EXC_TAKEN", 0x0009, "cpu", "Exception entry")
_c("ARM_PMU_EXC_RETURN", 0x000A, "cpu", "Exception return")
_c("ARM_PMU_PC_WRITE_RETIRED", 0x000C, "cpu", "Software PC change retired")
_c("ARM_PMU_BR_IMMED_RETIRED", 0x000D, "cpu", "Immediate branch retired")
_c("ARM_PMU_BR_RETURN_RETIRED", 0x000E, "cpu", "Function return retired")
_c("ARM_PMU_UNALIGNED_LDST_RETIRED", 0x000F, "cpu", "Unaligned load/store retired")
_c("ARM_PMU_CPU_CYCLES", 0x0011, "cpu", "CPU cycle")
_c("ARM_PMU_MEM_ACCESS", 0x0013, "memory", "Data memory access")
_c("ARM_PMU_L1I_CACHE", 0x0014, "memory", "L1 I-Cache access")
_c("ARM_PMU_L1D_CACHE_WB", 0x0015, "memory", "L1 D-Cache write-back")
_c("ARM_PMU_BUS_ACCESS", 0x0019, "memory", "Bus access")
_c("ARM_PMU_MEMORY_ERROR", 0x001A, "memory", "Local memory error")
_c("ARM_PMU_BUS_CYCLES", 0x001D, "memory", "Bus cycle")
_c("ARM_PMU_L1D_CACHE_ALLOCATE", 0x001F, "memory", "L1 D-Cache allocate (no refill)")
_c("ARM_PMU_BR_RETIRED", 0x0021, "cpu", "Branch retired")
_c("ARM_PMU_BR_MIS_PRED_RETIRED", 0x0022, "cpu", "Mispredicted branch retired")
_c("ARM_PMU_STALL_FRONTEND", 0x0023, "cpu", "Frontend stall cycle")
_c("ARM_PMU_STALL_BACKEND", 0x0024, "cpu", "Backend stall cycle")
_c("ARM_PMU_LL_CACHE_RD", 0x0036, "memory", "Last-level cache read")
_c("ARM_PMU_LL_CACHE_MISS_RD", 0x0037, "memory", "Last-level cache read miss")
_c("ARM_PMU_L1D_CACHE_MISS_RD", 0x0039, "memory", "L1 D-Cache read miss")
_c("ARM_PMU_STALL", 0x003C, "cpu", "Stall cycle")
_c("ARM_PMU_L1D_CACHE_RD", 0x0040, "memory", "L1 D-Cache read")
_c("ARM_PMU_LE_RETIRED", 0x0100, "cpu", "Loop end retired")
_c("ARM_PMU_LE_CANCEL", 0x0108, "cpu", "Loop end cancelled")
_c("ARM_PMU_SE_CALL_S", 0x0114, "cpu", "Secure call (to S)")
_c("ARM_PMU_SE_CALL_NS", 0x0115, "cpu", "Secure call (to NS)")

# --- MVE events ---
_c("ARM_PMU_MVE_INST_RETIRED", 0x0200, "mve", "MVE instruction retired")
_c("ARM_PMU_MVE_FP_RETIRED", 0x0204, "mve", "MVE FP instruction retired")
_c("ARM_PMU_MVE_FP_HP_RETIRED", 0x0208, "mve", "MVE half-precision FP retired")
_c("ARM_PMU_MVE_FP_SP_RETIRED", 0x020C, "mve", "MVE single-precision FP retired")
_c("ARM_PMU_MVE_FP_MAC_RETIRED", 0x0214, "mve", "MVE FP MAC retired")
_c("ARM_PMU_MVE_INT_RETIRED", 0x0224, "mve", "MVE integer instruction retired")
_c("ARM_PMU_MVE_INT_MAC_RETIRED", 0x0228, "mve", "MVE integer MAC retired")
_c("ARM_PMU_MVE_LDST_RETIRED", 0x0238, "mve", "MVE load/store retired")
_c("ARM_PMU_MVE_LD_RETIRED", 0x023C, "mve", "MVE load retired")
_c("ARM_PMU_MVE_ST_RETIRED", 0x0240, "mve", "MVE store retired")
_c("ARM_PMU_MVE_LDST_CONTIG_RETIRED", 0x0244, "mve", "MVE contiguous load/store retired")
_c("ARM_PMU_MVE_LD_CONTIG_RETIRED", 0x0248, "mve", "MVE contiguous load retired")
_c("ARM_PMU_MVE_ST_CONTIG_RETIRED", 0x024C, "mve", "MVE contiguous store retired")
_c("ARM_PMU_MVE_LDST_NONCONTIG_RETIRED", 0x0250, "mve", "MVE non-contiguous load/store retired")
_c("ARM_PMU_MVE_LD_NONCONTIG_RETIRED", 0x0254, "mve", "MVE non-contiguous load retired")
_c("ARM_PMU_MVE_ST_NONCONTIG_RETIRED", 0x0258, "mve", "MVE non-contiguous store retired")
_c("ARM_PMU_MVE_LDST_MULTI_RETIRED", 0x025C, "mve", "MVE multi-register load/store retired")
_c("ARM_PMU_MVE_LD_MULTI_RETIRED", 0x0260, "mve", "MVE multi-register load retired")
_c("ARM_PMU_MVE_ST_MULTI_RETIRED", 0x0264, "mve", "MVE multi-register store retired")
_c("ARM_PMU_MVE_LDST_UNALIGNED_RETIRED", 0x028C, "mve", "MVE unaligned load/store retired")
_c("ARM_PMU_MVE_LD_UNALIGNED_RETIRED", 0x0290, "mve", "MVE unaligned load retired")
_c("ARM_PMU_MVE_ST_UNALIGNED_RETIRED", 0x0294, "mve", "MVE unaligned store retired")
_c(
    "ARM_PMU_MVE_LDST_UNALIGNED_NONCONTIG_RETIRED",
    0x0298,
    "mve",
    "MVE unaligned non-contiguous load/store retired",
)
_c("ARM_PMU_MVE_VREDUCE_RETIRED", 0x02A0, "mve", "MVE vector reduction retired")
_c("ARM_PMU_MVE_VREDUCE_FP_RETIRED", 0x02A4, "mve", "MVE FP vector reduction retired")
_c("ARM_PMU_MVE_VREDUCE_INT_RETIRED", 0x02A8, "mve", "MVE integer vector reduction retired")
_c("ARM_PMU_MVE_PRED", 0x02B8, "mve", "Cycles with predicated MVE beats")
_c("ARM_PMU_MVE_STALL", 0x02CC, "mve", "MVE stall cycle")
_c("ARM_PMU_MVE_STALL_RESOURCE", 0x02CD, "mve", "MVE stall — resource conflict")
_c("ARM_PMU_MVE_STALL_RESOURCE_MEM", 0x02CE, "mve", "MVE stall — memory resource conflict")
_c("ARM_PMU_MVE_STALL_RESOURCE_FP", 0x02CF, "mve", "MVE stall — FP resource conflict")
_c("ARM_PMU_MVE_STALL_RESOURCE_INT", 0x02D0, "mve", "MVE stall — integer resource conflict")
_c("ARM_PMU_MVE_STALL_BREAK", 0x02D3, "mve", "MVE stall — chain break")
_c("ARM_PMU_MVE_STALL_DEPENDENCY", 0x02D4, "mve", "MVE stall — register dependency")

# --- TCM events ---
_c("ARM_PMU_ITCM_ACCESS", 0x4007, "memory", "Instruction TCM access")
_c("ARM_PMU_DTCM_ACCESS", 0x4008, "memory", "Data TCM access")


# ---------------------------------------------------------------------------
# Compute-unit groups and curated defaults
# ---------------------------------------------------------------------------

#: All group names that have counters registered.
GROUPS: dict[str, list[str]] = {}
for _ctr in _COUNTERS.values():
    GROUPS.setdefault(_ctr.group, []).append(_ctr.name)

#: Curated "default" set per group — the most useful counters for typical ML
#: workloads.  Designed to fit in a single pass (≤ 4 counters each).
DEFAULT_COUNTERS: dict[str, list[str]] = {
    "cpu": [
        "ARM_PMU_CPU_CYCLES",
        "ARM_PMU_INST_RETIRED",
        "ARM_PMU_STALL_FRONTEND",
        "ARM_PMU_STALL_BACKEND",
    ],
    "memory": [
        "ARM_PMU_MEM_ACCESS",
        "ARM_PMU_L1D_CACHE_REFILL",
        "ARM_PMU_BUS_ACCESS",
        "ARM_PMU_BUS_CYCLES",
    ],
    "mve": [
        "ARM_PMU_MVE_INST_RETIRED",
        "ARM_PMU_MVE_INT_MAC_RETIRED",
        "ARM_PMU_MVE_LDST_MULTI_RETIRED",
        "ARM_PMU_MVE_STALL",
    ],
}


# Hardware limit: 8 PMU counter slots on Cortex-M55, using 32-bit chained
# counters (2 slots each) to avoid overflow → 4 logical counters per pass.
MAX_COUNTERS_PER_PASS = 4


# ---------------------------------------------------------------------------
# Legacy preset → new counter selection mapping
# ---------------------------------------------------------------------------

#: Maps old ``pmu_presets`` names to ``(group, selection)`` tuples so the
#: new system is fully backward-compatible.
LEGACY_PRESET_MAP: dict[str, tuple[str, str]] = {
    "basic_cpu": ("cpu", "default"),
    "memory": ("memory", "default"),
    "mve": ("mve", "default"),
    "ml_default": ("cpu", "default"),  # legacy alias
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CounterPass:
    """A batch of counters that fit in a single firmware PMU pass."""

    group: str
    pass_index: int
    counters: tuple[PmuCounter, ...]

    @property
    def name(self) -> str:
        """Pass name used in the firmware protocol (e.g. ``mve_0``)."""
        return f"{self.group}_{self.pass_index}"


def get_counter(name: str) -> PmuCounter:
    """Look up a counter by name.  Raises ``ValueError`` if unknown."""
    ctr = _COUNTERS.get(name)
    if ctr is None:
        raise ValueError(
            f"Unknown PMU counter '{name}'.  Available: {', '.join(sorted(_COUNTERS))}"
        )
    return ctr


def list_counters(group: str | None = None) -> list[PmuCounter]:
    """Return all registered counters, optionally filtered by group."""
    if group is not None:
        names = GROUPS.get(group, [])
        return [_COUNTERS[n] for n in names]
    return list(_COUNTERS.values())


def list_groups() -> list[str]:
    """Return all registered group names."""
    return sorted(GROUPS.keys())


def supported_groups_for_domains(domains: Collection[str]) -> tuple[str, ...]:
    """Return counter groups supported by the active SoC/domain surface.

    ``domains`` comes from ``SocDef.profiling_domains`` and may include
    future domains before this module has counters for them. Only groups that
    both exist in the registry and are present on the SoC are returned.
    """
    domain_set = set(domains)
    return tuple(group for group in list_groups() if group in domain_set)


def validate_group_selection(
    selection: Mapping[str, str | list[str]],
    *,
    supported_groups: Collection[str],
) -> None:
    """Reject PMU group selections unsupported by the target SoC.

    This validation is intentionally separate from ``resolve_counters()`` so
    callers can fail with a capability-specific message before any firmware
    generation or build work starts.
    """
    supported = set(supported_groups)
    requested = set(selection)
    unsupported = sorted(requested - supported)
    if not unsupported:
        return
    supported_text = ", ".join(sorted(supported)) if supported else "none"
    raise ValueError(
        "PMU counter groups not supported for this target: "
        f"{', '.join(unsupported)}. Supported groups for this SoC: {supported_text}."
    )


def validate_legacy_presets(
    presets: Sequence[str],
    *,
    supported_groups: Collection[str],
) -> None:
    """Validate legacy preset names against the target's supported groups."""
    selection = resolve_legacy_presets(presets)
    validate_group_selection(selection, supported_groups=supported_groups)


def resolve_counters(
    selection: dict[str, str | list[str]],
) -> list[PmuCounter]:
    """Resolve a selection map to a flat list of :class:`PmuCounter`.

    *selection* maps group name → one of:
      - ``"default"`` — curated default set for that group.
      - ``"all"``     — every counter in the group.
      - ``["name1", "name2", …]`` — explicit counter names.

    Counters are returned in group order, then declaration order within
    each group.
    """
    result: list[PmuCounter] = []
    seen: set[str] = set()

    for group, sel in selection.items():
        if isinstance(sel, str):
            if sel == "default":
                names = DEFAULT_COUNTERS.get(group, [])
            elif sel == "all":
                names = GROUPS.get(group, [])
            else:
                raise ValueError(
                    f"Invalid selection '{sel}' for group '{group}'.  "
                    f"Use 'default', 'all', or an explicit list."
                )
        elif isinstance(sel, list):
            names = sel
        else:
            raise TypeError(f"Selection for group '{group}' must be str or list, got {type(sel)}")

        if not names:
            raise ValueError(f"No counters found for group '{group}'.")

        for name in names:
            ctr = get_counter(name)
            if ctr.name not in seen:
                result.append(ctr)
                seen.add(ctr.name)

    return result


def plan_passes(
    counters: list[PmuCounter],
    max_per_pass: int = MAX_COUNTERS_PER_PASS,
) -> list[CounterPass]:
    """Batch *counters* into firmware passes, grouped by compute unit.

    Counters are grouped by their ``.group`` field first, then split into
    batches of *max_per_pass*.  This produces the minimal number of
    firmware inference passes needed to capture all requested counters.
    """
    # Group counters by compute unit
    by_group: dict[str, list[PmuCounter]] = {}
    for ctr in counters:
        by_group.setdefault(ctr.group, []).append(ctr)

    passes: list[CounterPass] = []
    for group, ctrs in by_group.items():
        num_passes = math.ceil(len(ctrs) / max_per_pass)
        for i in range(num_passes):
            batch = ctrs[i * max_per_pass : (i + 1) * max_per_pass]
            passes.append(
                CounterPass(
                    group=group,
                    pass_index=i,
                    counters=tuple(batch),
                )
            )

    return passes


def resolve_legacy_presets(
    preset_names: tuple[str, ...] | list[str],
) -> dict[str, str | list[str]]:
    """Convert legacy ``pmu_presets`` list into the new selection format.

    Returns a selection dict suitable for :func:`resolve_counters`.
    """
    selection: dict[str, str | list[str]] = {}
    for name in preset_names:
        mapping = LEGACY_PRESET_MAP.get(name)
        if mapping is None:
            raise ValueError(
                f"Unknown legacy preset '{name}'.  "
                f"Known presets: {', '.join(sorted(LEGACY_PRESET_MAP))}"
            )
        group, sel = mapping
        # If already present, don't downgrade "all" to "default"
        if group not in selection:
            selection[group] = sel
    return selection
