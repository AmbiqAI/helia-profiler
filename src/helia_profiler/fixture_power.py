"""Full-fixture rail observations using the maintained power driver and sync."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Protocol

from .fixture_runtime import FixtureFile
from .power.base import PowerResult
from .power.joulescope.driver import JoulescopeDriver
from .power.metadata import MeasurementScope, PowerIntegrity, classify_observation
from .power.sync import SyncWiring

if TYPE_CHECKING:
    from .fixture_capture import FixtureCaptureRequest, FixtureTiming


@dataclass(frozen=True)
class FixturePowerSync:
    """Explicit firmware GPIO wiring; constructing this never opens hardware."""

    sync_gpio_pin: int
    state_gpio_pin: int
    go_gpio_pin: int

    def __post_init__(self) -> None:
        pins = (self.sync_gpio_pin, self.state_gpio_pin, self.go_gpio_pin)
        if any(type(p) is not int or not 0 <= p <= 255 for p in pins) or len(set(pins)) != 3:
            raise ValueError("Three distinct GPIO numbers required")


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
    failures: list[Exception] = []

    def check() -> None:
        try:
            guard.check(require_free=True, remaining_s=90)
        except Exception as exc:
            failures.append(exc)
            raise

    def started(wait_gpi) -> None:
        nonlocal phase
        check()
        reset()
        if not wait_gpi(power.state_input_index, True, min(15.0, request.settle_seconds)):
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
    raw.read()
    return FixtureRailEnergy(
        timing.iterations,
        "gross_rail_restore_and_invoke",
        request.rail,
        request.serial,
        "none",
        window.duration_s,
        window.avg_power_w,
        window.avg_current_a,
        window.energy_j,
        window.energy_j / timing.iterations,
        raw,
    )


def _validate_trace_coverage(observation: PowerResult) -> None:
    """Require complete counter-contiguous statistics spanning the measured gate.

    Integration remains driver-owned. This verifies retained evidence without
    replacing the driver's charge/energy calculation or inventing timestamps.
    """
    hz = observation.statistics_timebase_hz
    if type(hz) is not int or hz <= 0:
        raise ValueError("Missing instrument timestamp scale")
    previous_end = rate = previous_u0 = previous_u1 = previous_axis = None
    rows = []
    utc_intervals = []
    for packet in observation.statistics:
        try:
            t = packet["time"]
            first, last = t["samples"]["value"]
            frequency = t["sample_freq"]["value"]
            u0, u1 = t["utc"]["value"]
            if not all(math.isfinite(v) for v in (first, last, frequency, u0, u1)):
                raise ValueError
            if last <= first or frequency <= 0 or u1 <= u0:
                raise ValueError
            if previous_end is not None and (first != previous_end or frequency != rate):
                raise ValueError
            previous_end, rate = last, frequency
            axis = (u0 + u1) / 2 if observation.statistics_device_time else packet["_host_time64"]
            if not math.isfinite(axis) or (
                previous_axis is not None
                and (axis <= previous_axis or u0 <= previous_u0 or u1 <= previous_u1)
            ):
                raise ValueError
            previous_axis, previous_u0, previous_u1 = axis, u0, u1
            duration = (last - first) / frequency
            rows.append((axis, duration))
            utc_intervals.append((u0, u1))
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Incomplete or discontinuous instrument statistics") from exc
    origin = min(axis for axis, _ in rows)
    window = observation.gated_windows[0]
    selected = [
        (axis, duration)
        for axis, duration in rows
        if window.start_s <= (axis - origin) / hz <= window.end_s
    ]
    if len(selected) != window.sample_count:
        raise ValueError("Trace/window packet count mismatch")
    total = sum(duration for _, duration in selected)
    if not math.isclose(total, window.duration_s, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError("Trace/window duration mismatch")
    if not observation.statistics_device_time:
        raise ValueError("Timestamp coverage unavailable on the host-arrival axis")
    # Counter continuity cannot prove UTC coverage when the fitted time map
    # changes. Require the actual interval union to cover the gate interior;
    # overlap cannot compensate for an unsupported gap. Do not invent a clock
    # tolerance. A changing map that leaves a gap is explicitly unavailable.
    gate_start = origin + window.start_s * hz
    gate_end = origin + window.end_s * hz
    intersecting = [(a, b) for a, b in utc_intervals if b > gate_start and a < gate_end]
    if not intersecting:
        raise ValueError("Timestamp coverage unavailable for the measured gate")
    covered_end = intersecting[0][1]
    for start, end in intersecting[1:]:
        if start > covered_end:
            raise ValueError("Timestamp coverage unavailable: interior UTC gap")
        covered_end = max(covered_end, end)
    # Only the intersecting edge packets bound midpoint uncertainty. An
    # unrelated long packet outside the gate must not license missing coverage.
    first_start, first_end = intersecting[0]
    last_start, last_end = intersecting[-1]
    if first_start - gate_start > first_end - first_start or (
        gate_end - covered_end > last_end - last_start
    ):
        raise ValueError("Trace does not cover the measured gate endpoints")
