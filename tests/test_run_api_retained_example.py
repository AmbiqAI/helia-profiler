"""The retained run API example stays loadable, and its portable view current, as the format changes."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

import helia_profiler as hpx
from helia_profiler.api import load_result, observations

_ROOT = Path(__file__).resolve().parents[1] / "examples" / "results" / "run-api-synthetic"
_CONTRACT = Path(__file__).parent / "fixtures" / "result_contract_v1"


def test_the_retained_result_loads_with_its_build():
    compiled = hpx.load_build(_ROOT / "build.json", roots={"run": _ROOT})
    record = hpx.load_run(_ROOT / "result.json", roots={"run": _ROOT})
    result = record.result

    assert compiled.lifecycle is hpx.Lifecycle.CARRY
    assert result.compiled == compiled and result.succeeded
    assert result.verification is not None and result.verification.matched
    assert result.identity.intent_hash == compiled.identity.intent_hash
    assert result.identity.inputs_hash == compiled.identity.inputs_hash
    assert hpx.SemanticHash.parse(compiled.binding).kind == "binding"
    assert hpx.SemanticHash.parse(result.identity.observation_hash or "").kind == "observation"


def test_the_verified_reader_takes_the_retained_result():
    record = load_result(_ROOT / "result.json")

    assert isinstance(record, hpx.RunResultRecord)
    assert record.sha256 == hpx.load_run(_ROOT / "result.json", roots={"run": _ROOT}).sha256


def test_the_portable_export_is_current_and_valid():
    committed = json.loads((_ROOT / "portable.json").read_text(encoding="utf-8"))
    schema = json.loads((_CONTRACT / "result.schema.json").read_bytes())

    assert observations(load_result(_ROOT / "result.json")) == committed
    Draft202012Validator(schema).validate(committed)
