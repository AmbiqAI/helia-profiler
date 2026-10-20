"""``hpx freeze``, ``hpx build``, ``hpx validate build`` and ``hpx run`` over a mocked build and board."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click import unstyle
from typer.testing import CliRunner

import helia_profiler as hpx
from helia_profiler.cli.app import app
from helia_profiler.compiled_build import RunResult
from helia_profiler.expect import (
    Expect,
    ReferenceOrigin,
    ReferenceOutputs,
    TensorComparison,
    Tolerance,
    Verification,
)
from helia_profiler.fixture import FixtureBuild, FixtureCaptureResult, FixtureTiming
from helia_profiler.frozen_run import _ENVIRONMENT_VARIABLES, FrozenRun
from helia_profiler.lifecycle import Lifecycle
from helia_profiler.run_directory import run_roots
from helia_profiler.run_guard import OperatorGuard
from tests.run_api_helpers import ATFE_OUTPUT, atfe_build, compiled, plan_for, reseal
from tests.unit.test_fixture_record import _build

runner = CliRunner()
SERIAL = "1160002255"
BOARD = "apollo510_evb"
_FREEZE = (
    "--engine",
    "helia-aot",
    "--toolchain",
    "atfe",
    "--cpu-clock",
    "lp",
    "--arena-size",
    "32768",
    "--arena-location",
    "sram",
    "--weights-location",
    "mram",
)


@pytest.fixture(autouse=True)
def host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "helia_profiler.hostenv._proc.tool_output", lambda command, *, timeout_s: ATFE_OUTPUT
    )
    monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path / "cache"))
    for variable in (
        "SEGGER_RTT_PATH",
        "CMSIS_NN_PATH",
        "HELIART_DIST_PATH",
        "HELIART_SOURCE_PATH",
        *_ENVIRONMENT_VARIABLES,
    ):
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture
def out(tmp_path: Path) -> Path:
    return tmp_path / "run"


@pytest.fixture
def build(out: Path) -> FixtureBuild:
    return atfe_build(_build(out))


@pytest.fixture
def plan(tmp_path: Path, build: FixtureBuild) -> FrozenRun:
    return plan_for(build, tmp_path / "model.tflite")


@pytest.fixture
def plan_file(tmp_path: Path, plan: FrozenRun) -> Path:
    return plan.save(tmp_path / "plan.json")


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    """A run directory ``hpx build`` writes; ``out`` already holds the ``build`` fixture's files."""
    return tmp_path / "dest"


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch, build: FixtureBuild) -> list[dict[str, Any]]:
    """Mock the model analysis and the firmware build; record what the build was asked for.

    The mocked build writes its app and evidence under the work directory, as a real one does.
    """
    analysis = SimpleNamespace(
        inputs=tuple(io.tensor for io in build.inputs), outputs=build.output_tensors
    )
    monkeypatch.setattr("helia_profiler.run_inputs.analyze_run_model", lambda path: analysis)
    seen: list[dict[str, Any]] = []

    def build_fixed_fixture(config, fixture, **options):
        seen.append({"config": config, "fixture": fixture, **options})
        profiling = config.profiling
        return reseal(
            atfe_build(_build(config.work_dir)),
            model=fixture.model,
            inputs=fixture.inputs,
            iterations=profiling.iterations,
            warmups=profiling.warmup,
        )

    monkeypatch.setattr("helia_profiler._fixture_build.build_fixed_fixture", build_fixed_fixture)
    return seen


def _invoke(*args: str | Path):
    return runner.invoke(app, [str(arg) for arg in args])


def _plain(text: str) -> str:
    """*text* without Rich's colours, box rules or terminal-width wrapping."""
    return " ".join(unstyle(text).replace("│", " ").split())


def _saved(out: Path, plan: FrozenRun, build: FixtureBuild) -> hpx.CompiledBuild:
    record = compiled(plan, build, Lifecycle.CARRY)
    record.save(out / "build.json", roots=run_roots(out))
    return record


# -- hpx freeze ---------------------------------------------------------------


