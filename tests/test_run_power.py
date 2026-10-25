"""Power through the run API: the gross MCU-rail route and supply-only timing."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import helia_profiler as hpx
from helia_profiler import (
    FixtureBatchCompletion,
    FixtureBoardPowerRequest,
    FixtureBuild,
    FixtureCaptureResult,
    FixtureMethod,
    FixtureOutput,
    FixturePowerSync,
    FixtureRailEnergy,
    FixtureRailPowerRequest,
    FixtureTiming,
    FixtureTimingScope,
    TypedFixture,
)
from helia_profiler._fixture_build import FIXTURE_CAPABILITIES
from helia_profiler.compiled_build import CompiledBuild, _binding
from helia_profiler.engines import EngineType
from helia_profiler.errors import ConfigError
from helia_profiler.fixture_capture import FixtureCaptureRequest
from helia_profiler.fixture_target import fixture_target_for_board
from helia_profiler.identity import EnergyFacts, Unknown
from helia_profiler.lifecycle import Lifecycle
from helia_profiler.runtime_records import runtime
from tests.run_api_helpers import ATFE_OUTPUT, atfe_build, compiled, linking, plan_for, reseal
from tests.unit.test_fixture_record import _build, _pin

_WIRING = FixturePowerSync(29, 36, 14)
_POWER_METHOD = FixtureMethod(FixtureTimingScope.RESTORE_AND_INVOKE, power_sync=_WIRING)
_RESTORE = FixtureTimingScope.RESTORE_AND_INVOKE


def _rail(**change: Any) -> FixtureRailPowerRequest:
    fields: dict[str, Any] = {
        "serial": "JS320-001",
        "rail": "MCU",
        "io_voltage": 3.3,
        "sync_gpio_pin": 29,
        "state_gpio_pin": 36,
        "go_gpio_pin": 14,
        **change,
    }
    return FixtureRailPowerRequest(**fields)


_SUPPLY = FixtureBoardPowerRequest("joulescope", "JS320-001", "VDD")


class _Authority:
    """Records each call a capture makes on it."""

    def __init__(self, name: str, calls: list[tuple[str, str]]) -> None:
        self.name, self.calls = name, calls

    def check(self, *, require_free: bool, remaining_s: float) -> None:
        self.calls.append((self.name, "check"))

    def verify_target(self, **kwargs: object) -> None:
        self.calls.append((self.name, "verify_target"))

    def verify_power_device(self, *, serial: str, rail: str) -> None:
        self.calls.append((self.name, f"verify_power_device {serial} {rail}"))


@pytest.fixture(autouse=True)
def compiler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "helia_profiler.hostenv._proc.tool_output", lambda command, *, timeout_s: ATFE_OUTPUT
    )


@pytest.fixture
def build(tmp_path: Path) -> FixtureBuild:
    return atfe_build(_build(tmp_path / "run"))


def _as(build: FixtureBuild, engine: str, **change: Any) -> FixtureBuild:
    """*build* as an *engine* build; a LiteRT one links the prepared record's runtime."""
    if engine == "litert":
        record = runtime("litert")
        assert record is not None and record.kernels is not None
        assert build.binary is not None
        root = build.binary.path.parents[1]
        build = linking(build, root, engine, record.source.commit, record.kernels.commit)
        return reseal(build, **change)
    selected = EngineType(engine)
    capabilities = (("int8", FIXTURE_CAPABILITIES[selected]["int8"]),)
    return reseal(build, engine=selected, capabilities=capabilities, **change)


def _powered(tmp_path: Path, build: FixtureBuild, engine: str = "helia-aot") -> CompiledBuild:
    wired = _as(build, engine, timing_scope=_RESTORE, power_sync=_WIRING)
    return compiled(plan_for(wired, tmp_path / "model.tflite"), wired, Lifecycle.CARRY)


# build()


class _Compiler:
    """Stands in for the firmware build: returns *build* as the method and plan compile it."""

    def __init__(self, build: FixtureBuild) -> None:
        self.build = build
        self.calls: list[dict[str, Any]] = []

    def __call__(self, config: Any, fixture: Any, **options: Any) -> FixtureBuild:
        self.calls.append({"config": config, **options})
        method = options["method"]
        return _as(
            self.build,
            config.engine.type.value,
            timing_scope=method.timing_scope,
            power_sync=method.power_sync,
            iterations=config.profiling.iterations,
            warmups=config.profiling.warmup,
        )


