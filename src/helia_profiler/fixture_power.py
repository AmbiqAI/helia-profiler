"""Full-fixture rail observations using the maintained power driver and sync."""

from __future__ import annotations

import math
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING, Callable, Protocol

from .fixture_runtime import FixtureFile
from .power.base import PowerResult
from .power.joulescope.driver import JoulescopeDriver
from .power.metadata import MeasurementScope, PowerIntegrity, classify_observation
from .power.sync import SyncWiring

if TYPE_CHECKING:
    from .fixture_capture import FixtureCaptureRequest, FixtureTiming

#: The only rail-energy scope a fixture capture measures: the whole rail over
#: the timed restore-and-invoke loop, with nothing subtracted.
GROSS_RAIL_SCOPE = "gross_rail_restore_and_invoke"
NO_SUBTRACTION = "none"


@dataclass(frozen=True)
class FixturePowerSync:
    """Explicit firmware GPIO wiring; constructing this never opens hardware."""

    sync_gpio_pin: int
    state_gpio_pin: int
    go_gpio_pin: int
    protocol_version: int = 2

    def __post_init__(self) -> None:
        pins = (self.sync_gpio_pin, self.state_gpio_pin, self.go_gpio_pin)
        if any(type(p) is not int or not 0 <= p <= 255 for p in pins) or len(set(pins)) != 3:
            raise ValueError("Three distinct GPIO numbers required")
        if type(self.protocol_version) is not int or self.protocol_version not in (1, 2):
            raise ValueError("Unsupported fixture power protocol")


@dataclass(frozen=True)
class FixtureBoardPowerRequest:
    """Explicit station-owned supply path; enabling it does not request energy measurement."""

    driver: str
    serial: str
    rail: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(x, str) or not x.strip() for x in (self.driver, self.serial, self.rail)
        ):
            raise ValueError("Explicit power driver, instrument serial and supplied rail required")


@dataclass(frozen=True)
class FixtureRailPowerRequest:
    """Station-selected instrument/rail and channels, separate from firmware wiring."""

    serial: str
    rail: str
    io_voltage: float
    sync_gpio_pin: int
    state_gpio_pin: int
    go_gpio_pin: int
    sync_input_index: int = 0
    state_input_index: int = 1
    go_output_index: int = 0
    stats_rate_hz: int = 1000

    def __post_init__(self) -> None:
        if any(not isinstance(x, str) or not x.strip() for x in (self.serial, self.rail)):
            raise ValueError("Explicit instrument serial and measured rail required")
        if (
            type(self.io_voltage) not in (int, float)
            or not math.isfinite(self.io_voltage)
            or not 0 < self.io_voltage <= 5.5
        ):
            raise ValueError("Invalid instrument GPIO reference voltage")
        self.firmware_wiring()
        if (
            any(
                type(x) is not int or not 0 <= x <= 3
                for x in (self.sync_input_index, self.state_input_index, self.go_output_index)
            )
            or self.sync_input_index == self.state_input_index
        ):
            raise ValueError("Distinct gate/state inputs and explicit GO output required")
        if type(self.stats_rate_hz) is not int or not 1 <= self.stats_rate_hz <= 100_000:
            raise ValueError("Invalid integrated-statistics rate")

    def firmware_wiring(self) -> FixturePowerSync:
        return FixturePowerSync(self.sync_gpio_pin, self.state_gpio_pin, self.go_gpio_pin)


class FixturePowerGuard(Protocol):
    """Station authority for the board and measured instrument/rail."""

    def check(self, *, require_free: bool, remaining_s: float) -> None: ...

    def verify_power_device(self, *, serial: str, rail: str) -> None: ...


@dataclass(frozen=True)
class FixtureBatchCompletion:
    """Observed protocol-v2 transitions on the instrument's native sample counter."""

    protocol_version: int
    counter_domain: str
    sample_rate: int
    ready_sample: int
    go_ack_sample: int
    gate_rise_sample: int
    gate_fall_sample: int
    done_sample: int
    raw: FixtureFile


