"""Drift guard: docs/architecture/compatibility-baseline.md vs the baseline.

The page's qualified-reference table is hand-maintained prose mirroring
``src/helia_profiler/data/compatibility-baseline-v1.json`` plus two code
constants — and until #193 it had no guard (unlike pipeline.md, pinned by
test_pipeline.py): its first run caught two rows that were never added
to the doc (``nsx-executorch`` as both a project and an engine entry).

Mechanics follow the pipeline.md precedent: the doc stays hand-written, the
test extracts the table region and cross-checks it against the data. Refs in
the doc are ellipsized (``a9f4ec25…1132``), so matching is by 8-hex prefix in
both directions. The JSON ``modules`` block is deliberately NOT tabulated
(the doc covers module tiers in prose); every module ref that has a project
counterpart is guarded through the project row.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from helia_profiler.engines.helia_rt.artifacts import (
    HELIART_MIN_VERSION,
    HELIART_VERSION,
)

_REPO = Path(__file__).resolve().parents[1]
_DOC = _REPO / "docs" / "architecture" / "compatibility-baseline.md"
_JSON = _REPO / "src" / "helia_profiler" / "data" / "compatibility-baseline-v1.json"

#: Engine names as the doc table spells them (prose, not JSON keys).
_ENGINE_DOC_NAMES = {
    "helia-rt": "heliaRT",
    "helia-aot": "heliaAOT",
    "tflm": "tflm",
    "executorch": "executorch",
}


def _table_region(doc: str) -> str:
    """The qualified-reference table: from its header row to the first
    non-table line after it."""
    lines = doc.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("| Identity |"))
    end = start
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    return "\n".join(lines[start:end])


def test_every_baseline_entry_has_a_doc_row_with_its_ref():
    doc = _DOC.read_text(encoding="utf-8")
    table = _table_region(doc)
    data = json.loads(_JSON.read_text(encoding="utf-8"))

    for name, project in data["projects"].items():
        # helia-rt's project ref is documented on the heliaRT engine row.
        row_name = "heliaRT" if name == "helia-rt" else f"`{name}`"
        assert row_name in table, (
            f"project '{name}' has no row in the compatibility-baseline.md "
            "table — update docs/architecture/compatibility-baseline.md and "
            "src/helia_profiler/data/compatibility-baseline-v1.json together"
        )
        assert project["ref"][:8] in table, (
            f"project '{name}' ref {project['ref'][:12]}… is not in the doc "
            "table — the doc has drifted from the baseline JSON"
        )

    for name, engine in data["engines"].items():
        assert name in _ENGINE_DOC_NAMES, (
            f"engine '{name}' is new to the baseline JSON -- add it to "
            "_ENGINE_DOC_NAMES here and give it a doc row"
        )
        doc_name = _ENGINE_DOC_NAMES[name]
        assert doc_name in table, f"engine '{name}' has no doc row"
        if "version" in engine:
            assert str(engine["version"]) in table, (
                f"engine '{name}' version={engine['version']} missing from the doc table"
            )
        for key in ("min_version", "max_version_exclusive"):
            # Key-scoped, not table-wide: a one-sided range bump can alias an
            # unrelated value elsewhere in the table (#207, mutation r
            # -- raising min to the old max passed a bare substring check).
            if key in engine:
                assert f"{key}={engine[key]}" in table, (
                    f"engine '{name}' {key}={engine[key]} missing from the doc "
                    "table (expected as 'key=value')"
                )
        if "ref" in engine:
            assert engine["ref"][:8] in table, (
                f"engine '{name}' ref missing from the doc table"
            )
        if engine.get("governed_by_modules"):
            row = next(line for line in table.splitlines() if f"| {doc_name} |" in line)
            assert "governed" in row, f"'{doc_name}' row no longer states module governance"

    package = data["neuralspotx"]
    assert package["version"] in table
    assert package["sha256"][:8] in table


def test_every_doc_ref_exists_in_the_baseline():
    """The reverse direction: a stale ellipsized ref in the doc (left behind
    by a baseline bump) fails here."""
    table = _table_region(_DOC.read_text(encoding="utf-8"))
    data = json.loads(_JSON.read_text(encoding="utf-8"))

    known_hex = {p["ref"] for p in data["projects"].values()}
    known_hex |= {e["ref"] for e in data["engines"].values() if "ref" in e}
    known_hex.add(data["neuralspotx"]["sha256"])

    # Hygiene first: a stale ref typed with an ASCII "..." or truncated below
    # 8 hex would be INVISIBLE to the reverse check below -- refuse the format
    # outright (#207, mutations j/c2).
    malformed = re.findall(r"`[0-9a-f]{4,7}…|`[0-9a-f]{4,}\.{2,}", table)
    assert not malformed, (
        f"doc table refs must be >=8 hex chars followed by a real ellipsis "
        f"(U+2026); malformed: {malformed}"
    )
    for prefix in re.findall(r"`([0-9a-f]{8})[0-9a-f]*…", table):
        assert any(ref.startswith(prefix) for ref in known_hex), (
            f"doc table ref prefix '{prefix}…' matches nothing in "
            "compatibility-baseline-v1.json — stale row from a baseline bump?"
        )


def test_doc_identity_and_code_constants_are_current():
    doc = _DOC.read_text(encoding="utf-8")
    data = json.loads(_JSON.read_text(encoding="utf-8"))

    # The headline identity line carries the FULL baseline id, not just the
    # version: a re-pin within the same nsx version changes only the id date,
    # and that is exactly the bump-forgets-doc case (#207, mutation l).
    assert data["baseline_id"] in doc, (
        f"the doc headline no longer names the current baseline id "
        f"'{data['baseline_id']}' -- update compatibility-baseline.md"
    )

    # The heliaRT row mirrors the canonical code constants (the JSON side of
    # this pairing is pinned by test_compatibility.py).
    table = _table_region(doc)
    assert HELIART_VERSION in table
    assert f"min supported `{HELIART_MIN_VERSION}`" in table
