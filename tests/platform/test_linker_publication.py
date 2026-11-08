"""Public retained records reject values their strict readers cannot reload."""

from dataclasses import replace
from pathlib import Path

import pytest

import helia_profiler as hpx
from helia_profiler.platform.linker.model import LinkerVerification
from helia_profiler.platform.provenance import platform_snapshot
from tests.runs.helpers import compiled, reseal


def test_platform_serialization_rejects_an_invalid_nested_literal():
    platform = platform_snapshot("apollo510_evb")
    layout = platform.expected_linker
    assert layout is not None
    malformed = replace(platform, expected_linker=replace(layout, admission_source="typo"))
    with pytest.raises(ValueError, match="admission_source"):
        malformed.to_dict()


def test_actual_verification_serialization_rejects_an_integer_boolean():
    platform = platform_snapshot("apollo510_evb")
    layout = platform.expected_linker
    assert layout is not None
    malformed = replace(
        layout,
        regions=(replace(layout.regions[0], section_attributable=1), *layout.regions[1:]),
    )
    assert malformed == layout
    verification = LinkerVerification("profile", "hpx_profiler", malformed)
    with pytest.raises(ValueError, match="section_attributable"):
        verification.to_dict()


def test_public_compiled_save_preserves_destination_for_malformed_actual_record(tmp_path):
    example = Path(__file__).resolve().parents[2] / "examples/results/run-api-synthetic"
    original = hpx.load_build(example / "build.json", roots={"run": example})
    verification = original.build.linker_verification
    assert verification is not None and verification.layout is not None
    layout = verification.layout
    malformed = replace(
        layout,
        regions=(replace(layout.regions[0], section_attributable=1), *layout.regions[1:]),
    )
    assert original.plan.platform is not None
    assert malformed == original.plan.platform.expected_linker
    destination = tmp_path / "receipt.json"
    destination.write_bytes(b"retained previous receipt")
    with pytest.raises((ValueError, hpx.ConfigError), match="section_attributable"):
        build = reseal(original.build, linker_verification=replace(verification, layout=malformed))
        bad = compiled(original.plan, build, original.lifecycle)
        bad.save(destination, roots={"run": example})
    assert destination.read_bytes() == b"retained previous receipt"
