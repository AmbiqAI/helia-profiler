"""Pure controls for full-fixture power authority, lifecycle and measured facts."""

from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace

import pytest

from helia_profiler import fixture_power as power
from helia_profiler.fixture import (
    FixtureFile,
    FixtureMethod,
    FixturePowerSync,
    FixtureRailPowerRequest,
    FixtureStatePolicy,
    FixtureTiming,
    FixtureTimingScope,
)
from helia_profiler.power.base import GatedPowerWindow, PowerResult, PowerSummary
from helia_profiler.power.metadata import MeasurementScope, PowerIntegrity, PowerMetadata


def request():
    return FixtureRailPowerRequest("25QG", "MCU", 1.8, 29, 36, 14)


def measured():
    return PowerResult(
        PowerSummary(0.002, 0.004, 0.003, 0.02, 5.0, 5000),
        gated_windows=[GatedPowerWindow(1.0, 6.0, 5.0, 0.01, 0.02, 0.002, 0.004, 0.003, 5000)],
        statistics=[
            {
                "signals": {
                    name: {key: {"value": value} for key in fields}
                    for name, fields, value in (
                        ("current", ("avg", "integral"), 0.002),
                        ("voltage", ("avg",), 2.0),
                        ("power", ("avg", "integral"), 0.004),
                    )
                }
            }
        ],
        gate_samples=[(1, 1), (6, 0)],
        metadata=PowerMetadata(
            device="u/js320/25QG",
            stats_rate_hz=1000,
            integrity=PowerIntegrity.VALID,
            measurement_scope=MeasurementScope.GPIO_GATED_CLEAN_WINDOW,
            gate_rise_observed=True,
            gate_fall_observed=True,
        ),
    )


def test_power_method_is_explicit_and_timing_only_default_is_unchanged():
    assert FixtureMethod(FixtureTimingScope.INVOKE_ONLY).power_sync is None
    wiring = FixturePowerSync(29, 36, 14)
    with pytest.raises(ValueError, match="restore_and_invoke"):
        FixtureMethod(FixtureTimingScope.INVOKE_ONLY, power_sync=wiring)
    method = FixtureMethod(FixtureTimingScope.RESTORE_AND_INVOKE, FixtureStatePolicy.CARRY, wiring)
    assert method.state_policy is FixtureStatePolicy.CARRY


@pytest.mark.parametrize(
    "changes",
    [
        {"serial": ""},
        {"rail": ""},
        {"io_voltage": float("nan")},
        {"stats_rate_hz": True},
        {"sync_gpio_pin": 36},
        {"state_input_index": 0},
    ],
)
def test_invalid_instrument_and_wiring_refuse_without_operations(changes):
    with pytest.raises(ValueError):
        replace(request(), **changes)


