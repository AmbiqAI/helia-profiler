"""Emit ``pmu-catalog.json``: every PMU counter HPX can capture, by group and SoC.

The registry in ``helia_profiler.platform.counters`` is the source: the counter
catalogue with its event ids and descriptions, the curated ``default``
selection per group, and the groups each SoC's profiling domains admit. The
documentation renders this file rather than restating the tables by hand, so a
counter added to the registry reaches the page on the next build and one
removed cannot linger.

    uv run --isolated --no-dev python tools/docs/extract_pmu.py --source-tree <sha>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SCHEMA_VERSION, dump, provenance, source_tree  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "astro-site" / "src" / "data" / "pmu-catalog.json"


def _group_rows() -> list[dict[str, Any]]:
    from helia_profiler.platform.counters import DEFAULT_COUNTERS, list_counters, list_groups

    rows = []
    for group in list_groups():
        counters = list_counters(group)
        rows.append(
            {
                "group": group,
                "default": list(DEFAULT_COUNTERS.get(group, [])),
                "counters": [
                    {
                        "name": counter.name,
                        "eventId": counter.event_id,
                        "description": counter.description,
                    }
                    for counter in sorted(counters, key=lambda c: c.name)
                ],
            }
        )
    return rows


def _soc_rows() -> list[dict[str, Any]]:
    from helia_profiler.platform.counters import supported_groups_for_domains
    from helia_profiler.platform.registry import list_socs

    rows = []
    for soc in list_socs():
        domains = tuple(soc.profiling_domains)
        rows.append(
            {
                "soc": soc.name,
                "family": str(soc.family.value),
                "domains": list(domains),
                "groups": list(supported_groups_for_domains(domains)),
                "pmuMaxOps": soc.pmu_max_ops,
            }
        )
    return rows


def build(tree: str) -> dict[str, Any]:
    groups = _group_rows()
    socs = _soc_rows()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "pmu-catalog",
        "generatedFrom": provenance(tree),
        "counts": {
            "groups": len(groups),
            "counters": sum(len(group["counters"]) for group in groups),
            "socs": len(socs),
        },
        "groups": groups,
        "socs": socs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--source-tree", type=source_tree, required=True)
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)
    payload = build(args.source_tree)
    if args.stdout:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    dump(payload, args.out)
    counts = payload["counts"]
    print(
        f"wrote {args.out} ({counts['counters']} counters in {counts['groups']} groups, "
        f"{counts['socs']} SoCs)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
