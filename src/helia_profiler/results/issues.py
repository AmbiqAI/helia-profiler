"""Registry of stable machine-readable issue codes.

Every diagnostic code HPX can emit is declared here, in one of three forms:

* :class:`IssueCode` — run-validity codes carried by
  :class:`~helia_profiler.results.manifest.ResultIssue` (emitted only by
  ``evaluation.validity.evaluate_run``).
* :class:`ComparabilityCode` — static comparability codes carried by
  ``evaluation.comparability.ComparabilityIssue``.
* :class:`ComparabilityCodeFamily` — the two parameterized comparability
  families whose concrete codes embed a :class:`ComparisonDimension`.

The enums are ``StrEnum``, so a member serializes byte-identically to the
string literal it replaced; ``summary.json``, ``result_manifest.json``, and
compare artifacts are unchanged by construction (proven by the report golden
digests). Reader-side fields (``ResultIssue.code``) deliberately stay ``str``:
result bundles round-trip across HPX versions, so rehydration must tolerate
codes this build does not know. The registry types a code at *emission*, not
at load.

``docs/reference/issue-codes.md`` is generated from this module by
``tools/gen_issue_code_reference.py`` and drift-tested like the configuration
reference.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Literal, Mapping

from ..errors import ReportError

Severity = Literal["error", "warning"]


class IssueCode(StrEnum):
    """Run-validity issue codes (``ResultIssue.code`` values)."""

    PMU_MISSING = "pmu.missing"
    PMU_COUNTER_OVERFLOW = "pmu.counter_overflow"

    PROFILE_CLEAN_WINDOW_FROZEN = "profile.clean_window_frozen"
    PROFILE_CLEAN_WINDOW_CLOCK_RATE_LOW = "profile.clean_window_clock_rate_low"
    PROFILE_CLEAN_WINDOW_STALLED = "profile.clean_window_stalled"
    PROFILE_CLEAN_WINDOW_CHECK_INOPERATIVE = "profile.clean_window_check_inoperative"

    POWER_OBSERVATION_MISSING = "power.observation_missing"
    POWER_OBSERVATION_INVALID = "power.observation_invalid"
    POWER_OBSERVATION_DEGRADED = "power.observation_degraded"
    POWER_GATE_EDGES_MISSING = "power.gate_edges_missing"
    POWER_GATE_NOT_LOWERED = "power.gate_not_lowered"
    POWER_GATE_DURATION_MISMATCH = "power.gate_duration_mismatch"
    POWER_GATE_DURATION_UNVERIFIABLE = "power.gate_duration_unverifiable"
    POWER_TERMINAL_MISSING = "power.terminal_missing"
    POWER_TERMINAL_ERROR = "power.terminal_error"
    POWER_TERMINAL_INCOMPLETE = "power.terminal_incomplete"
    POWER_PLAN_COUNT_MISMATCH = "power.plan_count_mismatch"
    POWER_WINDOW_CLOCK_FROZEN = "power.window_clock_frozen"
    POWER_WINDOW_CLOCK_MISMATCH = "power.window_clock_mismatch"
    POWER_WINDOW_CLOCK_EXCEEDS_HOST_TIME = "power.window_clock_exceeds_host_time"
    POWER_ON_DEVICE_OVERFLOW = "power.on_device_overflow"
    POWER_ON_DEVICE_COUNT_MISMATCH = "power.on_device_count_mismatch"
    POWER_ON_DEVICE_MEASUREMENT_MISSING = "power.on_device_measurement_missing"


@dataclass(frozen=True)
class IssueSpec:
    """Contract for one :class:`IssueCode`.

    ``severity`` is the code's single allowed severity. The two codes whose
    severity depends on the power mode set ``severity=None`` and declare both
    levels: whether the broken number is the measurement of record decides
    fatal-vs-warn, and that decision stays at the emit site — the registry
    records the envelope so an emit outside it cannot ship.
    """

    code: IssueCode
    description: str
    severity: Severity | None = None
    internal_severity: Severity | None = None
    external_severity: Severity | None = None

    def __post_init__(self) -> None:
        fixed = self.severity is not None
        moded = self.internal_severity is not None and self.external_severity is not None
        if fixed == moded:
            raise ReportError(
                f"Issue spec for '{self.code}' must declare exactly one of a "
                "fixed severity or an internal/external severity pair."
            )

    @property
    def mode_dependent(self) -> bool:
        return self.severity is None

    def allowed_severities(self) -> frozenset[str]:
        if self.severity is not None:
            return frozenset((self.severity,))
        return frozenset((self.internal_severity, self.external_severity))


_ISSUE_SPECS: tuple[IssueSpec, ...] = (
    IssueSpec(
        IssueCode.PMU_MISSING,
        "The run has no PMU result.",
        severity="error",
    ),
    IssueSpec(
        IssueCode.PMU_COUNTER_OVERFLOW,
        "One or more PMU counters overflowed during capture.",
        severity="error",
    ),
    IssueSpec(
        IssueCode.PROFILE_CLEAN_WINDOW_FROZEN,
        "The clean window completed inferences in zero elapsed time; the "
        "clock timing it never advanced.",
        severity="warning",
    ),
    IssueSpec(
        IssueCode.PROFILE_CLEAN_WINDOW_CLOCK_RATE_LOW,
        "The clean window's cycle counter ran far below its expected rate, "
        "measured against an independent clock.",
        severity="warning",
    ),
    IssueSpec(
        IssueCode.PROFILE_CLEAN_WINDOW_STALLED,
        "The clean-inference window's cycle counter stalled; derived timings "
        "understate the true per-inference time.",
        severity="warning",
    ),
    IssueSpec(
        IssueCode.PROFILE_CLEAN_WINDOW_CHECK_INOPERATIVE,
        "The clean window's partial-stall check could not run; absence of "
        "stalls is not evidence of a healthy window.",
        severity="warning",
    ),
    IssueSpec(
        IssueCode.POWER_OBSERVATION_MISSING,
        "Power observation is missing.",
        severity="error",
    ),
    IssueSpec(
        IssueCode.POWER_OBSERVATION_INVALID,
        "Power observation integrity is invalid.",
        severity="error",
    ),
    IssueSpec(
        IssueCode.POWER_OBSERVATION_DEGRADED,
        "Power observation is diagnostic and not valid for efficiency "
        "metrics.",
        severity="warning",
    ),
    IssueSpec(
        IssueCode.POWER_GATE_EDGES_MISSING,
        "GPIO-gated power capture is missing a gate edge.",
        severity="error",
    ),
    IssueSpec(
        IssueCode.POWER_GATE_NOT_LOWERED,
        "Power firmware did not confirm GATE low.",
        severity="error",
    ),
    IssueSpec(
        IssueCode.POWER_GATE_DURATION_MISMATCH,
        "Measured power-gate duration does not agree with the expected "
        "window.",
        severity="warning",
    ),
    IssueSpec(
        IssueCode.POWER_GATE_DURATION_UNVERIFIABLE,
        "Power-gate duration cannot be verified because clean inference "
        "timing is invalid.",
        severity="warning",
    ),
    IssueSpec(
        IssueCode.POWER_TERMINAL_MISSING,
        "Dedicated power firmware did not publish terminal status.",
        severity="error",
    ),
    IssueSpec(
        IssueCode.POWER_TERMINAL_ERROR,
        "Power firmware reported an error.",
        severity="error",
    ),
    IssueSpec(
        IssueCode.POWER_TERMINAL_INCOMPLETE,
        "Power firmware completed a different inference count than "
        "requested.",
        severity="error",
    ),
    IssueSpec(
        IssueCode.POWER_PLAN_COUNT_MISMATCH,
        "Power firmware requested count differs from the host plan.",
        severity="error",
    ),
    IssueSpec(
        IssueCode.POWER_WINDOW_CLOCK_FROZEN,
        "Power firmware reported zero elapsed time for completed "
        "inferences.",
        internal_severity="error",
        external_severity="warning",
    ),
    IssueSpec(
        IssueCode.POWER_WINDOW_CLOCK_MISMATCH,
        "Firmware-reported window duration does not agree with the "
        "independently measured window.",
        severity="warning",
    ),
    IssueSpec(
        IssueCode.POWER_WINDOW_CLOCK_EXCEEDS_HOST_TIME,
        "Firmware-reported window is longer than the host wall time that "
        "contained it.",
        severity="warning",
    ),
    IssueSpec(
        IssueCode.POWER_ON_DEVICE_OVERFLOW,
        "On-device power monitor reported accumulator overflow.",
        internal_severity="error",
        external_severity="warning",
    ),
    IssueSpec(
        IssueCode.POWER_ON_DEVICE_COUNT_MISMATCH,
        "On-device measurement count differs from completed work.",
        severity="error",
    ),
    IssueSpec(
        IssueCode.POWER_ON_DEVICE_MEASUREMENT_MISSING,
        "Internal power mode has no on-device measurement.",
        severity="error",
    ),
)

ISSUE_REGISTRY: Mapping[IssueCode, IssueSpec] = MappingProxyType(
    {spec.code: spec for spec in _ISSUE_SPECS}
)


class ComparabilitySeverity(StrEnum):
    """Effect of one comparability issue on comparison output.

    Defined here so the comparability registry can bind severity to code
    without importing from ``evaluation``; ``evaluation.comparability``
    re-exports it, which remains the canonical public import path.
    """

    BLOCKING = "blocking"
    LAYER_BLOCKING = "layer_blocking"
    METRIC_BLOCKING = "metric_blocking"
    INFORMATIVE = "informative"


class ComparabilityCode(StrEnum):
    """Static comparability codes (``ComparabilityIssue.code`` values)."""

    RESULT_INCOMPLETE = "result.incomplete"
    RESULT_INVALID = "result.invalid"
    RESULT_INVALID_PMU_OVERFLOW = "result.invalid_pmu_overflow"
    RESULT_DEGRADED = "result.degraded"
    IDENTITY_MODEL_MISMATCH = "identity.model_mismatch"
    METRIC_POWER_INTEGRITY_INVALID = "metric.power_integrity_invalid"
    TOPOLOGY_LAYER_COUNT_MISMATCH = "topology.layer_count_mismatch"
    TOPOLOGY_OPERATION_SEQUENCE_MISMATCH = "topology.operation_sequence_mismatch"


class ComparisonDimension(StrEnum):
    """Comparison dimensions that parameterize comparability codes.

    Provisional home: Phase 3 of #154 replaces the parallel dimension lists
    in ``report/manifest.py`` and ``evaluation/comparability.py`` with a full
    dimension model, which will own these names. Until then this enum only
    needs to cover the dimensions that appear inside emitted code strings.
    """

    # Power dimensions — a mismatch blocks power metrics.
    POWER_SCOPE = "power_scope"
    POWER_MODE = "power_mode"
    POWER_FIRMWARE = "power_firmware"
    POWER_MONITOR = "power_monitor"
    POWER_LOCKSTEP = "power_lockstep"
    POWER_CLEAN_WINDOW_PROBE = "power_clean_window_probe"

    # Informative dimensions — a difference is reported, never blocking.
    HPX_VERSION = "hpx_version"
    ENGINE = "engine"
    BOARD = "board"
    SOC = "soc"
    CPU_CLOCK = "cpu_clock"
    TOOLCHAIN = "toolchain"
    COMPILER_VERSION = "compiler_version"
    SYSTEM_CLOCK_HZ = "system_clock_hz"
    RUN_SUMMARY_SCHEMA_VERSION = "run_summary_schema_version"
    RUN_METADATA_SCHEMA_VERSION = "run_metadata_schema_version"
    TRANSPORT = "transport"
    ARENA_LOCATION = "arena_location"
    WEIGHTS_LOCATION = "weights_location"


@dataclass(frozen=True)
class ComparabilitySpec:
    """Contract for one static :class:`ComparabilityCode`."""

    code: ComparabilityCode
    severity: ComparabilitySeverity
    description: str


_COMPARABILITY_SPECS: tuple[ComparabilitySpec, ...] = (
    ComparabilitySpec(
        ComparabilityCode.RESULT_INCOMPLETE,
        ComparabilitySeverity.BLOCKING,
        "A result bundle is not complete; no comparison is possible.",
    ),
    ComparabilitySpec(
        ComparabilityCode.RESULT_INVALID,
        ComparabilitySeverity.BLOCKING,
        "A result is invalid and cannot be compared.",
    ),
    ComparabilitySpec(
        ComparabilityCode.RESULT_INVALID_PMU_OVERFLOW,
        ComparabilitySeverity.BLOCKING,
        "A legacy result without a manifest has PMU counter overflow.",
    ),
    ComparabilitySpec(
        ComparabilityCode.RESULT_DEGRADED,
        ComparabilitySeverity.INFORMATIVE,
        "A result is degraded; affected metrics should be interpreted "
        "cautiously.",
    ),
    ComparabilitySpec(
        ComparabilityCode.IDENTITY_MODEL_MISMATCH,
        ComparabilitySeverity.BLOCKING,
        "Model SHA-256 differs; run-level performance deltas are not "
        "comparable.",
    ),
    ComparabilitySpec(
        ComparabilityCode.METRIC_POWER_INTEGRITY_INVALID,
        ComparabilitySeverity.METRIC_BLOCKING,
        "Power metrics omitted because a power result's integrity is not "
        "valid.",
    ),
    ComparabilitySpec(
        ComparabilityCode.TOPOLOGY_LAYER_COUNT_MISMATCH,
        ComparabilitySeverity.LAYER_BLOCKING,
        "Per-layer deltas omitted because layer counts differ.",
    ),
    ComparabilitySpec(
        ComparabilityCode.TOPOLOGY_OPERATION_SEQUENCE_MISMATCH,
        ComparabilitySeverity.LAYER_BLOCKING,
        "Per-layer deltas omitted because operation sequences differ.",
    ),
)

COMPARABILITY_REGISTRY: Mapping[ComparabilityCode, ComparabilitySpec] = MappingProxyType(
    {spec.code: spec for spec in _COMPARABILITY_SPECS}
)


@dataclass(frozen=True)
class ComparabilityCodeFamily:
    """One parameterized comparability-code family.

    ``pattern`` is the human-readable shape shown in docs; ``code_for``
    produces the exact wire string for one dimension. The wire strings are
    frozen: ``POWER_DIMENSION_MISMATCH`` embeds a dimension name that itself
    starts with ``power_``, so the emitted code doubles the prefix
    (``metric.power_power_scope_mismatch``). That is the shipped format the
    report goldens and downstream consumers pin; renaming it is a deliberate
    wire-format change for Phase 3 of #154, not a side effect of typing.
    """

    pattern: str
    severity: ComparabilitySeverity
    description: str
    dimensions: tuple[ComparisonDimension, ...]
    _prefix: str
    _suffix: str

    def code_for(self, dimension: ComparisonDimension) -> str:
        if dimension not in self.dimensions:
            raise ReportError(
                f"Dimension '{dimension}' is not part of the "
                f"'{self.pattern}' comparability family."
            )
        return f"{self._prefix}{dimension.value}{self._suffix}"


POWER_DIMENSION_MISMATCH = ComparabilityCodeFamily(
    pattern="metric.power_<dimension>_mismatch",
    severity=ComparabilitySeverity.METRIC_BLOCKING,
    description="Power metrics omitted because a power comparison dimension "
    "differs between the runs.",
    dimensions=(
        ComparisonDimension.POWER_SCOPE,
        ComparisonDimension.POWER_MODE,
        ComparisonDimension.POWER_FIRMWARE,
        ComparisonDimension.POWER_MONITOR,
        ComparisonDimension.POWER_LOCKSTEP,
        ComparisonDimension.POWER_CLEAN_WINDOW_PROBE,
    ),
    _prefix="metric.power_",
    _suffix="_mismatch",
)

DIMENSION_DIFFERS = ComparabilityCodeFamily(
    pattern="dimension.<dimension>_differs",
    severity=ComparabilitySeverity.INFORMATIVE,
    description="A comparison dimension differs between the runs; deltas "
    "remain comparable but should be read in that light.",
    dimensions=(
        ComparisonDimension.HPX_VERSION,
        ComparisonDimension.ENGINE,
        ComparisonDimension.BOARD,
        ComparisonDimension.SOC,
        ComparisonDimension.CPU_CLOCK,
        ComparisonDimension.TOOLCHAIN,
        ComparisonDimension.COMPILER_VERSION,
        ComparisonDimension.SYSTEM_CLOCK_HZ,
        ComparisonDimension.RUN_SUMMARY_SCHEMA_VERSION,
        ComparisonDimension.RUN_METADATA_SCHEMA_VERSION,
        ComparisonDimension.TRANSPORT,
        ComparisonDimension.ARENA_LOCATION,
        ComparisonDimension.WEIGHTS_LOCATION,
    ),
    _prefix="dimension.",
    _suffix="_differs",
)

COMPARABILITY_FAMILIES: tuple[ComparabilityCodeFamily, ...] = (
    POWER_DIMENSION_MISMATCH,
    DIMENSION_DIFFERS,
)
