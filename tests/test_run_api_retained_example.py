"""The retained run API example stays loadable as the format changes."""

from __future__ import annotations

import json
from pathlib import Path

import helia_profiler as hpx

_ROOT = Path(__file__).resolve().parents[1] / "examples" / "results" / "run-api-synthetic"


def test_the_retained_build_loads_and_matches_its_result():
    compiled = hpx.load_build(_ROOT / "build.json", roots={"run": _ROOT})
    result = json.loads((_ROOT / "result.json").read_text(encoding="utf-8"))

    assert compiled.lifecycle is hpx.Lifecycle.CARRY
    assert result["succeeded"] and result["verification"]["matched"]
    assert result["identity"]["intent_hash"] == compiled.identity.intent_hash
    assert result["identity"]["model_sha256"] == compiled.plan.model.sha256
    assert hpx.SemanticHash.parse(compiled.binding).kind == "binding"
    assert hpx.SemanticHash.parse(result["identity"]["observation_hash"]).kind == "observation"