@pytest.fixture
def compiler_calls(monkeypatch: pytest.MonkeyPatch, build: FixtureBuild) -> _Compiler:
    fake = _Compiler(build)
    monkeypatch.setattr("helia_profiler._fixture_build.build_fixed_fixture", fake)
    return fake


def _fixture(build: FixtureBuild) -> TypedFixture:
    assert build.model is not None
    return TypedFixture(
        build.model, build.inputs, tuple(FixtureOutput(t) for t in build.output_tensors)
    )


#: The compiler is a stand-in, so the runtime it links is never opened.
_LINKED: Any = object()


@pytest.mark.parametrize("engine", ["helia-aot", "litert"])
def test_build_compiles_the_rail_route_for_heliaaot_and_litert_on_ap510(
    tmp_path, build, compiler_calls, engine
):
    plan = plan_for(build, tmp_path / "model.tflite", engine=engine)

    record = hpx.build(
        plan, _fixture(build), method=_POWER_METHOD, lifecycle=Lifecycle.CARRY, runtime=_LINKED
    )

    (call,) = compiler_calls.calls
    assert call["method"] is _POWER_METHOD and call["compile"] is True
    assert record.build.power_sync == _WIRING and record.build.target.board == "apollo510_evb"
    assert record.binding == _binding(plan, Lifecycle.CARRY, record.build)


@pytest.mark.parametrize(
    ("engine", "board", "lifecycle", "message"),
    [
        (
            "helia-aot",
            None,
            Lifecycle.SINGLE,
            "not a Lifecycle.SINGLE one, which runs the model stateless",
        ),
        ("helia-rt", None, Lifecycle.CARRY, "heliaAOT and LiteRT builds, not helia-rt"),
        ("executorch", None, Lifecycle.CARRY, "heliaAOT and LiteRT builds, not executorch"),
        ("helia-aot", "apollo510b_evb", Lifecycle.CARRY, "apollo510_evb, not apollo510b_evb"),
        ("litert", "apollo4p_evb", Lifecycle.CARRY, "apollo510_evb, not apollo4p_evb"),
    ],
    ids=["single", "helia-rt", "executorch", "ap510b", "apollo4p"],
)
def test_build_refuses_power_outside_the_route(
    tmp_path, build, compiler_calls, engine, board, lifecycle, message
):
    plan = plan_for(build, tmp_path / "model.tflite", engine=engine, board=board)

    with pytest.raises(ConfigError, match=message):
        hpx.build(plan, _fixture(build), method=_POWER_METHOD, lifecycle=lifecycle)
    assert compiler_calls.calls == []


def test_the_binding_covers_the_power_wiring(tmp_path, build):
    restore = reseal(build, timing_scope=_RESTORE)
    plan = plan_for(restore, tmp_path / "model.tflite")
    bindings = {
        _binding(plan, Lifecycle.CARRY, reseal(restore, power_sync=wiring))
        for wiring in (None, _WIRING, FixturePowerSync(29, 36, 15))
    }
    assert len(bindings) == 3


# run_build()


class _Device:
    def __init__(self) -> None:
        self.reply: FixtureCaptureResult | None = None
        self.calls: list[tuple[FixtureCaptureRequest, Any]] = []

    def capture(self, request: FixtureCaptureRequest, *, guard: Any) -> FixtureCaptureResult:
        self.calls.append((request, guard))
        assert self.reply is not None
        return self.reply


@pytest.fixture
def device(monkeypatch: pytest.MonkeyPatch) -> _Device:
    device = _Device()
    monkeypatch.setattr("helia_profiler.fixture_capture.capture_fixture", device.capture)
    return device


def _energy(root: Path) -> FixtureRailEnergy:
    raw = _pin(root / "rail-power.json", b"{}")
    completion = FixtureBatchCompletion(
        2, "joulescope_sensor_sample_id", 1_000_000, 10, 20, 30, 530, 540, raw
    )
    return FixtureRailEnergy(
        100, "gross_rail_restore_and_invoke", "MCU", "JS320-001", "none",
        0.5, 0.01, 0.0055, 0.005, 0.00005, raw, completion,
    )  # fmt: skip