@dataclass(frozen=True)
class FixtureRailEnergy:
    iterations: int
    scope: str
    rail: str
    instrument_serial: str
    subtraction: str
    duration_s: float
    mean_power_w: float
    mean_current_a: float
    energy_j: float
    energy_per_iteration_j: float
    raw: FixtureFile
    completion: FixtureBatchCompletion | None = None


def observe_fixture_power(
    request: FixtureCaptureRequest,
    guard: FixturePowerGuard,
    reset: Callable[[], None],
) -> PowerResult:
    """Observe one detached window; never infer energy from target timing."""
    power = request.rail_power
    if power is None or request.power_sync != power.firmware_wiring():
        raise ValueError("Power request differs from compiled firmware wiring")
    guard.verify_power_device(serial=power.serial, rail=power.rail)
    guard.check(require_free=True, remaining_s=request.settle_seconds + 120)
    driver = JoulescopeDriver(serial=power.serial)
    sync = driver.make_sync_controller(
        SyncWiring(True, power.sync_input_index, power.state_input_index, power.go_output_index)
    )
    phase = "arming"
    phase_lock = RLock()
    failures: list[Exception] = []

    def check() -> None:
        with phase_lock:
            require_free = phase != "resetting"
            try:
                guard.check(require_free=require_free, remaining_s=90)
            except Exception as exc:
                failure = ValueError(
                    f"Power guard refused in {phase} (require_free={require_free}): {exc}"
                )
                failures.append(failure)
                raise failure from exc

    def started(wait_gpi) -> None:
        nonlocal phase
        # Serialize the free-probe check with entry into our owned reset;
        # the poller still checks authority while the reset child holds it.
        with phase_lock:
            check()
            phase = "resetting"
        try:
            reset()
        finally:
            with phase_lock:
                phase = "arming"
                check()
        if not wait_gpi(power.state_input_index, True, request.settle_seconds):
            if failures:
                raise failures[0]
            raise ValueError("Power firmware did not signal READY")
        check()
        phase = "go_signaled"
        sync.signal_go()

    def gate_rise() -> None:
        # Drop GO even if the caller's ongoing authority has expired.
        try:
            sync.release_go()
            check()
        except Exception as exc:
            failures.append(exc)
            raise

    def current_phase() -> str:
        check()
        return phase

    try:
        check()
        sync.arm()
        result = driver.capture_gated(
            duration_s=request.settle_seconds,
            io_voltage=power.io_voltage,
            sync_input_index=power.sync_input_index,
            state_input_index=power.state_input_index,
            stats_rate_hz=power.stats_rate_hz,
            on_started=started,
            on_gate_rise=gate_rise,
            phase_getter=current_phase,
            lockstep=True,
            lockstep_wiring_available=True,
            require_done_state=True,
        )
        if failures:
            raise failures[0]
        check()
        mode, integrity, rise, fall, bound = classify_observation(result.metadata)
        result.metadata.set_observation(
            observation_mode=mode,
            integrity=integrity,
            gate_rise_observed=rise,
            gate_fall_observed=fall,
            observation_deadline_s=bound or request.settle_seconds,
        )
        return result
    finally:
        sync.release()


