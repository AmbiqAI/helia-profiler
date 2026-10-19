"""The run API conformance example runs end to end without a board."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest

import helia_profiler as hpx
from helia_profiler._fixture_record import compiled_fixture_identity
from helia_profiler.build_record import BuildRecord
from helia_profiler.expect import host_reference
from helia_profiler.fixture import (
    FixtureBuild,
    FixtureCaptureResult,
    FixtureTiming,
    TypedFixture,
)
from helia_profiler.lifecycle import Lifecycle
from tests.unit.test_fixture_record import _build, _pin

_EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "run_api_conformance.py"
_GCC = "arm-none-eabi-gcc (Arm GNU Toolchain 15.2.Rel1) 15.2.1 20251203\n"


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
        "helia_profiler.hostenv._proc.tool_output", lambda command, *, timeout_s: _GCC
    )
    return hpx.examples.tiny_cnn()


def _compiled(fixture: TypedFixture, root: Path) -> FixtureBuild:
    """A compiled record of *fixture*, as the host build would produce it."""
    build = replace(
        _build(root),
        model=fixture.model,
        inputs=fixture.inputs,
        output_tensors=tuple(output.tensor for output in fixture.outputs),
        state_policy=None,
    )
    return replace(build, build_identity=compiled_fixture_identity(build))


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
    plan = (
        hpx.Session()
        .with_model(fixture.model.path)
        .with_engine("helia-aot")
        .with_target(board="apollo510_evb", toolchain="arm-none-eabi-gcc")
        .freeze()
    )
    build = _compiled(fixture, out / "build")
    _fake_capture(monkeypatch, tmp_path / "raw", [build])

    result = BuildRecord(plan, build, Lifecycle.CARRY).run(
        guard=example.OperatorGuard("1160002255"),
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


def test_the_operator_guard_refuses_another_probe(example):
    guard = example.OperatorGuard("1160002255")

    guard.verify_target(target=None, jlink_serial="1160002255")
    with pytest.raises(RuntimeError, match="is not the declared"):
        guard.verify_target(target=None, jlink_serial="999")


def test_main_freezes_builds_saves_loads_and_runs(example, model, tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    built: list[FixtureBuild] = []

    def build_fixed_fixture(config, fixture, *, method, **options):
        built.append(_compiled(fixture, out / "build"))
        return built[0]

    for owner in ("_fixture_build", "fixture", "build_record"):
        monkeypatch.setattr(
            f"helia_profiler.{owner}.build_fixed_fixture", build_fixed_fixture, raising=False
        )
    _fake_capture(monkeypatch, tmp_path / "raw", built)

    status = example.main(
        ["--jlink-serial", "1160002255", "--engine", "helia-aot", "--out", str(out)]
    )

    report = json.loads(capsys.readouterr().out)
    assert status == 0 and report["succeeded"]
    assert report["identity"]["observation_hash"]
    assert report["verification"]["matched"] is True
    assert (out / "record.json").is_file()
