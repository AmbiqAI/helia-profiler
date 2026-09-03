"""Census contract for `PowerResult.metadata` (#154 Phase 2).

``snapshots/power_metadata_census.json`` records every top-level key any
writer put into power metadata, taken by direct code audit at ``80fb77f``.
The ``PowerMetadata`` model is provably complete against it: its field set
must equal the census key set exactly, and no string-keyed access to those
keys may reappear in ``src/`` outside the model module (the report layer
reads the flat ``to_metadata_dict()`` view, which is the sanctioned
serialization boundary).
"""

from __future__ import annotations

import json
import re
from dataclasses import fields
from pathlib import Path

from helia_profiler.power.diagnostics import GateDurationIntegrity
from helia_profiler.power.metadata import (
    MeasurementScope,
    ObservationMode,
    PowerIntegrity,
    PowerMetadata,
)

CENSUS_PATH = Path(__file__).parent / "snapshots" / "power_metadata_census.json"
ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "helia_profiler"

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


def test_model_fields_equal_census_keys():
    # A key added to the census (a new writer) without a model field — or a
    # field added without recording who writes/reads it — fails here.
    assert {f.name for f in fields(PowerMetadata)} == set(_census()["keys"])


def test_emission_covers_every_census_key_and_only_those():
    census = _census()
    populated = PowerMetadata()
    for f in fields(PowerMetadata):
        if getattr(populated, f.name) is None:
            # Any non-None sentinel suffices: emission keys are what's pinned.
            object.__setattr__(populated, f.name, _sentinel_for(f.name))
    emitted = populated.to_metadata_dict()
    assert set(emitted) == set(census["keys"])


def _sentinel_for(name: str):
    if name == "gate_duration_integrity":
        return GateDurationIntegrity(measured_s=1.0, expected_s=1.0, tolerance_s=0.1)
    if name in {
        "sync",
        "sync_timing_s",
        "gate_failure",
        "window_clock_ceiling",
        "target_lifecycle",
    }:

        class _Flat:
            def to_metadata(self):
                return {"x": 1}

        return _Flat()
    if name == "measurement_scope":
        return MeasurementScope.GPIO_GATED_CLEAN_WINDOW
    if name == "observation_mode":
        return ObservationMode.GPIO_GATED
    if name == "integrity":
        return PowerIntegrity.VALID
    return 1


def test_no_string_keyed_census_access_survives_in_src():
    """#154 Phase 2 acceptance: power metadata is read through typed fields.

    Matches `.metadata["<census key>"]` / `.metadata.get("<census key>")` in
    src/ outside the model module. Flat-view dicts obtained from
    ``to_metadata_dict()`` are fine — they are local variables, not
    ``.metadata`` attribute chains.
    """
    keys = "|".join(sorted(re.escape(k) for k in _census()["keys"]))
    pattern = re.compile(rf"\.metadata(?:\.get\(|\[)['\"](?:{keys})['\"]")
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path == SRC / "power" / "metadata.py":
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "String-keyed power-metadata access in src/ — use PowerMetadata's "
        "typed fields (or to_metadata_dict() at the report boundary):\n" + "\n".join(offenders)
    )


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