def fixture_rail_energy(
    observation: PowerResult,
    request: FixtureRailPowerRequest,
    timing: FixtureTiming,
    raw: FixtureFile,
) -> FixtureRailEnergy:
    """Bind a complete instrument window to the successful target count."""
    meta = observation.metadata
    if (
        not observation.statistics
        or not observation.gate_samples
        or len(observation.gated_windows) != 1
        or meta.integrity != PowerIntegrity.VALID
        or meta.negative_current_diagnostic
        or observation.summary.negative_current_diagnostic
        or meta.measurement_scope != MeasurementScope.GPIO_GATED_CLEAN_WINDOW
        or meta.gate_rise_observed is not True
        or meta.gate_fall_observed is not True
        or meta.device is None
        or meta.device.rsplit("/", 1)[-1] != request.serial
        or meta.stats_rate_hz != request.stats_rate_hz
        or timing.iterations <= 0
        or timing.timing_scope.value != "restore_and_invoke"
    ):
        raise ValueError("Incomplete or mismatched gated rail observation")
    for packet in observation.statistics:
        for signal, fields in (
            ("current", ("avg", "integral")),
            ("voltage", ("avg",)),
            ("power", ("avg", "integral")),
        ):
            for field in fields:
                value = packet.get("signals", {}).get(signal, {}).get(field, {}).get("value")
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError("Rail trace lacks finite measured voltage/current/power")
    window = observation.gated_windows[0]
    values = (window.duration_s, window.energy_j, window.avg_power_w, window.avg_current_a)
    if (
        any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in values)
        or window.sample_count <= 0
    ):
        raise ValueError("Invalid measured rail integral")
    if not math.isclose(
        window.avg_power_w * window.duration_s, window.energy_j, rel_tol=1e-9, abs_tol=1e-12
    ):
        raise ValueError("Rail energy/power/duration disagree")
    _validate_trace_coverage(observation)
    completion = _batch_completion(observation, request, raw)
    raw.read()
    return FixtureRailEnergy(
        timing.iterations,
        GROSS_RAIL_SCOPE,
        request.rail,
        request.serial,
        NO_SUBTRACTION,
        window.duration_s,
        window.avg_power_w,
        window.avg_current_a,
        window.energy_j,
        window.energy_j / timing.iterations,
        raw,
        completion,
    )


def _batch_completion(
    observation: PowerResult, request: FixtureRailPowerRequest, raw: FixtureFile
) -> FixtureBatchCompletion:
    from .power.completion import resolve_counter_completion
    from .power.counter_gate import counter_gate_trace

    if observation.state_stream_error:
        raise ValueError("Invalid completion state stream")
    gate = counter_gate_trace(
        observation.gate_frames, observation.statistics, channel=request.sync_input_index
    )

    state = counter_gate_trace(
        observation.state_frames, observation.statistics, channel=request.state_input_index
    )
    completed = resolve_counter_completion(gate, state)
    rise, fall = completed.rise, completed.fall
    selected = observation.counter_gate_evidence
    if len(selected) != 1 or (selected[0]["rise"], selected[0]["fall"]) != (rise, fall):
        raise ValueError("Energy interval differs from completed batch gate")
    return FixtureBatchCompletion(
        2,
        "joulescope_sensor_sample_id",
        gate.rate,
        completed.ready,
        completed.acknowledge,
        rise,
        fall,
        completed.done,
        raw,
    )


def _validate_trace_coverage(observation: PowerResult) -> None:
    """Reconstruct the retained gate and verify the exact selected packet integral."""
    from .power.counter_gate import counter_gate_trace

    if observation.gate_stream_error or observation.counter_domain != "joulescope_sensor_sample_id":
        raise ValueError("Missing or invalid shared-counter gate evidence")
    if len(observation.counter_gate_evidence) != 1 or not observation.statistics_device_time:
        raise ValueError("Missing selected counter gate")
    channel = observation.metadata.sync_input_index
    if type(channel) is not int:
        raise ValueError("Missing gate channel")
    try:
        trace = counter_gate_trace(observation.gate_frames, observation.statistics, channel=channel)
        saved = observation.counter_gate_evidence[0]
        evidence = trace.window_evidence(saved["rise"], saved["fall"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Invalid retained counter coverage: {exc}") from exc
    if saved != evidence:
        raise ValueError("Retained gate selection/uncertainty mismatch")
    window = observation.gated_windows[0]
    if window.sample_count != evidence["packet_count"]:
        raise ValueError("Trace/window packet count mismatch")
    selected = observation.statistics[evidence["first_packet"] : evidence["last_packet"] + 1]
    energy = sum(abs(p["signals"]["power"]["integral"]["value"]) for p in selected)
    charge = sum(abs(p["signals"]["current"]["integral"]["value"]) for p in selected)
    origin_mid = sum(trace.intervals[0]) / 2
    facts = (
        (window.duration_s, evidence["integrated_duration_s"]),
        (window.energy_j, energy),
        (window.charge_c, charge),
        (window.start_s, (saved["rise"] - origin_mid) / trace.rate),
        (window.end_s, (saved["fall"] - origin_mid) / trace.rate),
    )
    if any(
        not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-12)
        for actual, expected in facts
    ):
        raise ValueError("Trace/window integral, duration or gate identity mismatch")
