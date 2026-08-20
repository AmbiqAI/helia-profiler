"""Census contract for `PowerResult.metadata` (#154 Phase 2).

``snapshots/power_metadata_census.json`` records every top-level key any
writer puts into power metadata, taken by direct code audit at ``80fb77f``.
The ``PowerMetadata`` model is provably complete against it: this test file
grows a completeness assertion (model fields ↔ census keys) once the model
lands; until then it pins the census file's own integrity.
"""

from __future__ import annotations

import json
from pathlib import Path

CENSUS_PATH = Path(__file__).parent / "snapshots" / "power_metadata_census.json"

VALID_DISPOSITIONS = {"typed", "artifact_only"}


def _census() -> dict:
    return json.loads(CENSUS_PATH.read_text(encoding="utf-8"))


def test_census_is_well_formed():
    census = _census()
    keys = census["keys"]
    assert keys, "census must not be empty"
    assert list(keys) == sorted(keys), "census keys must be sorted"
    for name, entry in keys.items():
        assert entry.get("writers"), f"{name}: every key needs at least one writer"
        assert entry.get("disposition") in VALID_DISPOSITIONS, name
        assert "readers" in entry, name


def test_artifact_only_keys_have_no_live_readers():
    # 'artifact_only' means written-never-read in src/: a key that gains a
    # live reader must be promoted to a typed field, not read by string.
    census = _census()
    for name, entry in census["keys"].items():
        if entry["disposition"] == "artifact_only":
            assert not entry["readers"] or entry["readers"] == ["report/summary.py"], (
                f"{name} is artifact_only but has live readers beyond the "
                f"report passthrough: {entry['readers']}"
            )
