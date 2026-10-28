"""Each engine declares the state lifecycles its firmware implements (#537)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from helia_profiler import (
    FixtureFile,
    FixtureIO,
    FixtureMethod,
    FixtureTensor,
    FixtureTimingScope,
    PerTensorQuantization,
    TypedFixture,
)
from helia_profiler import lifecycle as lifecycle_module
from helia_profiler.engines import EngineType, get_adapter
from helia_profiler.engines.base import EngineAdapter
from helia_profiler.errors import EngineError
from helia_profiler.fixtures.build import _validate
from helia_profiler.identity import Unknown
from helia_profiler.lifecycle import (
    InstanceBoundary,
    Lifecycle,
    first_invoke_window,
    lifecycle_invocations,
    require_lifecycle,
)
from tests.fixture_requests import FixtureBackend, FixtureBuildRequest

#: What each engine's firmware runs today. ExecuTorch reloads its program on
#: every invocation, so it cannot carry state.
DECLARED: dict[EngineType, frozenset[Lifecycle]] = {
    EngineType.LITERT: frozenset({Lifecycle.CARRY, Lifecycle.SINGLE}),
    EngineType.HELIA_RT: frozenset({Lifecycle.CARRY, Lifecycle.SINGLE}),
    EngineType.HELIA_AOT: frozenset({Lifecycle.CARRY, Lifecycle.SINGLE}),
    EngineType.EXECUTORCH: frozenset({Lifecycle.SINGLE}),
}


def test_every_engine_declares_a_lifecycle_set() -> None:
    assert set(DECLARED) == set(EngineType)
    for engine in EngineType:
        adapter = get_adapter(engine)
        assert isinstance(adapter, EngineAdapter)
        assert adapter.lifecycles, engine
        assert all(isinstance(item, Lifecycle) for item in adapter.lifecycles), engine


@pytest.mark.parametrize("engine", list(EngineType))
def test_adapter_declares_what_its_firmware_runs(engine: EngineType) -> None:
    assert get_adapter(engine).lifecycles == DECLARED[engine]


@pytest.mark.parametrize(
    ("engine", "lifecycle"),
    [(engine, item) for engine, declared in DECLARED.items() for item in declared],
)
def test_declared_lifecycle_is_accepted(engine: EngineType, lifecycle: Lifecycle) -> None:
    require_lifecycle(engine, lifecycle)


def test_executorch_refuses_carry_with_its_reason() -> None:
    with pytest.raises(EngineError) as raised:
        require_lifecycle(EngineType.EXECUTORCH, Lifecycle.CARRY)
    assert "reloads and re-initialises the program on every invocation" in str(raised.value)
    assert raised.value.hint == "Request a lifecycle ExecuTorch implements: single."


class _CarryOnlyAdapter:
    name = "Fake"
    lifecycles = frozenset({Lifecycle.CARRY})

    def lifecycle_refusal(self, lifecycle: Lifecycle) -> str:
        return f"Fake firmware cannot run {lifecycle.value}"


def test_undeclared_lifecycle_is_refused_with_the_engine_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[EngineType] = []

    def fake_get_adapter(engine: EngineType) -> _CarryOnlyAdapter:
        requested.append(engine)
        return _CarryOnlyAdapter()

    monkeypatch.setattr(lifecycle_module, "get_adapter", fake_get_adapter)

    require_lifecycle(EngineType.LITERT, Lifecycle.CARRY)
    with pytest.raises(EngineError) as raised:
        require_lifecycle(EngineType.LITERT, Lifecycle.SINGLE)

    assert str(raised.value).splitlines()[0] == "Fake firmware cannot run single"
    assert raised.value.hint == "Request a lifecycle Fake implements: carry."
    assert requested == [EngineType.LITERT, EngineType.LITERT]


def test_carry_runs_the_configured_invocations() -> None:
    assert lifecycle_invocations(Lifecycle.CARRY, 100, 5) == (100, 5)


def test_single_runs_one_invocation_without_warmups() -> None:
    assert lifecycle_invocations(Lifecycle.SINGLE, 100, 5) == (1, 0)


def _pin(path: Path, data: bytes) -> FixtureFile:
    path.write_bytes(data)
    return FixtureFile(path, hashlib.sha256(data).hexdigest())


@pytest.mark.parametrize(
    ("engine", "backend"),
    [
        (EngineType.LITERT, FixtureBackend.CMSIS_NN),
        (EngineType.HELIA_RT, FixtureBackend.HELIA),
        (EngineType.HELIA_AOT, None),
    ],
)
def test_single_invocation_counts_pass_fixture_validation(
    tmp_path: Path, engine: EngineType, backend: FixtureBackend | None
) -> None:
    iterations, warmup = lifecycle_invocations(Lifecycle.SINGLE, 100, 5)
    tensor = FixtureTensor("x", 0, "int8", (1, 4), PerTensorQuantization(0.5, 0))
    fixture = TypedFixture(
        _pin(tmp_path / "model.tflite", b"model"),
        (FixtureIO(tensor, _pin(tmp_path / "in.bin", bytes(4))),),
        (FixtureIO(tensor, _pin(tmp_path / "out.bin", bytes(4))),),
    )
    request = FixtureBuildRequest(
        fixture=fixture,
        method=FixtureMethod(FixtureTimingScope.INVOKE_ONLY),
        work_dir=tmp_path / "work",
        engine=engine,
        arena_size=65536,
        iterations=iterations,
        warmup=warmup,
        backend=backend,
    )
    config = request.to_config()

    _validate(config, fixture)

    assert (config.profiling.iterations, config.profiling.warmup) == (1, 0)


#: Where each engine runs a model's one-time initializer, from its firmware.
FIRST_INVOKE: dict[EngineType, tuple[InstanceBoundary, object]] = {
    EngineType.LITERT: (InstanceBoundary.BOOT, "declared"),
    EngineType.HELIA_RT: (InstanceBoundary.BOOT, "declared"),
    EngineType.HELIA_AOT: (InstanceBoundary.BOOT, False),
    EngineType.EXECUTORCH: (InstanceBoundary.INVOCATION, Unknown),
}


@pytest.mark.parametrize("engine", list(EngineType))
@pytest.mark.parametrize("declared", [True, False, Unknown("no analysis")])
def test_each_engine_states_its_instance_boundary_and_first_invocation(engine, declared) -> None:
    adapter = get_adapter(engine)
    boundary, initializer = FIRST_INVOKE[engine]

    answer = adapter.first_invoke_initializer(declared)
    assert adapter.instance_boundary is boundary
    if initializer == "declared":
        assert answer == declared
    elif initializer is Unknown:
        assert isinstance(answer, Unknown) and "every execute" in answer.reason
    else:
        assert answer is initializer


@pytest.mark.parametrize(
    "boundary,warmups,window",
    [
        (InstanceBoundary.BOOT, 5, "warmup"),
        (InstanceBoundary.BOOT, 0, "measured"),
        (InstanceBoundary.INVOCATION, 5, "measured"),
    ],
)
def test_the_first_invocation_falls_in_a_warmup_only_when_one_shares_its_instance(
    boundary, warmups, window
) -> None:
    assert first_invoke_window(boundary, warmups) == window
