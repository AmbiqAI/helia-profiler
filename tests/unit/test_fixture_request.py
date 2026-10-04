"""Typed fixture build requests: validation, path-free intent identity, and build results."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import helia_profiler
from helia_profiler._fixture_build import FIXTURE_CAPABILITIES, _aot_outputs, _engine_source
from helia_profiler.errors import ConfigError
from helia_profiler.fixture import (
    EngineType,
    FixtureBackend,
    FixtureBuildRequest,
    FixtureFile,
    FixtureIO,
    FixtureMethod,
    FixturePlacement,
    FixtureTensor,
    FixtureTimingScope,
    HeliaAotOptions,
    PerTensorQuantization,
    Placement,
    PreparedUpstreamRuntime,
    TypedFixture,
    build_fixed_fixture,
    build_fixture,
    fixture_capabilities,
    supported_fixture_target,
)
from helia_profiler.fixture_capture import FIXTURE_CPU_HZ, FIXTURE_SETTLE_TICKS, FIXTURE_TIMER_HZ

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


def test_intent_identity_ignores_where_files_and_work_live(tmp_path: Path) -> None:
    first = _request(tmp_path / "a")
    second = _request(tmp_path / "b" / "elsewhere")
    assert first.intent_identity == second.intent_identity


@pytest.mark.parametrize(
    "changes",
    [
        {"arena_size": 32768},
        {"iterations": 4},
        {"warmup": 2},
        {"method": FixtureMethod(FixtureTimingScope.RESTORE_AND_INVOKE)},
        {"aot": HeliaAotOptions(convert_args_json='{"memory": {"planner": "greedy"}}')},
        {"aot": HeliaAotOptions(cmsis_nn_requantize_inline_asm=False)},
        {"observe_aot_arenas": True},
        {"energy_gate": True},
    ],
)
def test_intent_identity_moves_with_every_build_input(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    assert _request(tmp_path, **changes).intent_identity != _request(tmp_path).intent_identity


def test_intent_identity_moves_with_fixture_content(tmp_path: Path) -> None:
    request = _request(tmp_path / "a")
    other = _request(tmp_path / "b")
    changed = _pin(tmp_path / "b" / "in.bin", b"\x01\x02\x03\x04")
    assert isinstance(other.fixture, TypedFixture)
    fixture = replace(other.fixture, inputs=(replace(other.fixture.inputs[0], data=changed),))
    assert replace(other, fixture=fixture).intent_identity != request.intent_identity


def test_convert_arguments_are_stored_canonically() -> None:
    assert (
        HeliaAotOptions(convert_args_json='{ "b": 1, "a": {"y": 2, "x": 1} }').convert_args_json
        == HeliaAotOptions(convert_args_json='{"a":{"x":1,"y":2},"b":1}').convert_args_json
        == '{"a":{"x":1,"y":2},"b":1}'
    )


@pytest.mark.parametrize("text", ["[1, 2]", "not json", '"text"'])
def test_convert_arguments_must_be_a_json_object(text: str) -> None:
    with pytest.raises(ConfigError, match="convert arguments"):
        HeliaAotOptions(convert_args_json=text)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"backend": FixtureBackend.CMSIS_NN}, "backend none"),
        ({"engine": EngineType.TFLM}, "backend cmsis_nn"),
        (
            {
                "engine": EngineType.TFLM,
                "backend": FixtureBackend.CMSIS_NN,
                "aot": HeliaAotOptions(),
            },
            "heliaAOT fixtures only",
        ),
        ({"placement": FixturePlacement(arena=Placement.TCM)}, "placement not qualified"),
        ({"placement": FixturePlacement(weights=Placement.TCM)}, "placement not qualified"),
        ({"engine": EngineType.EXECUTORCH}, "no fixed-fixture build"),
    ],
)
def test_requests_outside_the_qualified_contract_are_refused(
    tmp_path: Path, changes: dict[str, object], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        _request(tmp_path, **changes)


def test_request_config_carries_every_setting(tmp_path: Path) -> None:
    aot = HeliaAotOptions(
        prefix="m",
        linker_profile="lp",
        cmsis_nn_requantize_inline_asm=False,
        convert_args_json='{"memory": {"planner": "greedy"}}',
    )
    config = _request(tmp_path, aot=aot).to_config()
    assert config.engine.type is EngineType.HELIA_AOT
    assert config.engine.config == {
        "prefix": "m",
        "linker_profile": "lp",
        "cmsis_nn_requantize_inline_asm": False,
        "aot_args": {"memory": {"planner": "greedy"}},
    }
    assert (config.model.arena_location, config.model.weights_location) == (
        Placement.SRAM,
        Placement.MRAM,
    )
    assert (config.profiling.iterations, config.profiling.warmup) == (3, 1)
    assert config.work_dir == tmp_path / "work"


def test_aot_outputs_pin_plan_and_report_when_written(tmp_path: Path) -> None:
    assert _aot_outputs(tmp_path, "hpx") == ()
    plan = _pin(tmp_path / "aot_output" / "hpx_plan.json", b"{}")
    report = _pin(tmp_path / "aot_output" / "hpx_model" / "hpx_report.json", b"{}")
    assert _aot_outputs(tmp_path, "hpx") == (plan, report)
    _pin(tmp_path / "aot_output" / "other" / "hpx_plan.json", b"{}")
    with pytest.raises(ConfigError, match="More than one"):
        _aot_outputs(tmp_path, "hpx")


class _Distribution:
    def __init__(self, direct_url: str | None) -> None:
        self.version = "0.24.0"
        self._direct_url = direct_url

    def read_text(self, name: str) -> str | None:
        return self._direct_url if name == "direct_url.json" else None


@pytest.mark.parametrize(
    ("direct_url", "commit"),
    [
        (
            json.dumps({"url": "https://x", "vcs_info": {"vcs": "git", "commit_id": "5bec204b"}}),
            "5bec204b",
        ),
        (json.dumps({"url": "file:///x", "dir_info": {}}), None),
        (None, None),
    ],
)
def test_engine_source_records_the_install_commit(
    monkeypatch: pytest.MonkeyPatch, direct_url: str | None, commit: str | None
) -> None:
    monkeypatch.setattr("importlib.metadata.distribution", lambda _: _Distribution(direct_url))
    source = _engine_source(EngineType.HELIA_AOT)
    assert source is not None
    assert (source.package, source.version, source.commit) == ("helia-aot", "0.24.0", commit)
    assert _engine_source(EngineType.TFLM) is None


def test_capabilities_report_the_qualified_contract() -> None:
    facts = fixture_capabilities()
    (target,) = facts.targets
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


def test_request_build_matches_the_config_build_and_keeps_its_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A host-only heliaAOT build from a request renders what the equivalent config build renders."""
    pytest.importorskip("helia_aot")
    pytest.importorskip("ai_edge_litert")
    from helia_profiler.fixture_analysis import analyze_typed_fixture_model

    monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path / "cache"))
    model_bytes = (PACKAGE / "data" / "models" / "tiny_cnn.tflite").read_bytes()

    def request_in(root: Path) -> FixtureBuildRequest:
        model = _pin(root / "tiny_cnn.tflite", model_bytes)
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

    request = request_in(tmp_path / "request")
    built = build_fixture(request, compile=False)
    config_request = request_in(tmp_path / "config")
    by_config = build_fixed_fixture(
        config_request.to_config(),
        config_request.fixture,
        method=config_request.method,
        compile=False,
    )
    assert built.intent_identity == request.intent_identity == config_request.intent_identity
    assert by_config.intent_identity != built.intent_identity
    assert [(f.path.name, f.sha256) for f in built.generated_sources] == [
        (f.path.name, f.sha256) for f in by_config.generated_sources
    ]
    assert built.engine_source is not None and built.engine_source.package == "helia-aot"


