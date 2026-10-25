"""Fixture builds from a test request: targets, capabilities and the work directory."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

import helia_profiler
from helia_profiler import (
    EngineType,
    FixtureFile,
    FixtureIO,
    FixtureMethod,
    FixtureTensor,
    FixtureTimingScope,
    PerTensorQuantization,
    Placement,
    TypedFixture,
)
from helia_profiler._fixture_build import FIXTURE_CAPABILITIES
from helia_profiler._fixture_facts import fixture_capabilities
from helia_profiler.errors import ConfigError
from helia_profiler.fixture_capture import FIXTURE_CPU_HZ, FIXTURE_SETTLE_TICKS, FIXTURE_TIMER_HZ
from helia_profiler.fixture_target import supported_fixture_target
from tests.fixture_requests import (
    FixtureBackend,
    FixtureBuildRequest,
    FixturePlacement,
    build_fixture,
)

PACKAGE = Path(helia_profiler.__file__).resolve().parent


def _pin(path: Path, data: bytes) -> FixtureFile:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return FixtureFile(path, hashlib.sha256(data).hexdigest())


def _fixture(root: Path) -> TypedFixture:
    tensor = FixtureTensor("x", 0, "int8", (1, 4), PerTensorQuantization(0.5, 0))
    return TypedFixture(
        _pin(root / "model.tflite", b"model"),
        (FixtureIO(tensor, _pin(root / "in.bin", bytes(4))),),
        (FixtureIO(tensor, _pin(root / "out.bin", bytes(4))),),
    )


def _request(root: Path, **changes: object) -> FixtureBuildRequest:
    request = FixtureBuildRequest(
        fixture=_fixture(root),
        method=FixtureMethod(FixtureTimingScope.INVOKE_ONLY),
        work_dir=root / "work",
        engine=EngineType.HELIA_AOT,
        arena_size=65536,
        iterations=3,
        warmup=1,
    )
    return replace(request, **changes) if changes else request


def test_heliart_requests_accept_psram_weights(tmp_path):
    _request(
        tmp_path,
        engine=EngineType.HELIA_RT,
        backend=FixtureBackend.HELIA,
        placement=FixturePlacement(weights=Placement.PSRAM),
    )


def test_capabilities_report_the_qualified_contract() -> None:
    facts = fixture_capabilities()
    target, ap510b = facts.targets
    assert set(ap510b.engines) == {EngineType.HELIA_AOT, EngineType.LITERT}
    assert set(target.engines) == {EngineType.HELIA_AOT, EngineType.LITERT, EngineType.HELIA_RT}
    assert ap510b.board == "apollo510b_evb"
    assert ap510b.device == "AP510BFA-CBR"
    assert (target.board, target.clock_profile) == ("apollo510_evb", "lp")
    assert (target.cpu_hz, target.timer_hz, target.settle_ticks) == (
        FIXTURE_CPU_HZ,
        FIXTURE_TIMER_HZ,
        FIXTURE_SETTLE_TICKS,
    )
    assert {(c.engine, c.dtype, c.capability) for c in facts.engines} == {
        (engine, dtype, status)
        for engine, table in FIXTURE_CAPABILITIES.items()
        for dtype, status in table.items()
    }


def test_an_unqualified_target_is_refused(tmp_path: Path) -> None:
    target = replace(supported_fixture_target(), board="apollo4p_evb")
    with pytest.raises(ConfigError, match="Unsupported fixture target"):
        _request(tmp_path, target=target)


def _tiny_request(root: Path) -> FixtureBuildRequest:
    from helia_profiler.fixture_analysis import analyze_typed_fixture_model

    model = _pin(
        root / "tiny_cnn.tflite", (PACKAGE / "data" / "models" / "tiny_cnn.tflite").read_bytes()
    )
    analysis = analyze_typed_fixture_model(model.path)
    fixture = TypedFixture(
        model,
        tuple(
            FixtureIO(t, _pin(root / f"in{i}.bin", bytes(t.size_bytes)))
            for i, t in enumerate(analysis.inputs)
        ),
        tuple(
            FixtureIO(t, _pin(root / f"out{i}.bin", bytes(t.size_bytes)))
            for i, t in enumerate(analysis.outputs)
        ),
    )
    return replace(_request(root), fixture=fixture)


def test_a_build_does_not_pin_heliaaot_outputs_an_earlier_build_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("helia_aot")
    pytest.importorskip("ai_edge_litert")
    monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path / "cache"))
    request = _tiny_request(tmp_path)
    stale = _pin(request.work_dir / "aot_output" / "hpx_plan.json", b'{"stale": true}')
    build = build_fixture(request, compile=False)
    assert not stale.path.exists()
    assert all(output.path != stale.path for output in build.aot_outputs)


def test_a_foreign_work_directory_keeps_its_heliaaot_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("helia_aot")
    pytest.importorskip("ai_edge_litert")
    monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path / "cache"))
    request = _tiny_request(tmp_path)
    request.work_dir.mkdir(parents=True)
    (request.work_dir / "fixed-fixture-identity.json").write_text('{"other": true}')
    kept = _pin(request.work_dir / "aot_output" / "hpx_plan.json", b"{}")
    with pytest.raises(ConfigError, match="different fixture"):
        build_fixture(request, compile=False)
    assert kept.path.exists()


def test_ap510b_request_preserves_actual_target(tmp_path):
    from helia_profiler._fixture_build import _validate
    from helia_profiler.fixture_target import fixture_target_for_board

    default = _request(tmp_path)
    target = fixture_target_for_board("apollo510b_evb")
    request = replace(default, target=target)
    assert target.device == "AP510BFA-CBR"
    assert target.device != default.target.device
    assert request.to_config().target.board == "apollo510b_evb"
    _validate(request.to_config(), request.fixture)
    with pytest.raises(ValueError, match="Unsupported fixture target"):
        replace(target, device=default.target.device).verify()
    with pytest.raises(ConfigError, match="MRAM or TCM"):
        replace(request, placement=FixturePlacement(weights=Placement.PSRAM))


def test_ap510b_refuses_canonical_image_layout_drift(monkeypatch):
    import helia_profiler.fixture_target as targets

    original = targets.fixture_app_region

    def changed(region, board=targets.FIXTURE_BOARD):
        start, end = original(region, board)
        return (start, end - 4) if board == "apollo510b_evb" else (start, end)

    monkeypatch.setattr(targets, "fixture_app_region", changed)
    with pytest.raises(ValueError, match="image/sink layout"):
        targets.fixture_target_for_board("apollo510b_evb")