def test_freeze_writes_the_plan_a_session_freezes(tmp_path, build):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"model")

    result = _invoke("freeze", model, *_FREEZE, "-o", tmp_path / "plan.json")

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    plan = FrozenRun.load(report["plan"])
    expected = (
        hpx.Session()
        .with_model(model, arena_size=32768, arena_location="sram", weights_location="mram")
        .with_engine("helia-aot")
        .with_target(toolchain="atfe", clock={"cpu": "lp"})
        .freeze()
    )
    assert plan.intent_hash == expected.intent_hash == report["intent_hash"]
    assert plan.model.path == str(model)


def test_freeze_reads_the_config_and_lets_options_override_it(tmp_path):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"model")
    config = tmp_path / "hpx.yml"
    config.write_text(
        f"model:\n  path: {model}\n  arena_size: 4096\n"
        "engine:\n  type: helia-rt\n"
        "profiling:\n  iterations: 7\n",
        encoding="utf-8",
    )

    result = _invoke(
        "freeze",
        "-c",
        config,
        "--backend",
        "helia",
        "--arena-size",
        "8192",
        "--warmup",
        "2",
        "-o",
        tmp_path / "p.json",
    )

    assert result.exit_code == 0, result.output
    settings = FrozenRun.load(tmp_path / "p.json").settings
    assert settings["engine"]["type"] == "helia-rt" and settings["engine"]["backend"] == "helia"
    assert settings["model"]["arena_size"] == 8192
    assert (settings["profiling"]["iterations"], settings["profiling"]["warmup"]) == (7, 2)


def test_freeze_reports_a_config_error_and_exits_1(tmp_path):
    result = _invoke("freeze", tmp_path / "absent.tflite", "-o", tmp_path / "p.json")
    assert result.exit_code == 1
    assert "Error" in _plain(result.output) and not (tmp_path / "p.json").exists()


def test_freeze_requires_an_output_path(tmp_path):
    assert _invoke("freeze", tmp_path / "model.tflite").exit_code == 2


def test_freeze_refuses_a_directory_as_the_plan(tmp_path):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"model")

    result = _invoke("freeze", model, *_FREEZE, "-o", tmp_path)

    assert result.exit_code == 1 and isinstance(result.exception, SystemExit)
    assert "is a directory" in _plain(result.output)


def test_freeze_refuses_to_write_the_plan_over_the_model(tmp_path, monkeypatch):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"model")
    monkeypatch.chdir(tmp_path)

    result = _invoke("freeze", "model.tflite", *_FREEZE, "-o", "./sub/../model.tflite")

    assert result.exit_code == 1
    assert "is the model the plan is frozen against" in _plain(result.output)
    assert model.read_bytes() == b"model"


def test_freeze_records_the_model_by_its_resolved_path(tmp_path, monkeypatch):
    (tmp_path / "models").mkdir()
    model = tmp_path / "models" / "model.tflite"
    model.write_bytes(b"model")
    monkeypatch.chdir(tmp_path / "models")

    result = _invoke("freeze", "model.tflite", *_FREEZE, "-o", tmp_path / "plan.json")

    assert result.exit_code == 0, result.output
    assert FrozenRun.load(tmp_path / "plan.json").model.path == str(model)


# -- hpx build ----------------------------------------------------------------


def test_build_pins_zero_inputs_and_saves_the_run_directory(dest, plan, plan_file, build, calls):
    result = _invoke("build", plan_file, "--out", dest)

    assert result.exit_code == 0, result.output
    (call,) = calls
    config, fixture = call["config"], call["fixture"]
    assert fixture.model.path == dest / "inputs" / "model.tflite" == config.model.path
    assert [io.data.path.name for io in fixture.inputs] == ["input_0.bin", "input_1.bin"]
    assert [io.data.read() for io in fixture.inputs] == [bytes(4), bytes(4)]
    assert [output.tensor for output in fixture.outputs] == list(build.output_tensors)
    assert config.work_dir == dest / "work"
    assert (config.profiling.iterations, config.profiling.warmup) == (100, 5)
    assert call["method"] == hpx.FixtureMethod(hpx.FixtureTimingScope.INVOKE_ONLY)
    report = json.loads(result.stdout)
    assert report["build"] == str(dest / "build.json")
    assert report["inputs"] == [
        {
            "name": name,
            "bytes": 4,
            "path": str(dest / "inputs" / f"input_{index}.bin"),
            "source": "zero-filled",
        }
        for index, name in enumerate(["signal", "cache"])
    ]
    assert report["identity"]["intent_hash"] == plan.intent_hash
    assert report["identity"]["run"]["lifecycle"] == "carry"
    loaded = hpx.load_build(dest / "build.json", roots=run_roots(dest))
    assert loaded.identity.to_dict() == report["identity"]


