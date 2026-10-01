"""Normalize existing fixture observations without numerical-policy decisions."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from ._fixture_build import FixtureBuild
from .fixture_capture import FixtureCaptureResult
from .fixture_metrics import FixtureFootprint, FixtureMetric
from .fixture_runtime import FixtureFile
from .power.base import PowerResult
from .power.metadata import MeasurementScope, PowerIntegrity


@dataclass(frozen=True)
class FixtureEnergyWindow:
    """Producer-verified gate/count association for an existing power result.

    Construct only after completed firmware terminal/image validation. This
    record does not acquire a device or replace the power driver/sync protocol.
    """

    build_identity: str
    power: PowerResult
    completed_calls: int
    firmware_duration_s: float
    measured_domain: str
    instrument_serial: str
    evidence: tuple[FixtureFile, ...]


@dataclass(frozen=True)
class FixtureMeasurements:
    schema_version: int
    build_identity: str
    latency_s: FixtureMetric
    arena_capacity_bytes: FixtureMetric
    arena_used_after_io_bytes: FixtureMetric
    arena_used_after_warmup_bytes: FixtureMetric
    arena_used_after_invoke_bytes: FixtureMetric
    footprint: FixtureFootprint
    energy_j: FixtureMetric
    energy_per_inference_j: FixtureMetric
    energy_domain: str | None
    instrument_serial: str | None
    idle_subtracted: bool
    evidence: tuple[FixtureFile, ...]


def _energy_metrics(
    energy: FixtureEnergyWindow | None,
    build: FixtureBuild,
) -> tuple[FixtureMetric, FixtureMetric]:
    if energy is None:
        return (
            FixtureMetric(None, "J", "whole gated electrical domain", "not_captured"),
            FixtureMetric(None, "J/inference", "whole gated electrical domain", "not_captured"),
        )
    if (
        energy.build_identity != build.build_identity
        or type(energy.completed_calls) is not int
        or energy.completed_calls != build.iterations
        or not math.isfinite(energy.firmware_duration_s)
        or energy.firmware_duration_s <= 0
        or not energy.measured_domain.strip()
        or not energy.instrument_serial.strip()
        or not energy.evidence
    ):
        raise ValueError(
            "Energy lacks matching image, completed count, duration or electrical evidence"
        )
    for ref in energy.evidence:
        ref.read()
    power = energy.power
    metadata = power.metadata
    if (
        metadata.measurement_scope != MeasurementScope.GPIO_GATED_CLEAN_WINDOW
        or metadata.integrity != PowerIntegrity.VALID
        or metadata.gate_failure is not None
        or metadata.gating_method
        not in ("gpi_stream+host_stats_integral", "gpi_snapshot_poll+host_stats_integral")
        or metadata.window_count != 1
        or len(power.gated_windows) != 1
        or metadata.gate_rise_observed is not True
        or metadata.gate_fall_observed is not True
        or metadata.short_gate_pulses_ignored not in (None, 0)
    ):
        raise ValueError("Energy requires one valid completed GPIO gate")
    uncertainty = 0.0
    if metadata.gating_method == "gpi_snapshot_poll+host_stats_integral":
        raw_uncertainty = (metadata.gating_diagnostics or {}).get("poll_edge_uncertainty_s")
        if not isinstance(raw_uncertainty, (int, float)) or isinstance(raw_uncertainty, bool):
            raise ValueError("Unbounded polling edge uncertainty")
        uncertainty = float(raw_uncertainty)
        if (
            type(uncertainty) not in (int, float)
            or not math.isfinite(uncertainty)
            or not 0 <= uncertainty <= 0.01 * energy.firmware_duration_s
        ):
            raise ValueError("Unbounded polling edge uncertainty")
    window = power.gated_windows[0]
    if (
        not all(
            math.isfinite(v) and v > 0
            for v in (window.energy_j, window.duration_s, power.summary.energy_j)
        )
        or window.sample_count <= 0
        or power.summary.sample_count <= 0
        or not math.isclose(window.energy_j, power.summary.energy_j, rel_tol=1e-12)
        or not math.isclose(window.duration_s, power.summary.duration_s, rel_tol=1e-12)
        or abs(window.duration_s - energy.firmware_duration_s)
        > 0.01 * energy.firmware_duration_s + 0.002 + uncertainty
    ):
        raise ValueError("Energy integral/count or firmware window mismatch")
    basis = "completed GPIO gate; whole declared electrical domain; no idle subtraction"
    return (
        FixtureMetric(window.energy_j, "J", basis),
        FixtureMetric(window.energy_j / energy.completed_calls, "J/inference", basis),
    )


def summarize_fixture_measurements(
    build: FixtureBuild,
    capture: FixtureCaptureResult,
    footprint: FixtureFootprint,
    *,
    energy: FixtureEnergyWindow | None = None,
) -> FixtureMeasurements:
    """Bind complete raw observations to a compiled receipt; no numerical acceptance."""
    if (
        not build.built
        or build.build_identity is None
        or build.binary is None
        or build.flat_binary is None
    ):
        raise ValueError("Compiled build identity required")
    if (footprint.elf.sha256, footprint.image.sha256) != (
        build.binary.sha256,
        build.flat_binary.sha256,
    ):
        raise ValueError("Footprint differs from compiled image")
    if build.link_map is None or footprint.link_map.sha256 != build.link_map.sha256:
        raise ValueError("Footprint map differs from compiled image")
    for ref in (footprint.elf, footprint.image, footprint.link_map):
        ref.read()
    identities = [ref for ref in capture.artifacts if ref.path.name == "identity.json"]
    if len(identities) != 1:
        raise ValueError("Exactly one pinned capture identity required")
    for ref in capture.artifacts:
        ref.read()
    identity = json.loads(identities[0].read())
    if (identity.get("elf_sha256"), identity.get("image_sha256")) != (
        build.binary.sha256,
        build.flat_binary.sha256,
    ):
        raise ValueError("Capture differs from compiled image")
    timing = capture.timing
    if (
        capture.state != "success"
        or capture.status != 0
        or capture.output is None
        or timing is None
        or timing.iterations != build.iterations
        or timing.warmups != build.warmups
        or timing.timing_scope != build.timing_scope
        or capture.timing_scope != build.timing_scope
        or timing.ticks <= 0
        or timing.timer_hz <= 0
        or timing.iterations <= 0
    ):
        raise ValueError("Incomplete capture or mismatched method")
    build.binary.read()
    build.flat_binary.read()
    capture.output.read()
    memory = capture.memory
    if memory is None:
        capacity = after_io = warmup = invoke = FixtureMetric(
            None, "B", "allocator terminal", "not_observed"
        )
    else:
        if not all(
            0 < v <= memory.capacity
            for v in (memory.after_io_access, memory.after_warmup, memory.after_invoke)
        ):
            raise ValueError("Invalid allocator snapshots")
        capacity = FixtureMetric(memory.capacity, "B", "firmware arena capacity; not consumption")
        after_io, warmup, invoke = tuple(
            FixtureMetric(v, "B", "current allocator snapshot; not transient peak")
            for v in (memory.after_io_access, memory.after_warmup, memory.after_invoke)
        )
    joules, per_call = _energy_metrics(energy, build)
    return FixtureMeasurements(
        1,
        build.build_identity,
        FixtureMetric(
            timing.ticks / timing.timer_hz / timing.iterations,
            "s/inference",
            str(build.timing_scope),
        ),
        capacity,
        after_io,
        warmup,
        invoke,
        footprint,
        joules,
        per_call,
        energy.measured_domain if energy else None,
        energy.instrument_serial if energy else None,
        False,
        capture.artifacts + (energy.evidence if energy else ()),
    )