def _success(root: Path, energy: FixtureRailEnergy | None) -> FixtureCaptureResult:
    output = _pin(root / "output.bin", b"\x01\x02")
    return FixtureCaptureResult(
        "success",
        _RESTORE,
        output=output,
        status=0,
        timing=FixtureTiming(16384, 100, 5, 32768, 4, 96_000_000, _RESTORE),
        outputs=(output, _pin(root / "output_1.bin", bytes(4))),
        rail_energy=energy,
    )


def _run(record: CompiledBuild, tmp_path: Path, **options: Any):
    calls: list[tuple[str, str]] = []
    options.setdefault("guard", _Authority("board", calls))
    options.setdefault("settle_seconds", 30.0)
    result = hpx.run_build(
        record, jlink_serial="1160002255", evidence_dir=tmp_path / "evidence", **options
    )
    return result, calls


def test_a_rail_request_reaches_the_capture_and_its_energy_the_result(tmp_path, build, device):
    record = _powered(tmp_path, build)
    energy = _energy(tmp_path / "raw")
    device.reply = _success(tmp_path / "raw", energy)
    calls: list[tuple[str, str]] = []
    power = _rail()

    result, _ = _run(
        record,
        tmp_path,
        guard=_Authority("board", calls),
        power=power,
        power_guard=_Authority("station", calls),
    )

    ((request, guard),) = device.calls
    assert request.rail_power is power and request.board_power is None
    assert request.power_sync == _WIRING and request.timing_scope is _RESTORE
    assert result.power is power and result.energy is energy
    assert result.energy is not None and result.energy.completion is not None
    assert result.energy.completion.protocol_version == 2
    guard.verify_target(target=record.build.target, jlink_serial="1160002255")
    guard.check(require_free=True, remaining_s=10)
    guard.verify_power_device(serial="JS320-001", rail="MCU")
    assert calls == [
        ("board", "verify_target"),
        ("board", "check"),
        ("station", "check"),
        ("station", "verify_power_device JS320-001 MCU"),
    ]


def test_one_guard_holding_both_authorities_is_asked_once(tmp_path, build, device):
    record = _powered(tmp_path, build)
    device.reply = _success(tmp_path / "raw", _energy(tmp_path / "raw"))
    calls: list[tuple[str, str]] = []
    authority = _Authority("station", calls)

    _run(record, tmp_path, guard=authority, power=_rail(), power_guard=authority)

    ((_, guard),) = device.calls
    guard.check(require_free=False, remaining_s=10)
    assert calls == [("station", "check")]


@pytest.mark.parametrize("engine", ["helia-aot", "litert"])
def test_both_route_engines_run_with_a_rail_request(tmp_path, build, device, engine):
    record = _powered(tmp_path, build, engine)
    device.reply = _success(tmp_path / "raw", _energy(tmp_path / "raw"))

    result, _ = _run(record, tmp_path, power=_rail(), power_guard=_Authority("station", []))

    assert result.succeeded and result.energy is not None


def test_a_failed_power_capture_carries_no_energy(tmp_path, build, device):
    record = _powered(tmp_path, build)
    device.reply = FixtureCaptureResult("failure", _RESTORE, error="READY never came")

    result, _ = _run(record, tmp_path, power=_rail(), power_guard=_Authority("station", []))

    assert not result.succeeded and result.energy is None


def test_supply_only_timing_passes_the_board_request(tmp_path, build, device):
    plan = plan_for(build, tmp_path / "model.tflite")
    record = compiled(plan, build, Lifecycle.CARRY)
    device.reply = replace(
        _success(tmp_path / "raw", None),
        timing_scope=FixtureTimingScope.INVOKE_ONLY,
        timing=FixtureTiming(16384, 100, 5, 32768, 4, 96_000_000, FixtureTimingScope.INVOKE_ONLY),
    )

    result, _ = _run(record, tmp_path, power=_SUPPLY, power_guard=_Authority("station", []))

    ((request, guard),) = device.calls
    assert request.board_power is _SUPPLY and request.rail_power is None
    assert request.power_sync is None and callable(guard.verify_power_device)
    assert result.power is _SUPPLY and result.energy is None