def test_build_takes_inputs_lifecycle_scope_and_work_dir(tmp_path, dest, plan_file, calls):
    first, second = tmp_path / "a.bin", tmp_path / "b.bin"
    first.write_bytes(b"\x01\x02\x03\x04")
    second.write_bytes(b"\x80" * 4)

    result = _invoke(
        "build",
        plan_file,
        "--out",
        dest,
        "--input",
        first,
        "--input",
        second,
        "--lifecycle",
        "single",
        "--timing-scope",
        "restore_and_invoke",
        "--work-dir",
        dest / "firmware",
    )

    assert result.exit_code == 0, result.output
    (call,) = calls
    config, fixture = call["config"], call["fixture"]
    assert [io.data.read() for io in fixture.inputs] == [b"\x01\x02\x03\x04", b"\x80" * 4]
    assert fixture.inputs[0].data.path == dest / "inputs" / "input_0.bin"
    assert (config.profiling.iterations, config.profiling.warmup) == (1, 0)
    assert call["method"].timing_scope is hpx.FixtureTimingScope.RESTORE_AND_INVOKE
    assert config.work_dir == dest / "firmware"
    report = json.loads(result.stdout)
    assert report["identity"]["run"]["lifecycle"] == "single"
    assert [entry["source"] for entry in report["inputs"]] == [str(first), str(second)]
    assert _invoke("validate", "build", dest).exit_code == 0


@pytest.mark.parametrize("inside", ["", "inputs", "inputs/firmware"])
def test_build_refuses_a_work_dir_mixed_with_the_run_files(dest, plan_file, calls, inside):
    result = _invoke("build", plan_file, "--out", dest, "--work-dir", dest / inside)

    assert result.exit_code == 1 and calls == []
    assert "mix the firmware tree" in _plain(result.output)


def test_build_refuses_a_work_dir_outside_the_run_directory_before_building(
    tmp_path, dest, plan_file, calls
):
    result = _invoke("build", plan_file, "--out", dest, "--work-dir", tmp_path / "elsewhere")

    assert result.exit_code == 1 and calls == []
    assert "outside the run directory" in _plain(result.output)
    assert not dest.exists()


def test_a_refused_rebuild_leaves_the_earlier_build_valid(tmp_path, dest, plan_file, calls):
    assert _invoke("build", plan_file, "--out", dest).exit_code == 0
    pinned = (dest / "inputs" / "input_0.bin").read_bytes()
    first, second = tmp_path / "a.bin", tmp_path / "b.bin"
    first.write_bytes(b"\x01" * 4)
    second.write_bytes(b"\x02" * 4)

    result = _invoke("build", plan_file, "--out", dest, "--input", first, "--input", second)

    assert result.exit_code == 1 and len(calls) == 1
    assert "not a new or empty directory" in _plain(result.output)
    assert (dest / "inputs" / "input_0.bin").read_bytes() == pinned
    assert _invoke("validate", "build", dest).exit_code == 0


def test_build_refuses_a_non_empty_directory_without_a_build(dest, plan_file, calls):
    dest.mkdir()
    (dest / "notes.txt").write_text("keep")

    result = _invoke("build", plan_file, "--out", dest)

    assert result.exit_code == 1 and calls == []
    assert sorted(path.name for path in dest.iterdir()) == ["notes.txt"]


def test_build_refuses_a_model_other_than_the_plans_before_writing(dest, plan, plan_file, calls):
    Path(plan.model.path).write_bytes(b"an edited model")

    result = _invoke("build", plan_file, "--out", dest)

    assert result.exit_code == 1 and calls == []
    assert "is not the model the plan was frozen against" in _plain(result.output)
    assert not dest.exists()


