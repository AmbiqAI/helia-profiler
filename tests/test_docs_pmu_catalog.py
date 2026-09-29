"""The PMU catalogue the docs render is the counter registry, whole and current."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

TOOLS_DOCS = Path(__file__).resolve().parents[1] / "tools" / "docs"
TREE = "0" * 40


def _load_extractor():
    """Load tools/docs/extract_pmu.py by path; tools/ is not a package on the import path."""
    if str(TOOLS_DOCS) not in sys.path:
        sys.path.insert(0, str(TOOLS_DOCS))
    spec = importlib.util.spec_from_file_location("extract_pmu", TOOLS_DOCS / "extract_pmu.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


extract_pmu = _load_extractor()


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
