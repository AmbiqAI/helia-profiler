"""Typed fixture build requests: validation, path-free intent identity, and build results."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

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
    TypedFixture,
    build_fixed_fixture,
    build_fixture,
    fixture_capabilities,
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