@pytest.mark.parametrize(
    "inputs,message",
    [((b"\x00" * 4,), "2 input(s); 1 input file"), ((b"\x00" * 3, b"\x00" * 4), "holds 3 bytes")],
)
def test_build_refuses_inputs_that_do_not_fit_the_model(
    tmp_path, dest, plan_file, calls, inputs, message
):
    args: list[str | Path] = []
    for index, data in enumerate(inputs):
        path = tmp_path / f"in{index}.bin"
        path.write_bytes(data)
        args += ["--input", path]

    result = _invoke("build", plan_file, "--out", dest, *args)

    assert result.exit_code == 1
    assert message in _plain(result.output)
    assert calls == [] and not dest.exists()


def test_build_takes_the_model_from_another_path(tmp_path, dest, plan, plan_file, calls):
    moved = tmp_path / "elsewhere" / "model.tflite"
    moved.parent.mkdir()
    Path(plan.model.path).rename(moved)

    missing = _invoke("build", plan_file, "--out", dest)
    assert missing.exit_code == 1
    assert "name the model where it is now" in _plain(missing.output)
    result = _invoke("build", plan_file, "--out", dest, "--model", moved)

    assert result.exit_code == 0, result.output
    assert calls[-1]["fixture"].model.path == dest / "inputs" / "model.tflite"


def test_build_shows_the_moved_model_hint_only_for_the_plans_path(tmp_path, dest, plan_file, calls):
    result = _invoke("build", plan_file, "--out", dest, "--model", tmp_path / "absent.tflite")

    assert result.exit_code == 1
    assert "Cannot read the model file" in _plain(result.output) and "hint" not in _plain(
        result.output
    )


# -- hpx validate build -------------------------------------------------------


def test_validate_build_verifies_and_prints_the_identity(out, plan, build):
    record = _saved(out, plan, build)

    result = _invoke("validate", "build", out)

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report == {
        "build": str(out / "build.json"),
        "inputs": [
            {"name": "signal", "bytes": 4, "path": str(out / "input" / "signal.bin")},
            {"name": "cache", "bytes": 4, "path": str(out / "input" / "cache.bin")},
        ],
        "identity": record.identity.to_dict(),
    }


def test_validate_build_takes_the_build_file_as_well_as_its_directory(out, plan, build):
    _saved(out, plan, build)

    result = _invoke("validate", "build", out / "build.json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["build"] == str(out / "build.json")


def test_validate_build_refuses_a_changed_pinned_file(out, plan, build):
    _saved(out, plan, build)
    assert build.flat_binary is not None
    build.flat_binary.path.write_bytes(b"other image")

    result = _invoke("validate", "build", out)

    assert result.exit_code == 1
    assert "Fixture hash mismatch" in _plain(result.output)


@pytest.fixture
def rt_plan_file(tmp_path, build, monkeypatch) -> Path:
    """A heliaRT plan whose mocked build links a runtime prepared under the HPX cache."""
    from helia_profiler.hostenv.cache_dirs import hpx_cache_root
    from tests.run_api_helpers import linking

    plan = plan_for(build, tmp_path / "rt-model.tflite", engine="helia-rt")
    assert plan.runtime is not None
    source, kernels = plan.runtime.source_commit, plan.runtime.kernels_commit
    assert source is not None and kernels is not None
    analysis = SimpleNamespace(
        inputs=tuple(io.tensor for io in build.inputs), outputs=build.output_tensors
    )
    monkeypatch.setattr("helia_profiler.run_inputs.analyze_run_model", lambda path: analysis)
    monkeypatch.setattr(
        "helia_profiler.prepared_runtimes.prepared_runtime",
        lambda name, version=None: SimpleNamespace(runtime=None),
    )

    def build_fixed_fixture(config, fixture, **options):
        made = linking(
            atfe_build(_build(config.work_dir)),
            hpx_cache_root(),
            "helia-rt",
            source,
            kernels,
        )
        return reseal(made, model=fixture.model, inputs=fixture.inputs)

    monkeypatch.setattr("helia_profiler._fixture_build.build_fixed_fixture", build_fixed_fixture)
    return plan.save(tmp_path / "rt-plan.json")


def test_build_saves_a_runtime_from_the_cache_under_the_cache_root(
    tmp_path, dest, rt_plan_file, monkeypatch
):
    result = _invoke("build", rt_plan_file, "--out", dest)

    assert result.exit_code == 0, result.output
    assert _invoke("validate", "build", dest).exit_code == 0
    runtime = FrozenRun.load(rt_plan_file).runtime
    assert runtime is not None

    monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path / "another-cache"))
    moved = _invoke("validate", "build", dest)

    assert moved.exit_code == 1
    text = _plain(moved.output)
    assert f"hpx runtimes prepare helia-rt {runtime.version}" in text and "HPX_CACHE_DIR" in text
    assert "roots=" not in text


