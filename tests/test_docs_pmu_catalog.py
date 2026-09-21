"""The PMU catalogue the docs render is the counter registry, whole and current."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools" / "docs"))

import extract_pmu  # noqa: E402

TREE = "0" * 40


def test_catalog_covers_every_group_and_counter() -> None:
    from helia_profiler.platform.counters import GROUPS, DEFAULT_COUNTERS

    payload = extract_pmu.build(TREE)
    groups = {row["group"]: row for row in payload["groups"]}
    assert set(groups) == set(GROUPS)
    for name, names in GROUPS.items():
        assert {c["name"] for c in groups[name]["counters"]} == set(names)
        assert groups[name]["default"] == list(DEFAULT_COUNTERS.get(name, []))
        assert set(groups[name]["default"]) <= set(names)
    assert payload["counts"]["counters"] == sum(len(v) for v in GROUPS.values())


def test_catalog_lists_every_soc_with_its_groups() -> None:
    from helia_profiler.platform.counters import supported_groups_for_domains
    from helia_profiler.platform.registry import list_socs

    payload = extract_pmu.build(TREE)
    rows = {row["soc"]: row for row in payload["socs"]}
    assert set(rows) == {soc.name for soc in list_socs()}
    for soc in list_socs():
        assert rows[soc.name]["groups"] == list(supported_groups_for_domains(soc.profiling_domains))
        assert rows[soc.name]["pmuMaxOps"] == soc.pmu_max_ops
