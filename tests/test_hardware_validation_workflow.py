"""Tests for the hardware-validation workflow contract."""

from __future__ import annotations

import json
from pathlib import Path
import re


def test_ns_cmsis_nn_default_matches_qualified_baseline() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    baseline = json.loads(
        (
            repo_root
            / "src"
            / "helia_profiler"
            / "data"
            / "compatibility-baseline-v1.json"
        ).read_text()
    )
    workflow = (repo_root / ".github" / "workflows" / "hardware-validation.yml").read_text()
    match = re.search(
        r"^\s*HPX_QUALIFIED_NS_CMSIS_NN_REF:\s*([0-9a-f]{40})\s*$",
        workflow,
        re.MULTILINE,
    )

    assert match is not None
    assert match.group(1) == baseline["projects"]["ns-cmsis-nn"]["ref"]
    assert 'requested_ref="${HPX_QUALIFIED_NS_CMSIS_NN_REF}"' in workflow
    assert 'requested_kind="qualified_baseline"' in workflow