def _runtime(root: Path, manifest: bytes) -> PreparedUpstreamRuntime:
    return PreparedUpstreamRuntime(
        _pin(root / "runtime.a", b"!<arch>\n"), root, _pin(root / "runtime.json", manifest)
    )


@pytest.mark.parametrize(
    "aot",
    [
        HeliaAotOptions(prefix="m"),
        HeliaAotOptions(module_name="m"),
        HeliaAotOptions(linker_profile="lp"),
        HeliaAotOptions(platform_name="apollo510_evb"),
    ],
)
def test_intent_identity_moves_with_each_heliaaot_option(
    tmp_path: Path, aot: HeliaAotOptions
) -> None:
    plain = _request(tmp_path, aot=HeliaAotOptions())
    assert _request(tmp_path, aot=aot).intent_identity != plain.intent_identity


def test_intent_identity_moves_with_engine_and_runtime(tmp_path: Path) -> None:
    tflm = _request(
        tmp_path,
        engine=EngineType.TFLM,
        backend=FixtureBackend.CMSIS_NN,
        runtime=_runtime(tmp_path / "rt1", b'{"a": 1}'),
    )
    other_runtime = replace(tflm, runtime=_runtime(tmp_path / "rt2", b'{"a": 2}'))
    helia_rt = replace(tflm, engine=EngineType.HELIA_RT, backend=FixtureBackend.HELIA)
    identities = {r.intent_identity for r in (_request(tmp_path), tflm, other_runtime, helia_rt)}
    assert len(identities) == 4


