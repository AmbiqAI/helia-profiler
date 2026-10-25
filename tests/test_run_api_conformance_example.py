"""The run API conformance example runs end to end without a board."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

import helia_profiler as hpx
from helia_profiler import FixtureBuild, FixtureCaptureResult, FixtureTiming, TypedFixture
from helia_profiler.engines import EngineType
from helia_profiler.expect import host_reference
from helia_profiler.lifecycle import Lifecycle
from helia_profiler.placement import Placement
from tests.run_api_helpers import ATFE_OUTPUT, atfe_build, compiled, plan_for, reseal
from tests.unit.test_fixture_record import _build, _pin

_EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "run_api_conformance.py"


@pytest.fixture
def example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hpx_run_api_conformance", _EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    pytest.importorskip("ai_edge_litert")
    monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(
        "helia_profiler.hostenv._proc.tool_output", lambda command, *, timeout_s: ATFE_OUTPUT
    )
    return hpx.examples.tiny_cnn()


def _compiled(fixture: TypedFixture, root: Path, engine: str = "helia-aot") -> FixtureBuild:
    """A compiled record of *fixture*, as the host build would produce it."""
    return atfe_build(
        _build(root),
        engine=EngineType(engine),
        model=fixture.model,
        inputs=fixture.inputs,
        output_tensors=tuple(output.tensor for output in fixture.outputs),
        state_policy=None,
        arena_capacity=128 * 1024,
    )


def _device_capture(build: FixtureBuild, root: Path) -> FixtureCaptureResult:
    """What the board returns: the host LiteRT outputs, so verification matches."""
    reference = host_reference(
        build.model.read() if build.model else b"",
        [(io.tensor, io.data.read()) for io in build.inputs],
        build.output_tensors,
        invocations=build.warmups + build.iterations,
    )
    outputs = tuple(
        _pin(root / f"output_{index}.bin", data) for index, data in enumerate(reference.outputs)
    )
    return FixtureCaptureResult(
        "success",
        build.timing_scope,
        output=outputs[0],
        status=0,
        timing=FixtureTiming(
            32768, build.iterations, build.warmups, 32768, 4, 96_000_000, build.timing_scope
        ),
        outputs=outputs,
    )


def _fake_capture(monkeypatch: pytest.MonkeyPatch, root: Path, build: list[FixtureBuild]) -> None:
    monkeypatch.setattr(
        "helia_profiler.fixture_capture.capture_fixture",
        lambda request, *, guard: _device_capture(build[0], root),
    )


def test_the_report_states_identity_verification_and_measurements(
    example, model, tmp_path, monkeypatch
):
    out = tmp_path / "out"
    fixture = example.typed_fixture(model, out)
    build = _compiled(fixture, out / "build")
    plan = plan_for(build, fixture.model.path)
    _fake_capture(monkeypatch, tmp_path / "raw", [build])

    result = compiled(plan, build, Lifecycle.CARRY).run(
        guard=_Unconsulted(),
        jlink_serial="1160002255",
        evidence_dir=out / "evidence",
        settle_seconds=60.0,
        expect=example.Expect.host_litert(tolerance=example.Tolerance(max_abs_error=1)),
    )
    report = json.loads(json.dumps(example.report(result)))

    assert fixture.model.path.parent == out / "inputs"
    assert [io.data.read() for io in fixture.inputs] == [bytes(64)]
    assert report["succeeded"] and report["identity"]["observation_hash"]
    assert report["identity"]["run"]["lifecycle"] == "carry"
    assert report["verification"]["reference"] == "host-litert"
    assert report["verification"]["matched"] is True
    assert [t["name"] for t in report["verification"]["tensors"]] == ["probabilities"]
    assert report["measurements"]["latency_s"] == pytest.approx(1 / build.iterations)


class _Unconsulted:
    """The mocked capture never asks a guard."""

    def check(self, *, require_free: bool, remaining_s: float) -> None:
        raise AssertionError("the mocked capture consulted the guard")

    def verify_target(self, *, target: object, jlink_serial: str) -> None:
        raise AssertionError("the mocked capture consulted the guard")


def test_the_placeholder_guard_refuses_every_capture(example):
    guard = example.PlaceholderGuard()

    with pytest.raises(RuntimeError, match="pass your harness's board guard"):
        guard.check(require_free=True, remaining_s=90)
    with pytest.raises(RuntimeError, match="pass your harness's board guard"):
        guard.verify_target(target=None, jlink_serial="1160002255")


def _patch_build(monkeypatch, out: Path, cache: Path, engine: str, prepared: object):
    calls: list[dict[str, Any]] = []
    built: list[FixtureBuild] = []

    def build_fixed_fixture(config, fixture, **options):
        calls.append({"config": config, **options})
        build = _compiled(fixture, out / "build", engine)
        if engine != "helia-aot":
            manifest = _pin(cache / "runtimes" / engine / "provider-manifest.json", b"{}")
            build = reseal(build, runtime_manifest=manifest)
        built.append(build)
        return build

    for owner in ("_fixture_build", "compiled_build"):
        monkeypatch.setattr(
            f"helia_profiler.{owner}.build_fixed_fixture", build_fixed_fixture, raising=False
        )
    monkeypatch.setattr(
        "helia_profiler.prepared_runtimes.prepared_runtime",
        lambda name, version=None: SimpleNamespace(runtime=prepared),
    )
    return calls, built


@pytest.mark.parametrize(
    "engine,backend", [("helia-aot", None), ("helia-rt", "helia"), ("litert", "cmsis_nn")]
)
def test_main_builds_with_what_a_fixture_build_requires(
    example, model, tmp_path, monkeypatch, capsys, engine, backend
):
    out = tmp_path / "out"
    prepared = object()
    calls, _ = _patch_build(monkeypatch, out, tmp_path / "cache", engine, prepared)

    status = example.main(["--build-only", "--engine", engine, "--out", str(out)])

    report = json.loads(capsys.readouterr().out)
    assert status == 0 and report["identity"]["intent_hash"]
    (call,) = calls
    config = call["config"]
    assert (config.target.toolchain, config.target.clock.cpu) == ("atfe", "lp")
    assert (config.model.arena_location, config.model.weights_location) == (
        Placement.SRAM,
        Placement.MRAM,
    )
    assert config.model.arena_size == 128 * 1024 and config.work_dir == out / "build"
    assert config.engine.backend == backend and call["compile"] is True
    assert call["runtime"] is (None if engine == "helia-aot" else prepared)
    saved = json.dumps(json.loads((out / "build.json").read_text())["build"])
    assert str(tmp_path) not in saved
    assert ('"cache"' in saved) is (engine != "helia-aot")


def test_main_refuses_to_capture_before_building(example, tmp_path, monkeypatch, capsys):
    calls, _ = _patch_build(monkeypatch, tmp_path / "out", tmp_path / "cache", "helia-aot", None)
    with pytest.raises(SystemExit) as exit_info:
        example.main(["--out", str(tmp_path / "out")])
    assert exit_info.value.code == 2 and "--build-only" in capsys.readouterr().err
    assert calls == []


def test_run_conformance_captures_and_verifies(example, model, tmp_path, monkeypatch):
    out = tmp_path / "out"
    _, built = _patch_build(monkeypatch, out, tmp_path / "cache", "helia-aot", None)
    _fake_capture(monkeypatch, tmp_path / "raw", built)

    result = example.run_conformance(
        model=model,
        engine="helia-aot",
        board="apollo510_evb",
        jlink_serial="1160002255",
        out=out,
        guard=_Unconsulted(),
    )

    assert result.succeeded and result.identity.observation_hash
    assert result.verification is not None and result.verification.matched


def test_each_capture_gets_a_fresh_evidence_directory(example, tmp_path):
    first = example._fresh(tmp_path / "evidence")
    assert first == tmp_path / "evidence"
    first.mkdir()
    (tmp_path / "evidence-1").mkdir()
    assert example._fresh(tmp_path / "evidence") == tmp_path / "evidence-2"
