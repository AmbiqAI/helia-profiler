"""Guards docs/guide/output.md against advertising a stale schema version.

The guide tells a consumer which version to expect, in two places: the artifact
table and the worked ``summary.json`` example. Both are hand-written, so a
version bump leaves them behind silently -- #249 bumped the run summary to 5 and
the guide still promised 4 until a reviewer read both files side by side.

Only the current claims are pinned. The "what changed in v2" style history later
in the guide names old versions on purpose.
"""

from __future__ import annotations

import re
from pathlib import Path

from helia_profiler.report.contracts import (
    PROFILE_RESULTS_SCHEMA,
    PROFILE_RESULTS_SCHEMA_VERSION,
    RUN_METADATA_SCHEMA,
    RUN_METADATA_SCHEMA_VERSION,
    RUN_SUMMARY_SCHEMA,
    RUN_SUMMARY_SCHEMA_VERSION,
)

DOCS_PATH = Path(__file__).resolve().parents[1] / "docs" / "guide" / "output.md"

#: Every schema the guide's artifact table advertises, with its live version.
ADVERTISED = (
    (RUN_SUMMARY_SCHEMA, RUN_SUMMARY_SCHEMA_VERSION),
    (RUN_METADATA_SCHEMA, RUN_METADATA_SCHEMA_VERSION),
    (PROFILE_RESULTS_SCHEMA, PROFILE_RESULTS_SCHEMA_VERSION),
)


def test_the_artifact_table_advertises_the_live_schema_versions():
    text = DOCS_PATH.read_text(encoding="utf-8")
    for schema, version in ADVERTISED:
        # The table cell reads: `hpx.run-summary` v5
        found = re.findall(rf"`{re.escape(schema)}` v(\d+)", text)
        assert found, f"{DOCS_PATH.name} never advertises {schema}"
        assert found == [str(version)] * len(found), (
            f"{DOCS_PATH.name} advertises {schema} v{found} but the contract is v{version}"
        )


def test_the_worked_summary_example_carries_the_live_version():
    text = DOCS_PATH.read_text(encoding="utf-8")
    # The example pairs the schema name with its version two lines apart; pin
    # the pair rather than a bare `"schema_version": N`, which also appears in
    # the run-metadata and profile-results examples.
    block = re.search(
        rf'"schema": "{re.escape(RUN_SUMMARY_SCHEMA)}",\s*\n\s*"schema_version": (\d+),',
        text,
    )
    assert block is not None, f"{DOCS_PATH.name} has no worked {RUN_SUMMARY_SCHEMA} example"
    assert int(block.group(1)) == RUN_SUMMARY_SCHEMA_VERSION
