"""Immutable artifacts exchanged between major pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..power.base import PowerResult
from .models import BinarySections, PmuResult


@dataclass(frozen=True)
class FirmwareArtifact:
    """One built firmware target with explicit role and provenance."""

    role: Literal["profile", "power"]
    target_name: str
    app_dir: Path
    build_dir: Path
    binary_path: Path
    binary_sections: BinarySections | None = None


@dataclass(frozen=True)
class PowerRunPlan:
    """Resolved inputs for building and capturing one power run.

    ``inference_count`` is deliberately optional. A standalone power workflow
    can leave it unset and use firmware heuristics; a chained profile workflow
    can supply an authoritative host-selected count later.
    """

    firmware_mode: Literal["dedicated", "shared"]
    inference_count: int | None = None
    reference_inference_us: int | None = None
    target_duration_ms: int | None = None
    count_source: Literal["firmware_auto", "configured", "profile_guided"] = (
        "firmware_auto"
    )


@dataclass(frozen=True)
class DeploymentRecord:
    """One explicit deployment of a built firmware artifact."""

    firmware: FirmwareArtifact
    target_id: str
    deployed_at: str


@dataclass(frozen=True)
class ProfileRun:
    """Immutable state of the profile-firmware workflow."""

    firmware: FirmwareArtifact
    deployment: DeploymentRecord | None = None
    result: PmuResult | None = None


@dataclass(frozen=True)
class PowerObservation:
    """Host instrument observation, independent of firmware terminal status."""

    mode: Literal["gpio_gated", "free_form"]
    result: PowerResult
    gate_rise_observed: bool
    gate_fall_observed: bool
    deadline_s: float
    integrity: Literal["valid", "degraded", "invalid"]


@dataclass(frozen=True)
class PowerTerminalRecord:
    """Versioned firmware status emitted only after the power gate is low."""

    version: int
    status: Literal["ok", "error"]
    requested_count: int
    completed_count: int
    elapsed_us: int | None
    final_phase: str
    error_code: int
    gate_asserted: bool
    gate_lowered: bool

    def __post_init__(self) -> None:
        if self.version != 1:
            raise ValueError(f"Unsupported power terminal version: {self.version}.")
        if self.requested_count < 0 or self.completed_count < 0:
            raise ValueError("Power terminal counts must be non-negative.")
        if self.completed_count > self.requested_count:
            raise ValueError("Completed count exceeds requested count.")
        if self.elapsed_us is not None and self.elapsed_us < 0:
            raise ValueError("Power terminal elapsed time must be non-negative.")
        if not self.final_phase:
            raise ValueError("Power terminal final phase must not be empty.")
        if self.status == "ok" and self.error_code != 0:
            raise ValueError("Successful power terminal status requires error code 0.")
        if self.status == "error" and self.error_code <= 0:
            raise ValueError("Error power terminal status requires a positive error code.")


@dataclass(frozen=True)
class OnDevicePowerSummary:
    """Integer-unit aggregate reported by a firmware-side power monitor."""

    source: str
    scope: Literal["fixed_n_inference"]
    energy_nj: int
    duration_us: int
    inference_count: int
    overflow: bool
    charge_nc: int | None = None
    bus_voltage_uv: int | None = None
    sample_count: int | None = None
    calibration_id: str | None = None

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("On-device power source must not be empty.")
        if self.scope != "fixed_n_inference":
            raise ValueError(f"Unsupported on-device power scope: {self.scope!r}.")
        values = (
            self.energy_nj,
            self.duration_us,
            self.inference_count,
            self.charge_nc,
            self.bus_voltage_uv,
            self.sample_count,
        )
        if any(value is not None and value < 0 for value in values):
            raise ValueError("On-device power values must be non-negative.")
        if self.inference_count > 0 and self.duration_us == 0:
            raise ValueError("On-device power duration must be positive for completed work.")


@dataclass(frozen=True)
class PowerTerminalEnvelope:
    """Internal transport-neutral payload from dedicated power firmware.

    Public callers receive its components through :class:`ProfileResult`.
    """

    terminal: PowerTerminalRecord
    measurement: OnDevicePowerSummary | None = None


@dataclass(frozen=True)
class PowerRun:
    """Internal immutable state of the dedicated/shared power workflow."""

    plan: PowerRunPlan
    firmware: FirmwareArtifact | None = None
    deployment: DeploymentRecord | None = None
    observation: PowerObservation | None = None
    terminal: PowerTerminalRecord | None = None
    on_device_summary: OnDevicePowerSummary | None = None