def test_a_symlinked_cache_still_names_the_runtime_to_prepare(
    tmp_path, dest, rt_plan_file, monkeypatch
):
    (tmp_path / "cache").mkdir(exist_ok=True)
    link = tmp_path / "cache-link"
    link.symlink_to(tmp_path / "cache", target_is_directory=True)
    monkeypatch.setenv("HPX_CACHE_DIR", str(link))
    built = _invoke("build", rt_plan_file, "--out", dest)
    assert built.exit_code == 0, built.output
    for item in (tmp_path / "cache").rglob("*"):
        if item.is_file():
            item.unlink()

    result = _invoke("validate", "build", dest)

    assert result.exit_code == 1 and "hpx runtimes prepare helia-rt" in _plain(result.output)


def test_a_missing_file_under_the_run_directory_says_so(out, plan, build):
    _saved(out, plan, build)
    assert build.flat_binary is not None
    build.flat_binary.path.unlink()

    result = _invoke("validate", "build", out)

    assert result.exit_code == 1
    text = _plain(result.output)
    assert "is incomplete; build it again" in text and "roots=" not in text


def test_validate_build_refuses_a_missing_directory(tmp_path):
    result = _invoke("validate", "build", tmp_path / "absent")
    text = _plain(result.output)
    assert result.exit_code == 1 and "not found" in text and "incomplete" not in text


def test_a_missing_build_file_is_named_once(tmp_path):
    (tmp_path / "empty").mkdir()
    result = _invoke("validate", "build", tmp_path / "empty" / "build.json")
    assert result.exit_code == 1 and "build.json/build.json" not in _plain(result.output)


def test_a_refused_suite_option_is_named_by_its_flag(out, monkeypatch):
    monkeypatch.setattr("helia_profiler.cli.validate_cmd._cmd_validate", lambda **_: None)

    result = _invoke("validate", "-k", "kws", "build", out)

    assert result.exit_code == 2
    assert "-k" in _plain(result.output) and "--keyword" not in _plain(result.output)


def test_suite_options_do_not_reach_validate_build(out, monkeypatch):
    suite: list[dict[str, Any]] = []
    monkeypatch.setattr("helia_profiler.cli.validate_cmd._cmd_validate", suite.append)

    result = _invoke("validate", "--models", "kws", "build", out)

    # Rich may wrap mid-word inside its error box, whose rules differ by platform.
    squeezed = re.sub(r"[\s│└┘╰╯─]", "", unstyle(result.output))
    assert result.exit_code == 2 and "--models" in squeezed and suite == []


# -- hpx run ------------------------------------------------------------------


def _capture(build: FixtureBuild, *, state: str = "success") -> FixtureCaptureResult:
    return FixtureCaptureResult(
        state,
        build.timing_scope,
        status=0 if state == "success" else None,
        timing=FixtureTiming(
            65536, build.iterations, build.warmups, 32768, 4, 96_000_000, build.timing_scope
        )
        if state == "success"
        else None,
        error=None if state == "success" else "Probe enumeration mismatch",
    )


