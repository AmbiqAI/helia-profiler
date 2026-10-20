"""Semantic hashes keep their exact encoding: one golden basis per kind, and the parser's edges.

The same examples are on the Output files page; a change here is a change
for every reader that stores or compares these strings.
"""

from __future__ import annotations

import json
import re
from importlib.resources import files
from pathlib import Path

import pytest

import helia_profiler
from helia_profiler import SemanticHash
from helia_profiler._semantic_hash import KIND_VERSIONS, canonical_json, require, semantic_hash

HEX = "a74a43acef82134a3774a67959b8da1232a38fb88d7ea6f93e55e3e7662786c8"

#: kind -> (basis value, canonical bytes, namespaced string).
GOLDEN = {
    "intent": (
        {"settings": {"engine": {"type": "helia-aot"}}, "toolchain": ["atfe", "21.1.1"]},
        b'{"settings":{"engine":{"type":"helia-aot"}},"toolchain":["atfe","21.1.1"]}',
        f"hpx.intent/1:sha256:{HEX}",
    ),
    "build": (
        {"image": "ab12", "lock": {}, "runtime_archive": None, "model": "cd34"},
        b'{"image":"ab12","lock":{},"model":"cd34","runtime_archive":null}',
        "hpx.build/1:sha256:6c416b863ba7e576075dca7ff677be4449aa90e879738cb4591dcb323d9583f5",
    ),
    "observation": (
        {"cycles": [120, 80], "mean_us": float("nan"), "error": False},
        b'{"cycles":[120,80],"error":false,"mean_us":"nan"}',
        "hpx.observation/1:sha256:8f2796b5d85db5099f58b011ce8cb5c0b7c38f22d36d462cf80e6e9b77eba605",
    ),
    "binding": (
        {"lifecycle": "carry", "power_sync": None},
        b'{"lifecycle":"carry","power_sync":null}',
        "hpx.binding/1:sha256:7b9198891413a5779f01bb8aed3d0a68125d87cf983fddec759efbfcb58201ed",
    ),
}

DOCS = Path(__file__).resolve().parents[2] / "astro-site/src/content/docs/guide/output.mdx"


def test_every_kind_has_a_golden_at_its_current_version():
    assert set(GOLDEN) == set(KIND_VERSIONS)
    assert all(version == 1 for version in KIND_VERSIONS.values())


@pytest.mark.parametrize("kind", sorted(GOLDEN))
def test_each_kind_encodes_its_golden_basis_exactly(kind):
    value, basis, expected = GOLDEN[kind]
    assert canonical_json(value) == basis
    assert semantic_hash(kind, value) == expected
    assert str(SemanticHash.parse(expected)) == expected


@pytest.mark.parametrize("kind", sorted(GOLDEN))
def test_the_output_guide_states_every_golden(kind):
    _, basis, expected = GOLDEN[kind]
    text = DOCS.read_text(encoding="utf-8")
    assert expected in text
    assert basis.decode() in text


def test_parse_reads_the_parts():
    parsed = SemanticHash.parse(f"hpx.intent/1:sha256:{HEX}")
    assert (parsed.producer, parsed.kind, parsed.version, parsed.hex) == ("hpx", "intent", 1, HEX)
    other = SemanticHash.parse(f"helx.cost-model/12:sha256:{HEX}")
    assert (other.producer, other.kind, other.version) == ("helx", "cost-model", 12)


@pytest.mark.parametrize(
    "text",
    [
        HEX,
        f"sha256:{HEX}",
        f"HPX.intent/1:sha256:{HEX}",
        f"hpx_intent/1:sha256:{HEX}",
        f".intent/1:sha256:{HEX}",
        f"hpx-.intent/1:sha256:{HEX}",
        f"hpx.Intent/1:sha256:{HEX}",
        f"hpx.intent/1:sha256:{HEX.upper()}",
        f"hpx.intent:sha256:{HEX}",
        f"hpx.intent/:sha256:{HEX}",
        f"hpx.intent/0:sha256:{HEX}",
        f"hpx.intent/01:sha256:{HEX}",
        f"hpx.intent/v1:sha256:{HEX}",
        f"hpx.intent/1:sha512:{HEX}",
        f"hpx.intent/1:md5:{HEX}",
        f"hpx.intent/1:{HEX}",
        f"hpx.intent/1:sha256:{HEX[:-1]}",
        f"hpx.intent/1:sha256:{HEX}0",
        f" hpx.intent/1:sha256:{HEX}",
        f"hpx.intent/1:sha256:{HEX}\n",
        "",
    ],
)
def test_parse_refuses_anything_else(text):
    with pytest.raises(ValueError, match="Not a semantic hash"):
        SemanticHash.parse(text)


@pytest.mark.parametrize("value", [None, 7, b"hpx.intent/1:sha256:" + HEX.encode()])
def test_parse_refuses_a_non_string(value):
    with pytest.raises(ValueError, match="Not a semantic hash"):
        SemanticHash.parse(value)


@pytest.mark.parametrize(
    "parts",
    [
        ("hpx", "intent", 0, HEX),
        ("hpx", "intent", True, HEX),
        ("hpx", "intent", "1", HEX),
        ("hpx", "intent", 1, HEX.upper()),
        ("hpx", "intent", 1, int("1" * 64)),
    ],
)
def test_construction_refuses_a_malformed_part(parts):
    with pytest.raises(ValueError):
        SemanticHash(*parts)


@pytest.mark.parametrize(
    "text",
    [
        HEX,
        f"hpx.build/1:sha256:{HEX}",
        f"hpx.intent/2:sha256:{HEX}",
        f"helx.intent/1:sha256:{HEX}",
        None,
    ],
)
def test_require_refuses_another_kind_version_or_producer(text):
    with pytest.raises(ValueError, match=re.escape("not an hpx.intent/1:sha256:<hex> hash")):
        require(text, "intent")


def test_require_returns_a_matching_hash():
    assert require(f"hpx.intent/1:sha256:{HEX}", "intent") == f"hpx.intent/1:sha256:{HEX}"


def test_the_summary_schema_pins_the_identity_kinds_at_their_versions():
    schema = json.loads(
        files("helia_profiler").joinpath("data/run_summary.schema.v1.json").read_text("utf-8")
    )
    hashes = schema["properties"]["identity"]["properties"]
    for name, kind in (
        ("build_hash", "build"),
        ("observation_hash", "observation"),
        ("intent_hash", "intent"),
    ):
        pattern = hashes[name]["oneOf"][0]["pattern"]
        good = f"hpx.{kind}/{KIND_VERSIONS[kind]}:sha256:{HEX}"
        # JSON Schema patterns are unanchored searches.
        assert re.search(pattern, good)
        for bad in (HEX, f"{good}0", f"x{good}"):
            assert not re.search(pattern, bad)


def test_semantic_hash_is_experimental_public_api():
    assert helia_profiler.__api_stability__["SemanticHash"] == "experimental"