class _NoCheck:
    def verify_power_device(self, *, serial: str, rail: str) -> None: ...


class _NoVerifyPowerDevice:
    def check(self, *, require_free: bool, remaining_s: float) -> None: ...


_PARTIAL_GUARDS = {"no-check": _NoCheck, "no-verify": _NoVerifyPowerDevice}


def _timing_only(tmp_path: Path, build: FixtureBuild) -> CompiledBuild:
    return compiled(plan_for(build, tmp_path / "model.tflite"), build, Lifecycle.CARRY)


@pytest.mark.parametrize(
    ("which", "options", "message"),
    [
        ("timing", {"power": _rail()}, "no power wiring"),
        ("power", {"power": _rail(go_gpio_pin=15)}, r"pins \(sync 29, state 36, GO 15\) differ"),
        ("power", {"power": _rail(rail="VDDIO")}, "MCU rail only, not 'VDDIO'"),
        ("power", {}, "needs a FixtureRailPowerRequest"),
        ("power", {"power": _SUPPLY}, "supply-only FixtureBoardPowerRequest measures no energy"),
        ("power", {"power": "MCU"}, "Unknown power request str"),
        ("power", {"power": _rail(), "power_guard": None}, "needs a power_guard"),
        ("timing", {"power": _SUPPLY, "power_guard": None}, "needs a power_guard"),
        ("timing", {"power_guard": "station"}, "without a power request"),
        ("power", {"power": _rail(), "power_guard": object()}, "is not a FixturePowerGuard"),
        ("power", {"power": _rail(), "power_guard": "no-check"}, "is not a FixturePowerGuard"),
        ("power", {"power": _rail(), "power_guard": "no-verify"}, "is not a FixturePowerGuard"),
        ("timing", {"power": _SUPPLY, "power_guard": "no-check"}, "is not a FixturePowerGuard"),
        ("timing", {"power": _SUPPLY, "power_guard": "no-verify"}, "is not a FixturePowerGuard"),
    ],
    ids=[
        "no-wiring",
        "wiring",
        "rail",
        "missing",
        "supply-on-power-build",
        "type",
        "no-guard",
        "supply-no-guard",
        "stray-guard",
        "guard-type",
        "rail-guard-without-check",
        "rail-guard-without-verify-power-device",
        "supply-guard-without-check",
        "supply-guard-without-verify-power-device",
    ],
)
def test_run_build_refuses_a_power_mismatch_before_the_device(
    tmp_path, build, device, which, options, message
):
    record = _powered(tmp_path, build) if which == "power" else _timing_only(tmp_path, build)
    if "power" in options and "power_guard" not in options:
        options = {**options, "power_guard": _Authority("station", [])}
    named = options.get("power_guard")
    if named == "station":
        options = {**options, "power_guard": _Authority("station", [])}
    elif isinstance(named, str) and named in _PARTIAL_GUARDS:
        options = {**options, "power_guard": _PARTIAL_GUARDS[named]()}

    with pytest.raises(ConfigError, match=message):
        _run(record, tmp_path, **options)
    assert device.calls == []
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize(
    ("engine", "board", "lifecycle", "message"),
    [
        ("helia-rt", "apollo510_evb", Lifecycle.CARRY, "not helia-rt"),
        ("helia-aot", "apollo510b_evb", Lifecycle.CARRY, "apollo510_evb, not apollo510b_evb"),
        ("helia-aot", "apollo510_evb", Lifecycle.SINGLE, "not a Lifecycle.SINGLE one"),
    ],
    ids=["engine", "board", "single"],
)
def test_run_build_refuses_a_power_build_outside_the_route(
    tmp_path, build, device, monkeypatch, engine, board, lifecycle, message
):
    target = fixture_target_for_board(board)
    wired = _as(build, engine, timing_scope=_RESTORE, power_sync=_WIRING, target=target)
    if lifecycle is Lifecycle.SINGLE:
        wired = reseal(wired, iterations=1, warmups=0)
    record = compiled(plan_for(wired, tmp_path / "model.tflite"), wired, lifecycle)
    # Only build() makes these; a hand-made one still meets the route check at run time.
    monkeypatch.setattr(CompiledBuild, "verify", lambda self: None)

    with pytest.raises(ConfigError, match=message):
        _run(record, tmp_path, power=_rail(), power_guard=_Authority("station", []))
    assert device.calls == []


