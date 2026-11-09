"""Setup nesting is bounded before copies, compiler validation and hashing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from helia_profiler.calibration import Setup, calibrate, read_bundle
from helia_profiler.cli.app import app
from helia_profiler.errors import CalibrationError
from tests.calibration_bundles import (
    _republish,
    bundle,
    flat,
    schema_checked_writes,  # noqa: F401
)

pytestmark = pytest.mark.usefixtures("schema_checked_writes")


def nested_json(levels: int, kind: str) -> str:
    text = "1"
    for level in range(levels):
        if kind == "list" or (kind == "mixed" and level % 2):
            text = "[" + text + "]"
        else:
            text = '{"next":' + text + "}"
    return text


def deep_bundle(
    tmp_path: Path, depth: int, kind: str, section: str = "config", key: str = "probe"
) -> Path:
    pytest.importorskip("helia_aot")
    path = bundle(tmp_path, cycles={0: 1.0, 1: 1.0, 2: 1.0})
    metadata = path / "run_metadata.json"
    data = json.loads(metadata.read_text())
    data[section][key] = "DEPTH_MARKER"
    text = json.dumps(data).replace('"DEPTH_MARKER"', nested_json(depth - 2, kind))
    json.loads(text)
    metadata.write_text(text)
    _republish(path)
    return path


@pytest.mark.parametrize("kind", ["object", "list", "mixed"])
def test_setup_at_the_depth_budget_is_preserved(tmp_path: Path, kind: str) -> None:
    path = deep_bundle(tmp_path / "accepted", 64, kind)
    before = (path / "run_metadata.json").read_bytes()
    setup = read_bundle(path).setup
    assert setup.values is not None
    assert setup.values["config"]["probe"] == json.loads(nested_json(62, kind))
    output = tmp_path / "out.json"
    assert calibrate([path], output=output).setup == setup
    assert (path / "run_metadata.json").read_bytes() == before


@pytest.mark.parametrize("section", ["config", "engine", "toolchain"])
@pytest.mark.parametrize("depth,kind", [(65, "object"), (65, "list"), (600, "mixed")])
def test_parseable_deep_setup_is_refused_before_recursive_processing(
    tmp_path: Path, section: str, depth: int, kind: str
) -> None:
    path = deep_bundle(tmp_path / "deep", depth, kind, section)
    before = (path / "run_metadata.json").read_bytes()
    output = tmp_path / "out.json"
    message = rf"Bundle .*deep: section '{section}' nests deeper than 64 container levels"
    with pytest.raises(CalibrationError, match=message) as caught:
        calibrate([path], output=output)
    assert caught.value.hint and "setup" in caught.value.hint
    assert not output.exists()
    assert (path / "run_metadata.json").read_bytes() == before


@pytest.mark.parametrize("merge", [False, True])
def test_refusing_setup_preserves_existing_output(tmp_path: Path, merge: bool) -> None:
    valid = deep_bundle(tmp_path / "valid", 16, "mixed")
    output = tmp_path / "out.json"
    calibrate([valid], output=output)
    before = output.read_bytes()
    deep = deep_bundle(tmp_path / "deep", 600, "object")
    with pytest.raises(CalibrationError, match="section 'config' nests deeper than 64"):
        calibrate([deep], output=output, merge=merge)
    assert output.read_bytes() == before


def test_cli_reports_setup_depth_without_a_traceback(tmp_path: Path) -> None:
    deep = deep_bundle(tmp_path / "deep", 600, "object")
    output = tmp_path / "out.json"
    result = CliRunner().invoke(app, ["calibrate", str(deep), "-o", str(output)])
    assert result.exit_code == 1
    assert "section 'config' nests deeper than 64 container levels" in flat(result.output)
    assert "Traceback" not in result.output and "RecursionError" not in result.output
    assert not output.exists()


@pytest.mark.parametrize("kind", ["object", "list", "mixed"])
@pytest.mark.parametrize("pairing", [False, True])
def test_direct_setup_api_checks_both_bases(kind: str, pairing: bool) -> None:
    legal = {"config": json.loads(nested_json(63, kind))}
    assert Setup.of(legal, pairing=legal).digest == Setup.of(legal).digest
    deep = {"config": json.loads(nested_json(64, kind))}
    with pytest.raises(CalibrationError, match="section 'config' nests deeper than 64"):
        Setup.of(legal if pairing else deep, pairing=deep if pairing else None)


def test_in_memory_setup_cycle_hits_the_same_budget() -> None:
    basis: dict[str, Any] = {}
    basis["config"] = basis
    with pytest.raises(CalibrationError, match="section 'config' nests deeper than 64"):
        Setup.of(basis)


def test_raw_setup_budget_also_covers_later_excluded_sections(tmp_path: Path) -> None:
    path = deep_bundle(tmp_path / "deep", 600, "object", key="power")
    with pytest.raises(CalibrationError, match="section 'config' nests deeper than 64"):
        read_bundle(path)


def test_supported_aot_rules_deeper_than_conditions_are_accepted(tmp_path: Path) -> None:
    from helia_profiler.engines.helia_aot.compile import nesting_depth

    pytest.importorskip("helia_aot")
    from helia_aot.cli.defines import ConvertArgs

    args: dict[str, Any] = {
        "memory": {
            "constraints": [{"name": "dtcm", "max_size": 65536}],
            "tensors": [{"type": "scratch", "attributes": {"memory": "dtcm"}}],
        },
        "operators": [{"type": "conv_2d", "attributes": {"memory": "sram"}}],
        "transforms": [{"name": "fuse_activation", "options": {"eps": 1e-6}}],
    }
    ConvertArgs(**args)
    path = bundle(tmp_path / "valid", cycles={0: 1.0, 1: 1.0, 2: 1.0})
    metadata = path / "run_metadata.json"
    data = json.loads(metadata.read_text())
    data["config"]["engine"]["config"]["aot_args"] = args
    metadata.write_text(json.dumps(data))
    _republish(path)
    setup = read_bundle(path).setup
    assert nesting_depth(setup.values) > 8
    assert setup.pairing_digest is not None
    assert len(calibrate([path], output=tmp_path / "out.json").samples) == 3