def test_run_captures_under_the_operator_guard(out, plan, build, monkeypatch):
    _saved(out, plan, build)
    seen: dict[str, Any] = {}

    def capture_fixture(request, *, guard):
        seen.update(request=request, guard=guard)
        return _capture(build)

    monkeypatch.setattr("helia_profiler.fixture_capture.capture_fixture", capture_fixture)

    result = _invoke("run", out, "--board", BOARD, "--jlink-serial", SERIAL, "--expect", "none")

    assert result.exit_code == 0, result.output
    assert seen["guard"] == OperatorGuard(BOARD, SERIAL)
    assert seen["request"].jlink_serial == SERIAL
    assert seen["request"].evidence_dir == out / "evidence-1"
    report = json.loads(result.stdout)
    assert report["succeeded"] is True and report["verification"] is None
    assert report["authority"] == "operator"
    assert report["evidence_dir"] == str(out / "evidence-1")
    assert report["identity"]["observation_hash"]
    assert report["timing"]["latency_s"] == pytest.approx(2 / build.iterations)
    assert (report["timing"]["iterations"], report["timing"]["warmups"]) == (100, 5)


def test_run_refuses_another_board_before_any_device_call(out, plan, build, monkeypatch):
    _saved(out, plan, build)

    def capture_fixture(request, *, guard):
        raise AssertionError("the capture ran for another board")

    monkeypatch.setattr("helia_profiler.fixture_capture.capture_fixture", capture_fixture)

    result = _invoke(
        "run", out, "--board", "apollo510b_evb", "--jlink-serial", SERIAL, "--expect", "none"
    )

    assert result.exit_code == 1
    assert "the operator named apollo510b_evb" in _plain(result.output)
    assert not (out / "evidence-1").exists()


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch, out, plan, build) -> list[dict[str, Any]]:
    """Mock run_build; record what ``hpx run`` asked of it."""
    record = _saved(out, plan, build)
    seen: list[dict[str, Any]] = []

    def run_build(compiled, **options):
        seen.append({"compiled": compiled, **options})
        return RunResult(compiled, _capture(build), record.identity, None)

    monkeypatch.setattr("helia_profiler.compiled_build.run_build", run_build)
    return seen


@pytest.mark.parametrize(
    "args,expected",
    [
        ((), Expect.host_litert(tolerance=Tolerance(0))),
        (
            ("--expect", "host-litert", "--tolerance", "1.5"),
            Expect.host_litert(tolerance=Tolerance(1.5)),
        ),
        (("--expect", "none", "--tolerance", "3"), None),
    ],
)
def test_run_maps_expect_and_tolerance(out, runs, args, expected):
    result = _invoke("run", out, "--board", BOARD, "--jlink-serial", SERIAL, *args)

    assert result.exit_code == 0, result.output
    (call,) = runs
    assert call["expect"] == expected
    assert call["jlink_serial"] == SERIAL and call["settle_seconds"] == 60.0


def test_run_offers_no_build_expectation(out, runs):
    # A build from hpx build carries no expected outputs, so Expect.from_build cannot succeed.
    result = _invoke("run", out, "--board", BOARD, "--jlink-serial", SERIAL, "--expect", "build")
    assert result.exit_code == 2 and runs == []


def test_run_compares_with_reference_files(tmp_path, out, runs):
    first, second = tmp_path / "score.bin", tmp_path / "cache.bin"

    result = _invoke(
        "run",
        out,
        "--board",
        BOARD,
        "--jlink-serial",
        SERIAL,
        "--reference",
        first,
        "--reference",
        second,
        "--tolerance",
        "1",
        "--settle-seconds",
        "30",
        "--evidence-dir",
        tmp_path / "evidence",
    )

    assert result.exit_code == 0, result.output
    (call,) = runs
    assert call["expect"] == Expect.from_files(first, second, tolerance=Tolerance(1))
    assert call["settle_seconds"] == 30.0
    assert call["evidence_dir"] == tmp_path / "evidence"


def test_run_refuses_references_with_an_expect(tmp_path, out, runs):
    result = _invoke(
        "run",
        out,
        "--board",
        BOARD,
        "--jlink-serial",
        SERIAL,
        "--expect",
        "host-litert",
        "--reference",
        tmp_path / "score.bin",
    )
    assert result.exit_code == 2 and runs == []


@pytest.mark.parametrize("tolerance", ["-1", "nan", "inf"])
def test_run_refuses_a_tolerance_that_is_not_a_finite_bound(out, runs, tolerance):
    result = _invoke(
        "run", out, "--board", BOARD, "--jlink-serial", SERIAL, "--tolerance", tolerance
    )
    assert result.exit_code == 2 and runs == []


