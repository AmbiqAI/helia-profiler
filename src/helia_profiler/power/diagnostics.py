"""Power-capture diagnostic helpers.

Keep host/device sync metadata and failure classification in one small module so
capture wrappers and instrument drivers do not each invent their own diagnostic
shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .sync import DeviceState


class GateFailureKind(StrEnum):
    """Classified gated-capture transition failure."""

    NO_GATE_RISE = "no_gate_rise"
    NO_GATE_FALL = "no_gate_fall"
    NO_STATS_WINDOW = "no_stats_window"


@dataclass(frozen=True)
class SyncHandshakeMetadata:
    """Host-observed lockstep handshake metadata."""

    lockstep: bool
    ready_wait_s: float | None = None
    ready_observed: bool | None = None
    last_state: DeviceState | None = None

    def to_metadata(self) -> dict[str, object]:
        metadata: dict[str, object] = {"lockstep": self.lockstep}
        if self.ready_wait_s is not None:
            metadata["ready_wait_s"] = self.ready_wait_s
        if self.ready_observed is not None:
            metadata["ready_observed"] = self.ready_observed
        if self.last_state is not None:
            metadata["last_state"] = self.last_state.value
        return metadata


@dataclass(frozen=True)
class GateTransitionTiming:
    """Host-observed GPI gate transition timings."""

    capture_to_gate_rise_s: float | None = None
    capture_to_gate_fall_s: float | None = None
    go_release_to_gate_rise_s: float | None = None

    def to_metadata(self) -> dict[str, float]:
        metadata: dict[str, float] = {}
        if self.capture_to_gate_rise_s is not None:
            metadata["capture_to_gate_rise_s"] = self.capture_to_gate_rise_s
        if self.capture_to_gate_fall_s is not None:
            metadata["capture_to_gate_fall_s"] = self.capture_to_gate_fall_s
        if self.go_release_to_gate_rise_s is not None:
            metadata["go_release_to_gate_rise_s"] = self.go_release_to_gate_rise_s
        return metadata


@dataclass(frozen=True)
class GateFailure:
    """Classified gated-capture failure with user-facing text."""

    kind: GateFailureKind
    message: str
    hint: str

    def to_metadata(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "message": self.message,
            "hint": self.hint,
        }


@dataclass(frozen=True)
class GateDurationIntegrity:
    """Agreement between a measured gate and the expected inference window."""

    measured_s: float
    expected_s: float
    tolerance_s: float
    minimum_s: float = 0.0

    @property
    def valid(self) -> bool:
        return (
            self.measured_s >= self.minimum_s
            and abs(self.measured_s - self.expected_s) <= self.tolerance_s
        )

    @property
    def ratio(self) -> float:
        return self.measured_s / self.expected_s if self.expected_s > 0 else 0.0


def assess_gate_duration(
    *,
    measured_s: float,
    clean_infer_count: int,
    clean_infer_avg_us: int,
    stats_rate_hz: int,
    minimum_s: float = 0.0,
    relative_tolerance: float = 0.01,
) -> GateDurationIntegrity:
    """Compare a gate against ``N * inference_time`` with instrument jitter allowance."""
    expected_s = clean_infer_count * clean_infer_avg_us / 1_000_000.0
    inference_slack_s = clean_infer_avg_us / 2_000_000.0
    packet_slack_s = 2.0 / max(1, stats_rate_hz)
    cross_binary_slack_s = expected_s * relative_tolerance
    return GateDurationIntegrity(
        measured_s=measured_s,
        expected_s=expected_s,
        tolerance_s=max(inference_slack_s, packet_slack_s, cross_binary_slack_s),
        minimum_s=minimum_s,
    )


def classify_gate_failure(
    *, saw_gate_rise: bool, saw_gate_fall: bool = False, duration_s: float
) -> GateFailure:
    """Classify why a gated capture produced no complete high window."""
    if not saw_gate_rise:
        return GateFailure(
            kind=GateFailureKind.NO_GATE_RISE,
            message="No GPIO gate rising edge detected during Joulescope gated capture",
            hint=(
                "Check GO/state/gate wiring, confirm the firmware reached the power "
                "window wait state, and verify the selected reset strategy relaunches "
                "the firmware before capture."
            ),
        )
    if saw_gate_fall:
        return GateFailure(
            kind=GateFailureKind.NO_STATS_WINDOW,
            message="GPIO gate edges were observed but no Joulescope stats window was selected",
            hint=(
                "The device completed its gated window, but host GPIO timestamps did not "
                "overlap the instrument stats timeline. Retain the diagnostic artifact and "
                "check Joulescope callback timing before trusting power data."
            ),
        )
    return GateFailure(
        kind=GateFailureKind.NO_GATE_FALL,
        message="GPIO gate rose but did not fall during Joulescope gated capture",
        hint=(
            "The firmware entered the measured window but did not close it before "
            f"the {duration_s:.1f}s safety bound. Increase power.duration_s or "
            "check for firmware hangs inside the clean window."
        ),
    )


__all__ = [
    "GateDurationIntegrity",
    "GateFailure",
    "GateFailureKind",
    "GateTransitionTiming",
    "SyncHandshakeMetadata",
    "assess_gate_duration",
    "classify_gate_failure",
]
