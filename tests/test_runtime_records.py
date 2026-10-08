"""Runtime records and the runtime × target qualification lookup."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from helia_profiler.cli.app import app
from helia_profiler.engines import EngineType
from helia_profiler.errors import ConfigError
from helia_profiler.platform import build_platform_registry, get_soc_for_board
from helia_profiler.runtime_records import (
    RuntimeQualification,
    load_runtime_records,
    qualification,
    runtime,
    runtimes,
)

_COMMIT = "a" * 40


def _record(**changes: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema": "helia-profiler/runtime@1",
        "name": "helia-rt",
        "version": "1.0.0",
        "default": True,
        "source": {"repo": "AmbiqAI/helia-rt", "commit": _COMMIT},
        "precisions": {"a8w8": "supported", "fp16": {"unsupported": "no FP16 kernels"}},
        "qualified": [
            {
                "board": "apollo510_evb",
                "clock": "lp",
                "precisions": ["a8w8"],
                "basis": "test",
                "trace": "test",
            }
        ],
    }
    record.update(changes)
    return record


def _write(root: Path, *records: dict[str, Any]) -> Path:
    for record in records:
        path = root / record["name"] / f"{record['version']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding="utf-8")
    return root


def test_shipped_records_name_engines_targets_and_one_default_each() -> None:
    records = runtimes()
    boards = build_platform_registry().boards
    assert {record.name for record in records} == {engine.value for engine in EngineType}
    for record in records:
        assert runtime(record.name) is not None
        for target in record.qualified:
            clocks = get_soc_for_board(target.board).cpu_clock.speed_names
            assert target.board in boards and target.clock in clocks


def test_shipped_records_keep_the_fixture_capability_table() -> None:
    from helia_profiler._fixture_build import FIXTURE_CAPABILITIES

    table = {
        engine.value: {str(dtype): status.value for dtype, status in statuses.items()}
        for engine, statuses in FIXTURE_CAPABILITIES.items()
    }
    assert table == {
        "tflm": {
            "int8": "qualified",
            "int16": "supported",
            "int32": "supported",
            "float16": "unsupported",
            "float32": "supported",
        },
        "helia-aot": {
            "int8": "qualified",
            "int16": "supported",
            "int32": "supported",
            "float16": "supported",
            "float32": "supported",
        },
        "helia-rt": {
            "int8": "supported",
            "int16": "supported",
            "int32": "supported",
            "float16": "supported",
            "float32": "supported",
        },
    }


@pytest.mark.parametrize(
    ("name", "version", "precision", "board", "state", "reason"),
    [
        ("helia-aot", "0.23.0", "a8w8", "apollo510_evb", "qualified", None),
        ("tflm", None, "a8w8", "apollo510_evb", "qualified", None),
        ("helia-aot", None, "a8w8", "apollo510_evb", "supported", "0.25.0 is not qualified"),
        ("helia-aot", "0.23.0", "a16w8", "apollo510_evb", "supported", "not qualified for a16w8"),
        ("helia-aot", "0.23.0", "a8w8", "apollo4p_evb", "supported", "on apollo4p_evb at lp"),
        ("tflm", None, "fp16", "apollo510_evb", "unsupported", "refuses any model with a FLOAT16"),
        ("helia-rt", None, "a8w4", "apollo510_evb", "unsupported", "does not declare a8w4"),
        ("helia-aot", "0.25.1", "a8w8", "apollo510_evb", "unsupported", "No runtime record"),
        ("vela", None, "a8w8", "apollo510_evb", "unsupported", "No runtime record for vela"),
    ],
)
def test_qualification_answers_from_the_records(
    name: str, version: str | None, precision: str, board: str, state: str, reason: str | None
) -> None:
    answer = qualification(name, version, board=board, clock="lp", precision=precision)
    assert answer.state is RuntimeQualification(state)
    if reason is None:
        assert answer.reason is None
    else:
        assert answer.reason is not None and reason in answer.reason


def test_qualification_is_per_clock() -> None:
    answer = qualification("tflm", board="apollo510_evb", clock="hp", precision="a8w8")
    assert answer.state is RuntimeQualification.SUPPORTED
    assert answer.reason is not None and "at hp" in answer.reason


def test_an_omitted_version_means_the_default_record() -> None:
    default = runtime("helia-aot")
    assert default is not None and (default.version, default.default) == ("0.25.0", True)
    assert runtime("helia-aot", "0.23.0") is not None
    assert runtime("helia-aot", "0.23.0") is not default


def test_qualification_refuses_a_precision_outside_the_vocabulary() -> None:
    with pytest.raises(ValueError, match="Unknown precision 'int8'"):
        qualification("tflm", board="apollo510_evb", clock="lp", precision="int8")
    with pytest.raises(ValueError, match="Unknown precision 'int8'"):
        qualification("tflm", board="nonexistent", clock="xp", precision="int8")


@pytest.mark.parametrize(
    ("board", "clock", "message"),
    [
        ("nonexistent", "lp", "Unknown board 'nonexistent'"),
        ("apollo510_evb", "xp", "apollo510_evb has no 'xp' clock"),
        ("apollo4p_evb", "ulp", "apollo4p_evb has no 'ulp' clock"),
    ],
)
def test_qualification_refuses_an_unregistered_target(board: str, clock: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        qualification("tflm", board=board, clock=clock, precision="a8w8")


def test_a_prepared_runtime_record_carries_kernels_and_archive(tmp_path: Path) -> None:
    kernels = {"repo": "AmbiqAI/ns-cmsis-nn", "commit": "b" * 40}
    root = _write(tmp_path, _record(kernels=kernels, archive={"sha256": "c" * 64}))
    (record,) = load_runtime_records(root)
    assert record.kernels is not None
    assert (record.kernels.repo, record.kernels.commit) == ("AmbiqAI/ns-cmsis-nn", "b" * 40)
    assert record.archive_sha256 == "c" * 64


def test_records_load_from_a_directory(tmp_path: Path) -> None:
    root = _write(tmp_path, _record(), _record(version="1.1.0", default=False))
    records = load_runtime_records(root)
    assert [record.version for record in records] == ["1.0.0", "1.1.0"]
    assert records[0].precisions == {"a8w8": None, "fp16": "no FP16 kernels"}


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"extra": 1}, "must be an object with exactly"),
        ({"schema": "helia-profiler/runtime@2"}, "has schema"),
        ({"default": "yes"}, "default must be true or false"),
        ({"source": {"repo": "AmbiqAI/helia-rt", "commit": "v1.0.0"}}, "40-hex commit"),
        ({"precisions": {"int8": "supported"}}, "unknown precision 'int8'"),
        ({"precisions": {"a8w8": "partial"}}, "a8w8 must be an object"),
        ({"precisions": {"a8w8": {"unsupported": ""}}}, "reason must be a non-empty string"),
        ({"qualified": {}}, "qualified must be a list"),
        ({"kernels": {"repo": "AmbiqAI/ns-cmsis-nn", "commit": _COMMIT}}, "exactly"),
        (
            {
                "kernels": {"repo": "AmbiqAI/ns-cmsis-nn", "commit": _COMMIT},
                "archive": {"sha256": "x"},
            },
            "archive sha256 must be 64 hex",
        ),
        (
            {
                "kernels": {"repo": "AmbiqAI/ns-cmsis-nn", "commit": "v7"},
                "archive": {"sha256": "a" * 64},
            },
            "kernels commit must be a 40-hex commit",
        ),
        (
            {
                "qualified": [
                    {
                        "board": "apollo510_evb",
                        "clock": "lp",
                        "precisions": ["fp16"],
                        "basis": "test",
                        "trace": "test",
                    }
                ]
            },
            "qualifies fp16, which is not supported",
        ),
        (
            {
                "qualified": [
                    {
                        "board": "apollo510_evb",
                        "clock": "lp",
                        "precisions": [],
                        "basis": "test",
                        "trace": "test",
                    }
                ]
            },
            "precisions must be a non-empty list",
        ),
    ],
)
def test_malformed_records_are_refused(
    tmp_path: Path, changes: dict[str, Any], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        load_runtime_records(_write(tmp_path, _record(**changes)))


def test_stray_files_are_ignored_and_duplicate_keys_refused(tmp_path: Path) -> None:
    root = _write(tmp_path, _record())
    (root / ".DS_Store").write_bytes(b"\0")
    (root / "helia-rt" / "notes.txt").write_text("not a record", encoding="utf-8")
    assert [record.version for record in load_runtime_records(root)] == ["1.0.0"]

    path = root / "helia-rt" / "1.0.0.json"
    path.write_text(path.read_text(encoding="utf-8")[:-1] + ', "default": false}', encoding="utf-8")
    with pytest.raises(ConfigError, match="duplicate keys \\['default'\\]"):
        load_runtime_records(root)


def test_the_helia_aot_range_spans_the_records() -> None:
    from helia_profiler.engines.helia_aot.compile import _recorded_range

    records = load_runtime_records(Path(__file__).parents[1] / "src/helia_profiler/data/runtimes")
    assert _recorded_range(records) == ("0.23.0", "0.26.0")
    newest = [r for r in records if (r.name, r.version) == ("helia-aot", "0.25.0")]
    assert _recorded_range([replace(newest[0], version="1.2.3")]) == ("1.2.3", "1.3.0")
    with pytest.raises(ConfigError, match="major.minor.patch"):
        _recorded_range([replace(newest[0], version="next")])
    with pytest.raises(ConfigError, match="no heliaAOT runtime record"):
        _recorded_range([])


def test_a_record_must_live_at_its_name_and_version(tmp_path: Path) -> None:
    path = tmp_path / "helia-rt" / "1.0.1.json"
    path.parent.mkdir()
    path.write_text(json.dumps(_record()), encoding="utf-8")
    with pytest.raises(ConfigError, match="names helia-rt 1.0.0"):
        load_runtime_records(tmp_path)


@pytest.mark.parametrize("defaults", [(False, False), (True, True)])
def test_each_runtime_has_exactly_one_default(tmp_path: Path, defaults: tuple[bool, bool]) -> None:
    root = _write(
        tmp_path,
        _record(default=defaults[0]),
        _record(version="1.1.0", default=defaults[1]),
    )
    with pytest.raises(ConfigError, match="exactly one default record"):
        load_runtime_records(root)


def test_runtimes_cli_lists_and_shows_records() -> None:
    runner = CliRunner()
    listed = runner.invoke(app, ["runtimes", "list"])
    assert listed.exit_code == 0, listed.output
    assert "helia-aot   0.23.0           AmbiqAI/helia-aot@d75c96eb  apollo510_evb/lp: a8w8" in (
        listed.output
    )
    assert "helia-aot   0.25.0   default AmbiqAI/helia-aot@3e45ef8f  -" in listed.output

    shown = runner.invoke(app, ["runtimes", "show", "helia-rt"])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["version"] == "1.21.3"

    missing = runner.invoke(app, ["runtimes", "show", "helia-rt", "9.9.9"])
    assert missing.exit_code == 1
    assert "No runtime record for helia-rt 9.9.9." in missing.output