# The run's identity


def test_the_identity_states_the_rail_energy_method(tmp_path, build, device):
    record = _powered(tmp_path, build)
    device.reply = _success(tmp_path / "raw", _energy(tmp_path / "raw"))

    result, _ = _run(record, tmp_path, power=_rail(), power_guard=_Authority("station", []))

    expected = EnergyFacts("gross_rail_restore_and_invoke", "MCU", "none", 2)
    assert result.identity.run.energy == expected
    assert record.identity.run.energy == replace(
        expected, rail=Unknown("the run's power request names the rail")
    )
    assert result.identity.to_dict()["run"]["energy"] == {
        "scope": "gross_rail_restore_and_invoke",
        "rail": "MCU",
        "subtraction": "none",
        "completion_protocol": 2,
    }


def test_a_failed_power_run_states_its_method_with_the_requested_rail(tmp_path, build, device):
    record = _powered(tmp_path, build)
    device.reply = FixtureCaptureResult("failure", _RESTORE, error="READY never came")

    result, _ = _run(record, tmp_path, power=_rail(), power_guard=_Authority("station", []))

    assert result.energy is None
    assert result.identity.run.energy == EnergyFacts(
        "gross_rail_restore_and_invoke", "MCU", "none", 2
    )


def test_supply_only_timing_states_no_energy(tmp_path, build, device):
    record = _timing_only(tmp_path, build)
    device.reply = replace(
        _success(tmp_path / "raw", None),
        timing_scope=FixtureTimingScope.INVOKE_ONLY,
        timing=FixtureTiming(16384, 100, 5, 32768, 4, 96_000_000, FixtureTimingScope.INVOKE_ONLY),
    )

    result, _ = _run(record, tmp_path, power=_SUPPLY, power_guard=_Authority("station", []))

    assert result.identity.run.energy is None and record.identity.run.energy is None


# build() for a plan frozen with power


def test_build_refuses_a_power_plan_with_a_timing_method(tmp_path, build, compiler_calls):
    plan = plan_for(build, tmp_path / "model.tflite", power=True)
    timing = FixtureMethod(_RESTORE)

    with pytest.raises(ConfigError, match="frozen with power enabled") as raised:
        hpx.build(plan, _fixture(build), method=timing, runtime=_LINKED)
    assert "power_sync=FixturePowerSync" in str(raised.value.hint)
    assert "power= to run_build()" in str(raised.value.hint)
    assert compiler_calls.calls == []


def test_build_compiles_a_power_plan_with_a_power_method(tmp_path, build, compiler_calls):
    plan = plan_for(build, tmp_path / "model.tflite", power=True)

    record = hpx.build(plan, _fixture(build), method=_POWER_METHOD, runtime=_LINKED)

    assert record.build.power_sync == _WIRING


def test_a_power_plan_compiles_without_the_profile_power_section(tmp_path, build, compiler_calls):
    plan = plan_for(build, tmp_path / "model.tflite", power=True)

    hpx.build(plan, _fixture(build), method=_POWER_METHOD, runtime=_LINKED)

    # build_fixed_fixture refuses a config with power enabled.
    assert [call["config"].power.enabled for call in compiler_calls.calls] == [False]


# Messages


@pytest.mark.parametrize(
    ("engine", "board", "message", "hint"),
    [
        (None, "apollo510_evb", "the plan names no engine", "Use a heliaAOT or LiteRT plan."),
        ("helia-rt", "apollo510_evb", "not helia-rt", "Use a heliaAOT or LiteRT plan."),
        ("helia-aot", None, "the plan names no board", "Build for apollo510_evb."),
        ("helia-aot", "apollo4p_evb", "not apollo4p_evb", "Build for apollo510_evb."),
    ],
    ids=["no-engine", "engine", "no-board", "board"],
)
def test_route_refusals_name_what_is_missing_and_what_to_do(engine, board, message, hint):
    from helia_profiler._run_contracts import require_power_route

    with pytest.raises(ConfigError, match=message) as raised:
        require_power_route(engine, board, Lifecycle.CARRY)
    assert "None" not in str(raised.value) and raised.value.hint == hint


