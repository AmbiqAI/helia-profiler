from __future__ import annotations

import json
import re
from importlib.resources import files
from pathlib import Path

from helia_profiler.report.contracts import (
    PROFILE_RESULTS_SCHEMA,
    PROFILE_RESULTS_SCHEMA_VERSION,
    RUN_METADATA_SCHEMA,
    RUN_METADATA_SCHEMA_VERSION,
    RUN_SUMMARY_SCHEMA,
    RUN_SUMMARY_SCHEMA_VERSION,
)

DOCS_PATH = (
    Path(__file__).resolve().parents[1]
    / "astro-site"
    / "src"
    / "content"
    / "docs"
    / "guide"
    / "output.mdx"
)

#: Every schema the guide's artifact table advertises, with its live version.
ADVERTISED = (
    (RUN_SUMMARY_SCHEMA, RUN_SUMMARY_SCHEMA_VERSION),
    (RUN_METADATA_SCHEMA, RUN_METADATA_SCHEMA_VERSION),
    (PROFILE_RESULTS_SCHEMA, PROFILE_RESULTS_SCHEMA_VERSION),
)


def test_the_artifact_table_advertises_the_live_schema_versions():
    text = DOCS_PATH.read_text(encoding="utf-8")
    for schema, version in ADVERTISED:
        found = re.findall(rf"`{re.escape(schema)}` v(\d+)", text)
        assert found, f"{DOCS_PATH.name} never advertises {schema}"
        assert found == [str(version)] * len(found), (
            f"{DOCS_PATH.name} advertises {schema} v{found} but the contract is v{version}"
        )


def test_the_worked_summary_example_preserves_the_captured_bundle():
    text = DOCS_PATH.read_text(encoding="utf-8")
    example = re.search(r"```json\n(.*?)\n```", text, flags=re.DOTALL)
    assert example is not None
    excerpt = json.loads(example.group(1))
    bundle = (
        DOCS_PATH.parents[5]
        / "examples/results/hardware-validation-2026-09-16"
        / "apollo510_evb-kws-rt-ns-arm-none-eabi-gcc-rtt-auto/summary.json"
    )
    captured = json.loads(bundle.read_text(encoding="utf-8"))
    assert excerpt == {key: captured[key] for key in excerpt}
    assert f"New runs use schema version {RUN_SUMMARY_SCHEMA_VERSION}" in text


def test_packaged_summary_schema_matches_the_emitted_version():
    schema = json.loads(
        files("helia_profiler")
        .joinpath("data/run_summary.schema.v1.json")
        .read_text(encoding="utf-8")
    )
    assert schema["properties"]["schema"] == {"const": RUN_SUMMARY_SCHEMA}
    assert schema["properties"]["schema_version"] == {"const": RUN_SUMMARY_SCHEMA_VERSION}