def test_run_requires_the_board_and_the_serial(out, runs):
    assert _invoke("run", out, "--jlink-serial", SERIAL).exit_code == 2
    assert _invoke("run", out, "--board", BOARD).exit_code == 2
    assert runs == []


def test_each_run_takes_a_fresh_evidence_directory(out, runs):
    (out / "evidence-1").mkdir(parents=True)
    result = _invoke("run", out, "--board", BOARD, "--jlink-serial", SERIAL, "--expect", "none")
    assert result.exit_code == 0, result.output
    assert runs[0]["evidence_dir"] == out / "evidence-2"


@pytest.mark.parametrize(
    "state,matched,code", [("failure", None, 1), ("success", False, 3), ("success", True, 0)]
)
def test_run_exit_status_follows_the_capture_and_the_verification(
    out, plan, build, monkeypatch, state, matched, code
):
    record = _saved(out, plan, build)
    verification = (
        None
        if matched is None
        else Verification(
            ReferenceOutputs(ReferenceOrigin("host-litert"), (bytes(2),)),
            (
                TensorComparison(
                    name="score",
                    index=3,
                    dtype="int8",
                    shape=(1, 2),
                    reference_sha256="a" * 64,
                    observed_sha256="a" * 64 if matched else "b" * 64,
                    elements=2,
                    mismatched_elements=0 if matched else 2,
                    max_abs_error=0.0 if matched else 4.0,
                    max_abs_error_real=None,
                    within_tolerance=matched,
                ),
            ),
            Tolerance(0),
        )
    )
    monkeypatch.setattr(
        "helia_profiler.compiled_build.run_build",
        lambda compiled, **options: RunResult(
            compiled, _capture(build, state=state), record.identity, verification
        ),
    )

    result = _invoke("run", out, "--board", BOARD, "--jlink-serial", SERIAL)

    assert result.exit_code == code, result.output
    report = json.loads(result.stdout)
    assert report["succeeded"] is (state == "success")
    if state == "failure":
        assert report["error"] == "Probe enumeration mismatch" and report["timing"] is None
    else:
        assert report["verification"]["matched"] is matched
        assert report["verification"]["tensors"][0]["name"] == "score"


def _mismatch(build: FixtureBuild, error: float) -> Verification:
    return Verification(
        ReferenceOutputs(ReferenceOrigin("host-litert"), (bytes(8),)),
        (
            TensorComparison(
                name="score",
                index=3,
                dtype="float32",
                shape=(1, 2),
                reference_sha256="a" * 64,
                observed_sha256="b" * 64,
                elements=2,
                mismatched_elements=1,
                max_abs_error=error,
                max_abs_error_real=None,
                within_tolerance=False,
            ),
        ),
        Tolerance(0.5),
    )


@pytest.mark.parametrize("error", [float("inf"), float("nan")])
def test_run_reports_a_non_finite_error_as_null_and_exits_3(out, plan, build, monkeypatch, error):
    record = _saved(out, plan, build)
    monkeypatch.setattr(
        "helia_profiler.compiled_build.run_build",
        lambda compiled, **options: RunResult(
            compiled, _capture(build), record.identity, _mismatch(build, error)
        ),
    )

    result = _invoke("run", out, "--board", BOARD, "--jlink-serial", SERIAL)

    assert result.exit_code == 3, result.output
    verification = json.loads(result.stdout)["verification"]
    assert verification["matched"] is False and verification["tolerance"] == 0.5
    assert verification["tensors"] == [
        {"name": "score", "identical": False, "mismatched_elements": 1, "max_abs_error": None}
    ]


@pytest.mark.parametrize("settle", ["0", "-1", "nan", "300.5", "inf"])
def test_run_refuses_a_settle_time_the_capture_cannot_take(out, runs, settle):
    result = _invoke(
        "run", out, "--board", BOARD, "--jlink-serial", SERIAL, "--settle-seconds", settle
    )
    assert result.exit_code == 2 and runs == []


def test_run_takes_the_longest_settle_time_the_capture_takes(out, runs):
    result = _invoke(
        "run", out, "--board", BOARD, "--jlink-serial", SERIAL, "--settle-seconds", "300"
    )
    assert result.exit_code == 0, result.output
    assert runs[0]["settle_seconds"] == 300.0


