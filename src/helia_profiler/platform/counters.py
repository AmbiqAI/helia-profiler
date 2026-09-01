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

import json
import importlib.resources as resources
import math
from dataclasses import dataclass
from typing import Collection, Mapping

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
# Source: data/armv8m_pmu_events.json, synced from the upstream
# nsx-pmu-armv8m export generated from nsx_pmu_map[] in
# nsx-pmu-armv8m/src/armv8m/nsx_pmu_utils.c
# ---------------------------------------------------------------------------

def _load_counter_catalog() -> dict[str, PmuCounter]:
    catalog_path = resources.files("helia_profiler").joinpath("data/armv8m_pmu_events.json")
    rows = json.loads(catalog_path.read_text(encoding="utf-8"))
    return {
        row["name"]: PmuCounter(
            name=row["name"],
            event_id=int(row["event_id"], 16),
            group=row["group"],
            description=row["description"],
        )
        for row in rows
    }


_COUNTERS: dict[str, PmuCounter] = _load_counter_catalog()


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
        "ARM_PMU_MVE_LDST_RETIRED",
        "ARM_PMU_MVE_STALL",
    ],
}


# Hardware limit: 8 PMU counter slots on Cortex-M55, using 32-bit chained
# counters (2 slots each) to avoid overflow → 4 logical counters per pass.
MAX_COUNTERS_PER_PASS = 4


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
