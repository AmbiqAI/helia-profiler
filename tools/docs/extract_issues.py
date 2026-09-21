"""Emit ``issues.json``: every machine-readable diagnostic code HPX can emit.

The registries in ``helia_profiler.results.issues`` are the source. This is
the structured rendition of the generated Reference issue-codes page: a reader wanting the prose gets the
generated page, a consumer wanting to key on a code gets this file.

The parameterized families are expanded here rather than left as a pattern.
``code_for`` is the only thing that knows the wire string, which doubles the
``power`` prefix for the dimensions that already start with it, so expanding
through it is the one way the published list cannot drift from the emitted
one.

    uv run --isolated --no-dev python tools/docs/extract_issues.py --source-tree <sha>
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
DEFAULT_OUT = REPO_ROOT / "astro-site" / "src" / "data" / "issues.json"


def _issue_rows() -> list[dict[str, Any]]:
    from helia_profiler.results.issues import ISSUE_REGISTRY

    rows = []
    for code in sorted(ISSUE_REGISTRY):
        spec = ISSUE_REGISTRY[code]
        rows.append(
            {
                "code": code.value,
                "description": spec.description,
                "severity": spec.severity.value if spec.severity else None,
                "modeDependent": spec.mode_dependent,
                "internalSeverity": (
                    spec.internal_severity.value if spec.internal_severity else None
                ),
                "externalSeverity": (
                    spec.external_severity.value if spec.external_severity else None
                ),
                "metricGroup": spec.metric_group,
            }
        )
    return rows


def _comparability_rows() -> list[dict[str, Any]]:
    from helia_profiler.results.issues import COMPARABILITY_REGISTRY

    rows = []
    for code in sorted(COMPARABILITY_REGISTRY):
        spec = COMPARABILITY_REGISTRY[code]
        rows.append(
            {
                "code": code.value,
                "severity": spec.severity.value,
                "description": spec.description,
                "metricGroup": spec.metric_group,
            }
        )
    return rows


def _family_rows() -> list[dict[str, Any]]:
    from helia_profiler.results.issues import COMPARABILITY_FAMILIES

    rows = []
    for family in COMPARABILITY_FAMILIES:
        rows.append(
            {
                "pattern": family.pattern,
                "severity": family.severity.value,
                "description": family.description,
                "metricGroup": family.metric_group,
                "dimensions": [dimension.value for dimension in family.dimensions],
                "codes": [family.code_for(dimension) for dimension in family.dimensions],
            }
        )
    return rows


def build(tree: str) -> dict[str, Any]:
    issues = _issue_rows()
    comparability = _comparability_rows()
    families = _family_rows()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "issue-codes",
        "generatedFrom": provenance(tree),
        "counts": {
            "issues": len(issues),
            "comparability": len(comparability),
            "families": len(families),
            "familyCodes": sum(len(family["codes"]) for family in families),
        },
        "issues": issues,
        "comparability": comparability,
        "families": families,
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
        f"wrote {args.out} ({counts['issues']} issue codes, "
        f"{counts['comparability']} comparability codes, {counts['families']} families)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
