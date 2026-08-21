"""Typed model behind ``PowerResult.metadata`` (#154 Phase 2).

``PowerMetadata`` replaces the ``dict[str, Any]`` bag that six writer sites
used to fill by string key. It follows the ``RunMetadata`` precedent: a plain
**mutable** dataclass whose optional fields are enriched across pipeline
stages (capture → publication → terminal collection), holding the existing
frozen diagnostics dataclasses as objects rather than their flattened dicts.

Serialization happens once, in :meth:`PowerMetadata.to_metadata_dict`, which
reproduces the byte-exact key/value surface of the old dict (the report
golden digests are the referee). Fields default to ``None`` = "never set";
a boolean ``False`` is a real recorded value and is emitted. The fields
marked *artifact-only* are written by capture and read by nothing in
``src/`` except the report passthrough — they exist for the artifact record
and are documented here so that distinction is visible in one place
(previously nothing separated load-bearing keys from write-only ones).

The full key inventory this model must cover is pinned by
``tests/contracts/snapshots/power_metadata_census.json``.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from .diagnostics import (
    GateDurationIntegrity,
    GateFailure,
    GateTransitionTiming,
    SyncHandshakeMetadata,
    WindowClockCeiling,
)

if TYPE_CHECKING:
    from ..target.lifecycle import TargetLifecyclePlan


class MeasurementScope(StrEnum):
    """What the published power numbers actually measured."""

    #: Host instrument integrated exactly the GPIO-high clean window.
    GPIO_GATED_CLEAN_WINDOW = "gpio_gated_clean_window"
    #: Gating failed; numbers cover the whole free-running capture.
    FREE_FORM_CAPTURE = "free_form_capture"
    #: On-device monitor integrated the firmware-gated inference window.
    ON_DEVICE_GATED_INFERENCE = "on_device_gated_inference"
    #: Reader-side default for legacy artifacts that predate the key.
    WHOLE_CAPTURE_WINDOW = "whole_capture_window"


class ObservationMode(StrEnum):
    """How the observation was made (previously a three-vs-two vocabulary
    skew: ``PowerObservation.mode`` was a two-value ``Literal`` while the
    internal-mode path wrote ``on_device`` — this enum is now the single
    vocabulary)."""

    GPIO_GATED = "gpio_gated"
    FREE_FORM = "free_form"
    ON_DEVICE = "on_device"


class PowerIntegrity(StrEnum):
    """Whether the observation is valid for efficiency metrics."""

    VALID = "valid"
    DEGRADED = "degraded"
    INVALID = "invalid"


#: Fields whose values are typed objects flattened via their own
#: ``to_metadata()`` at serialization time.
_TO_METADATA_FIELDS = frozenset(
    {
        "sync",
        "sync_timing_s",
        "gate_failure",
        "gate_duration_integrity",
        "window_clock_ceiling",
        "target_lifecycle",
    }
)


@dataclass
class PowerMetadata:
    """Everything the capture layer tells the rest of HPX about one power run.

    Mutable by design — enriched progressively, like ``RunMetadata``. The
    serialized view (:meth:`to_metadata_dict`) emits every non-``None`` field
    under its historical key name, flattening the typed diagnostics through
    their ``to_metadata()`` methods.
    """

    # -- Capture provenance (set by the driver) ----------------------------
    driver: str | None = None
    device: str | None = None
    io_voltage: float | None = None
    gating_method: str | None = None
    sync_input_index: int | None = None
    stats_rate_hz: int | None = None
    stats_scnt: int | None = None

    # -- Window accounting (set by the driver / internal-mode synthesis) ---
    window_count: int | None = None
    gpi_poll_count: int | None = None
    stat_packets: int | None = None
    early_stopped: bool | None = None
    capture_window_s: float | None = None
    capture_safety_bound_s: float | None = None
    short_gate_pulses_ignored: int | None = None
    clean_infer_count: int | None = None
    inference_count: int | None = None
    source: str | None = None

    # -- Observation classification (set by capture / pipeline publishers) -
    #: ``measurement_scope`` is an extension point: registered third-party
    #: drivers may report scopes HPX does not know (a custom gating scheme).
    #: Known values coerce to the enum in ``__post_init__``; unknown strings
    #: are kept verbatim and classify as not-gated, exactly as before.
    #: ``observation_mode`` and ``integrity`` are HPX-owned closed
    #: vocabularies and coerce strictly.
    measurement_scope: MeasurementScope | str | None = None
    observation_mode: ObservationMode | None = None
    integrity: PowerIntegrity | None = None
    gate_rise_observed: bool | None = None
    gate_fall_observed: bool | None = None
    observation_deadline_s: float | None = None

    # -- Orchestration records (set by capture/__init__.py) ----------------
    power_firmware: str | None = None
    power_plan: dict[str, Any] | None = None

    # -- Typed diagnostics — the objects, not their dicts ------------------
    sync: SyncHandshakeMetadata | None = None
    #: Assigned only when at least one transition was timed (the old writer
    #: skipped the key when ``to_metadata()`` came back empty).
    sync_timing_s: GateTransitionTiming | None = None
    gate_failure: GateFailure | None = None
    gate_duration_integrity: GateDurationIntegrity | None = None
    window_clock_ceiling: WindowClockCeiling | None = None
    target_lifecycle: "TargetLifecyclePlan | None" = None

    # -- Artifact-only diagnostics: written by capture, read by nothing in
    # -- src/ except the report passthrough. Kept for wire stability.
    short_gate_pulse_diagnostics: dict[str, Any] | None = None
    whole_capture_summary: dict[str, Any] | None = None
    fullrate_xcheck: dict[str, Any] | None = None
    gating_diagnostics: dict[str, Any] | None = None
    gated_vs_whole_current_ok: bool | None = None

    def __post_init__(self) -> None:
        if isinstance(self.measurement_scope, str) and not isinstance(
            self.measurement_scope, MeasurementScope
        ):
            try:
                self.measurement_scope = MeasurementScope(self.measurement_scope)
            except ValueError:
                pass  # third-party driver scope — kept verbatim
        if isinstance(self.observation_mode, str) and not isinstance(
            self.observation_mode, ObservationMode
        ):
            self.observation_mode = ObservationMode(self.observation_mode)
        if isinstance(self.integrity, str) and not isinstance(self.integrity, PowerIntegrity):
            self.integrity = PowerIntegrity(self.integrity)

    def to_metadata_dict(self) -> dict[str, Any]:
        """Flat dict view, byte-compatible with the pre-#154 metadata bag.

        Emits every non-``None`` field under its historical key; typed
        diagnostics flatten through their own ``to_metadata()``. ``False``
        is a recorded value and is emitted; ``None`` means "never set" and
        is omitted (matching the old conditional writes).
        """
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if value is None:
                continue
            if f.name in _TO_METADATA_FIELDS:
                out[f.name] = value.to_metadata()
            else:
                out[f.name] = value
        return out

    def set_observation(
        self,
        *,
        observation_mode: ObservationMode,
        integrity: PowerIntegrity | str,
        gate_rise_observed: bool,
        gate_fall_observed: bool,
        observation_deadline_s: float,
    ) -> None:
        """Publication-time enrichment (the old ``metadata.update`` block in
        ``PipelineContext.publish_power_observation``)."""
        self.observation_mode = observation_mode
        self.integrity = PowerIntegrity(integrity)
        self.gate_rise_observed = gate_rise_observed
        self.gate_fall_observed = gate_fall_observed
        self.observation_deadline_s = observation_deadline_s


def classify_observation(
    metadata: PowerMetadata,
) -> tuple[ObservationMode, PowerIntegrity, bool, bool, float | None]:
    """Derive observation mode, integrity, observed edges, and deadline from
    capture metadata.

    The single source for the classification that previously existed twice
    with different defaults (``stages/capture_power.py`` vs
    ``PipelineContext.publish_power_result``): a gated clean-window scope is a
    valid ``gpio_gated`` observation; anything else is a degraded
    ``free_form`` one. Edge observations default to the scope verdict unless
    the capture recorded them explicitly.
    """
    gated = metadata.measurement_scope is MeasurementScope.GPIO_GATED_CLEAN_WINDOW
    mode = ObservationMode.GPIO_GATED if gated else ObservationMode.FREE_FORM
    integrity = PowerIntegrity.VALID if gated else PowerIntegrity.DEGRADED
    rise = metadata.gate_rise_observed if metadata.gate_rise_observed is not None else gated
    fall = metadata.gate_fall_observed if metadata.gate_fall_observed is not None else gated
    return mode, integrity, bool(rise), bool(fall), metadata.capture_safety_bound_s