# settle_seconds


@pytest.mark.parametrize("seconds", [61.0, 0, -1.0, math.nan, math.inf, None, "30", True])
def test_a_rail_capture_refuses_an_unbounded_settle_time(tmp_path, build, device, seconds):
    record = _powered(tmp_path, build)

    with pytest.raises(ConfigError, match="settle_seconds must be a number above 0"):
        _run(
            record,
            tmp_path,
            power=_rail(),
            power_guard=_Authority("station", []),
            settle_seconds=seconds,
        )
    assert device.calls == []
    assert not (tmp_path / "evidence").exists()


def test_the_settle_refusal_names_the_rail_limit(tmp_path, build, device):
    record = _powered(tmp_path, build)

    with pytest.raises(ConfigError, match="at most 60 for a rail capture, not 61.0"):
        _run(
            record,
            tmp_path,
            power=_rail(),
            power_guard=_Authority("station", []),
            settle_seconds=61.0,
        )


def test_a_rail_capture_accepts_the_full_60_seconds(tmp_path, build, device):
    record = _powered(tmp_path, build)
    device.reply = _success(tmp_path / "raw", _energy(tmp_path / "raw"))

    _run(record, tmp_path, power=_rail(), power_guard=_Authority("station", []), settle_seconds=60)

    ((request, _),) = device.calls
    assert request.settle_seconds == 60


def _invoke_only(root: Path) -> FixtureCaptureResult:
    return replace(
        _success(root, None),
        timing_scope=FixtureTimingScope.INVOKE_ONLY,
        timing=FixtureTiming(16384, 100, 5, 32768, 4, 96_000_000, FixtureTimingScope.INVOKE_ONLY),
    )


@pytest.mark.parametrize("seconds", [61.0, 300])
def test_supply_only_timing_keeps_the_capture_settle_limit(tmp_path, build, device, seconds):
    device.reply = _invoke_only(tmp_path / "raw")

    _run(
        _timing_only(tmp_path, build),
        tmp_path,
        power=_SUPPLY,
        power_guard=_Authority("station", []),
        settle_seconds=seconds,
    )

    ((request, _),) = device.calls
    assert request.settle_seconds == seconds


@pytest.mark.parametrize("seconds", [301.0, 0, math.nan, None, "30"])
def test_supply_only_timing_refuses_an_unbounded_settle_time(tmp_path, build, device, seconds):
    with pytest.raises(ConfigError, match="settle_seconds must be a number above 0"):
        _run(
            _timing_only(tmp_path, build),
            tmp_path,
            power=_SUPPLY,
            power_guard=_Authority("station", []),
            settle_seconds=seconds,
        )
    assert device.calls == []


def test_the_settle_limits_are_the_capture_s_own():
    from helia_profiler import fixture_capture

    assert (fixture_capture._RAIL_SETTLE_LIMIT_S, fixture_capture._SETTLE_LIMIT_S) == (60, 300)


# Supply-only scope


def test_supply_only_timing_runs_on_ap510b(tmp_path, build, device):
    ap510b = reseal(build, target=fixture_target_for_board("apollo510b_evb"))
    record = compiled(plan_for(ap510b, tmp_path / "model.tflite"), ap510b, Lifecycle.CARRY)
    device.reply = _invoke_only(tmp_path / "raw")

    result, _ = _run(record, tmp_path, power=_SUPPLY, power_guard=_Authority("station", []))

    ((request, _),) = device.calls
    assert request.board_power is _SUPPLY and result.power is _SUPPLY


def test_supply_only_timing_runs_a_single_invocation(tmp_path, build, device):
    single = reseal(build, iterations=1, warmups=0)
    record = compiled(plan_for(single, tmp_path / "model.tflite"), single, Lifecycle.SINGLE)
    device.reply = replace(
        _invoke_only(tmp_path / "raw"),
        timing=FixtureTiming(164, 1, 0, 32768, 4, 96_000_000, FixtureTimingScope.INVOKE_ONLY),
    )

    result, _ = _run(record, tmp_path, power=_SUPPLY, power_guard=_Authority("station", []))

    ((request, _),) = device.calls
    assert request.board_power is _SUPPLY and result.power is _SUPPLY