@pytest.mark.parametrize("failure", [None, "ownership", "expired", "ready", "drop"])
def test_existing_driver_has_guarded_lifecycle_and_always_releases(monkeypatch, failure):
    events = []
    observation = measured()

    class Guard:
        def verify_power_device(self, **kw):
            events.append("verify")
            assert kw == {"serial": "25QG", "rail": "MCU"}
            if failure == "ownership":
                raise ValueError("ownership")

        def check(self, **kw):
            events.append("check")
            if failure == "expired" and "go" in events:
                raise ValueError("expired")

    class Sync:
        def arm(self):
            events.append("arm")

        def wait_ready(self, **kw):
            events.append("ready")
            return failure != "ready"

        def signal_go(self):
            events.append("go")

        def release_go(self):
            events.append("drop")
            if failure == "drop":
                raise ValueError("drop")

        def release(self):
            events.append("release")

    class Driver:
        def __init__(self, **kw):
            events.append("driver")
            assert "verify" in events and "check" in events

        def make_sync_controller(self, wiring):
            assert wiring.lockstep
            return Sync()

        def capture_gated(self, **kw):
            events.append("observe")
            kw["on_started"]()
            # The maintained driver logs callback exceptions. The wrapper must
            # still refuse the result if an ongoing guard failed.
            try:
                kw["phase_getter"]()
                kw["on_gate_rise"]()
            except ValueError:
                pass
            return observation

    monkeypatch.setattr(power, "JoulescopeDriver", Driver)
    r = SimpleNamespace(
        rail_power=request(), power_sync=request().firmware_wiring(), settle_seconds=30
    )
    if failure:
        with pytest.raises(ValueError):
            power.observe_fixture_power(r, Guard(), lambda: events.append("reset"))
    else:
        assert (
            power.observe_fixture_power(r, Guard(), lambda: events.append("reset")) is observation
        )
        assert (
            events.index("observe")
            < events.index("reset")
            < events.index("ready")
            < events.index("go")
            < events.index("drop")
        )
    if "arm" in events:
        assert events[-1] == "release"
    if failure == "ownership":
        assert events == ["verify"]


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "missing_window",
        "degraded",
        "wrong_device",
        "no_fall",
        "nan",
        "arithmetic",
        "trace_missing",
        "trace_voltage_nan",
    ],
)
def test_integrated_energy_is_measured_and_bound_to_count(tmp_path, fault):
    observation = measured()
    if fault == "trace_missing":
        observation = replace(observation, statistics=[])
    if fault == "trace_voltage_nan":
        observation.statistics[0]["signals"]["voltage"]["avg"]["value"] = float("nan")
    if fault == "missing_window":
        observation = replace(observation, gated_windows=[])
    if fault == "degraded":
        observation.metadata.integrity = PowerIntegrity.DEGRADED
    if fault == "wrong_device":
        observation.metadata.device = "u/js320/other"
    if fault == "no_fall":
        observation.metadata.gate_fall_observed = False
    if fault in ("nan", "arithmetic"):
        observation.gated_windows[0] = replace(
            observation.gated_windows[0], energy_j=float("nan") if fault == "nan" else 7.0
        )
    path = tmp_path / "power.json"
    path.write_bytes(b"raw instrument record")
    raw = FixtureFile(path, sha256(path.read_bytes()).hexdigest())
    timing = FixtureTiming(
        163840, 400, 5, 32768, 300, 96000000, FixtureTimingScope.RESTORE_AND_INVOKE
    )
    if fault:
        with pytest.raises(ValueError):
            power.fixture_rail_energy(observation, request(), timing, raw)
    else:
        result = power.fixture_rail_energy(observation, request(), timing, raw)
        assert result.energy_j == 0.02
        assert result.energy_per_iteration_j == 0.00005
        assert result.iterations == 400 and result.subtraction == "none"
        assert result.raw == raw


def test_fixture_gpio_linkage_uses_the_same_render_context_without_power_operations():
    from helia_profiler.firmware.context import SyncContext

    method = FixtureMethod(
        FixtureTimingScope.RESTORE_AND_INVOKE, power_sync=FixturePowerSync(29, 36, 14)
    )
    ctx = SimpleNamespace(fixture=SimpleNamespace(method=method))
    # No ProfileConfig power driver/rail object exists in this control. Both
    # module selection and project rendering consume this shared context.
    selected = SyncContext.from_pipeline_context(ctx)
    assert selected.power_sync_enabled and selected.lockstep
    assert (selected.sync_gpio_pin, selected.state_gpio_pin, selected.go_gpio_pin) == (29, 36, 14)


def test_numeric_instrument_packets_are_serialized_without_waveform_fabrication():
    import json

    import numpy as np

    from helia_profiler.fixture_capture import _power_json_value

    packet = {"utc": np.array([12, 14], dtype=np.int64), "voltage": np.float32(1.8)}
    saved = json.loads(json.dumps(packet, default=_power_json_value))
    assert saved["utc"] == [12, 14]
    assert saved["voltage"] == pytest.approx(1.8)
    with pytest.raises(TypeError):
        json.dumps({"bad": object()}, default=_power_json_value)