def test_run_refuses_an_existing_evidence_directory_before_the_capture(tmp_path, out, runs):
    (tmp_path / "evidence").mkdir()

    result = _invoke(
        "run",
        out,
        "--board",
        BOARD,
        "--jlink-serial",
        SERIAL,
        "--evidence-dir",
        tmp_path / "evidence",
    )

    assert result.exit_code == 1 and runs == []
    assert "already exists" in _plain(result.output)


def test_run_takes_the_build_file_as_well_as_its_directory(out, runs):
    result = _invoke(
        "run", out / "build.json", "--board", BOARD, "--jlink-serial", SERIAL, "--expect", "none"
    )
    assert result.exit_code == 0, result.output
    assert runs[0]["evidence_dir"] == out / "evidence-1"


@pytest.mark.parametrize(
    "error", [ValueError("Unbounded settle time"), PermissionError(13, "Permission denied")]
)
def test_run_reports_a_value_or_os_error_without_a_traceback(out, plan, build, monkeypatch, error):
    _saved(out, plan, build)

    def run_build(compiled, **options):
        raise error

    monkeypatch.setattr("helia_profiler.compiled_build.run_build", run_build)

    result = _invoke("run", out, "--board", BOARD, "--jlink-serial", SERIAL, "--expect", "none")

    assert result.exit_code == 1 and isinstance(result.exception, SystemExit)
    assert _plain(result.output).startswith("Error:") and str(error) in _plain(result.output)


# -- OperatorGuard ------------------------------------------------------------


def test_the_operator_guard_allows_every_step_and_checks_the_target():
    guard = OperatorGuard(BOARD, SERIAL)
    target = hpx.FixtureTarget(BOARD, "AP510NFA-CBR", 0x410000)

    guard.check(require_free=True, remaining_s=90)
    guard.verify_target(target=target, jlink_serial=SERIAL)
    with pytest.raises(hpx.ConfigError, match="operator named apollo510_evb"):
        guard.verify_target(target=replace(target, board="apollo510b_evb"), jlink_serial=SERIAL)
    with pytest.raises(hpx.ConfigError, match=f"operator named {SERIAL}"):
        guard.verify_target(target=target, jlink_serial="999")


@pytest.mark.parametrize("board,serial", [("", SERIAL), (BOARD, " ")])
def test_the_operator_guard_needs_a_board_and_a_serial(board, serial):
    with pytest.raises(hpx.ConfigError, match="explicit"):
        OperatorGuard(board, serial)


def test_the_operator_guard_is_a_capture_guard():
    guard: hpx.FixtureCaptureGuard = OperatorGuard(BOARD, SERIAL)
    assert hpx.OperatorGuard is OperatorGuard and guard.board == BOARD


def test_the_summary_of_a_refused_terminal_states_no_latency(tmp_path, plan, build):
    from helia_profiler.run_directory import run_summary

    record = compiled(plan, build, Lifecycle.CARRY)
    refused = FixtureCaptureResult(
        "failure",
        build.timing_scope,
        timing=FixtureTiming(0, 0, 0, 32768, 4, 96_000_000, build.timing_scope),
        error="Invalid timing completion terminal",
    )

    summary = run_summary(
        RunResult(record, refused, record.identity, None), tmp_path, authority="operator"
    )

    assert summary["succeeded"] is False and summary["timing"]["latency_s"] is None


def test_each_run_command_epilog_keeps_its_example_in_one_paragraph():
    from helia_profiler.cli.validation_app import validate_app

    epilogs = [command.epilog for command in app.registered_commands] + [
        command.epilog for command in validate_app.registered_commands
    ]

    assert epilogs and all("\\\n\n" not in (epilog or "") for epilog in epilogs)


def test_an_os_error_naming_a_bracketed_path_prints_it_verbatim(out, monkeypatch):
    error = PermissionError(13, "Permission denied", "/data/[dataset]/plan.json")

    def refuse(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr("helia_profiler.run_directory.load_from", refuse)
    result = _invoke("validate", "build", out)

    assert result.exit_code == 1 and "/data/[dataset]/plan.json" in _plain(result.output)
