from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from helia_profiler.results import load_result_manifest

BUNDLES = sorted(
    (Path(__file__).resolve().parents[1] / "examples/results").rglob("result_manifest.json")
)


@pytest.mark.parametrize("manifest", BUNDLES, ids=lambda path: path.parent.name)
def test_published_bundle_integrity_and_probe_redaction(manifest: Path):
    load_result_manifest(manifest, verify=True)
    metadata = json.loads(manifest.with_name("run_metadata.json").read_text())

    def check(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"jlink_serial", "serial", "serial_number"} and item is not None:
                    assert re.fullmatch(r"<redacted-serial:[0-9a-f]{8}>", str(item))
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)

    check(metadata)