def test_default_heliaaot_options_hash_like_none(tmp_path: Path) -> None:
    assert (
        _request(tmp_path).intent_identity
        == _request(tmp_path, aot=HeliaAotOptions()).intent_identity
    )


def test_an_unqualified_target_is_refused(tmp_path: Path) -> None:
    target = replace(supported_fixture_target(), board="apollo4p_evb")
    with pytest.raises(ConfigError, match="Unsupported fixture target"):
        _request(tmp_path, target=target)


def test_request_config_carries_every_heliaaot_option(tmp_path: Path) -> None:
    aot = HeliaAotOptions(prefix="p", module_name="mod", linker_profile="lp", platform_name="plat")
    config = _request(tmp_path, aot=aot).to_config()
    assert config.engine.config == {
        "prefix": "p",
        "module_name": "mod",
        "linker_profile": "lp",
        "platform_name": "plat",
        "cmsis_nn_requantize_inline_asm": True,
    }


def test_engine_source_without_the_package_or_with_a_malformed_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from importlib import metadata

    def missing(_: str) -> None:
        raise metadata.PackageNotFoundError("helia-aot")

    monkeypatch.setattr("importlib.metadata.distribution", missing)
    assert _engine_source(EngineType.HELIA_AOT) is None
    for text in ("{not json", '["list"]', '{"vcs_info": "text"}', '{"vcs_info": {"commit_id": 7}}'):
        monkeypatch.setattr("importlib.metadata.distribution", lambda _, t=text: _Distribution(t))
        source = _engine_source(EngineType.HELIA_AOT)
        assert source is not None and source.commit is None


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


def test_a_request_without_the_energy_gate_keeps_its_identity(tmp_path: Path) -> None:
    request = _request(tmp_path)
    intent = {
        "fixture": request.fixture.identity,
        "method": request.method.timing_scope.value,
        "engine": request.engine.value,
        "backend": request.backend.value if request.backend else None,
        "arena_size": request.arena_size,
        "iterations": request.iterations,
        "warmup": request.warmup,
        "placement": [request.placement.arena.value, request.placement.weights.value],
        "target": asdict(request.target),
        "runtime": None,
        "aot": asdict(request.aot or HeliaAotOptions()),
        "observe_aot_arenas": False,
    }
    expected = hashlib.sha256(json.dumps(intent, sort_keys=True).encode()).hexdigest()
    assert request.intent_identity == expected


def test_only_a_gated_fixture_or_gated_power_capture_drives_the_gate_pin(tmp_path: Path) -> None:
    from helia_profiler.firmware.context import drives_gate_pin
    from helia_profiler.pipeline import PipelineContext

    config = _request(tmp_path).to_config()
    assert not config.power.gated_external_capture
    for fixture, expected in (
        (None, False),
        (SimpleNamespace(energy_gate=False), False),
        (SimpleNamespace(energy_gate=True), True),
    ):
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        ctx.fixture = cast(Any, fixture)
        assert drives_gate_pin(ctx) is expected
