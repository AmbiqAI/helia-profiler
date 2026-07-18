"""Versioned deterministic regression policy for HPX comparisons."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from ..errors import ReportError

if TYPE_CHECKING:
    from ..compare import CompareResult, MetricDiff

COMPARISON_PROFILE_SCHEMA = "hpx.comparison-profile"
COMPARISON_PROFILE_SCHEMA_VERSION = 1


class MetricDirection(StrEnum):
    """Preferred candidate direction for one metric."""

    SMALLER = "smaller"
    LARGER = "larger"
    EQUAL = "equal"


class MissingMetricPolicy(StrEnum):
    """Verdict when a selected metric is unavailable."""

    FAIL = "fail"
    WARN = "warn"
    IGNORE = "ignore"


class VerdictStatus(StrEnum):
    """Regression policy outcome."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class MetricPolicy:
    """Tolerance and availability policy for one named comparison metric."""

    direction: MetricDirection
    unit: str
    max_regression_pct: float | None = None
    max_regression_abs: float | None = None
    missing: MissingMetricPolicy | None = None
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.direction, MetricDirection):
            raise ReportError("Metric policy direction must be a MetricDirection value.")
        for name in ("max_regression_pct", "max_regression_abs"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value < 0
            ):
                raise ReportError(f"Metric policy {name} must be a non-negative number.")
        if not isinstance(self.unit, str):
            raise ReportError("Metric policy unit must be a string.")
        if self.missing is not None and not isinstance(self.missing, MissingMetricPolicy):
            raise ReportError("Metric policy missing must be a MissingMetricPolicy value.")
        if not isinstance(self.extra, dict):
            raise ReportError("Metric policy extra fields must be an object.")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return _from_dict(
            cls,
            data,
            transforms={
                "direction": MetricDirection,
                "missing": lambda value: MissingMetricPolicy(value) if value is not None else None,
            },
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        extra = data.pop("extra")
        data["direction"] = self.direction.value
        if self.missing is not None:
            data["missing"] = self.missing.value
        return {**extra, **{key: value for key, value in data.items() if value is not None}}


@dataclass(frozen=True)
class ComparisonProfile:
    """Open v1 profile selecting deterministic metric regression policies."""

    schema: str
    schema_version: int
    metrics: dict[str, MetricPolicy]
    missing: MissingMetricPolicy | None = None
    required_dimensions: tuple[str, ...] = ()
    name: str | None = None
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.schema != COMPARISON_PROFILE_SCHEMA:
            raise ReportError(f"Unsupported comparison profile schema: {self.schema!r}")
        if (
            isinstance(self.schema_version, bool)
            or self.schema_version != COMPARISON_PROFILE_SCHEMA_VERSION
        ):
            raise ReportError(
                f"Unsupported comparison profile version: {self.schema_version!r}"
            )
        if not isinstance(self.metrics, dict) or not self.metrics or not all(
            isinstance(key, str) and key and isinstance(value, MetricPolicy)
            for key, value in self.metrics.items()
        ):
            raise ReportError("Comparison profile metrics must map names to MetricPolicy values.")
        if self.missing is not None and not isinstance(self.missing, MissingMetricPolicy):
            raise ReportError("Comparison profile missing must be a MissingMetricPolicy value.")
        if not isinstance(self.required_dimensions, tuple) or not all(
            isinstance(value, str) and value for value in self.required_dimensions
        ):
            raise ReportError("Comparison profile required_dimensions must be an array of names.")
        if len(set(self.required_dimensions)) != len(self.required_dimensions):
            raise ReportError("Comparison profile required_dimensions must be unique.")
        if self.name is not None and (not isinstance(self.name, str) or not self.name):
            raise ReportError("Comparison profile name must be a non-empty string.")
        if not isinstance(self.extra, dict):
            raise ReportError("Comparison profile extra fields must be an object.")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return _from_dict(
            cls,
            data,
            transforms={
                "metrics": lambda values: {
                    key: MetricPolicy.from_dict(value) for key, value in values.items()
                },
                "missing": MissingMetricPolicy,
                "required_dimensions": _dimension_tuple,
            },
        )

    @classmethod
    def load(cls, path: str | Path) -> Self:
        profile_path = Path(path)
        try:
            data = json.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReportError(f"Cannot load comparison profile {profile_path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ReportError(f"Comparison profile must contain a JSON object: {profile_path}")
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "metrics": {key: value.to_dict() for key, value in self.metrics.items()},
        }
        if self.missing is not None:
            data["missing"] = self.missing.value
        if self.required_dimensions:
            data["required_dimensions"] = list(self.required_dimensions)
        if self.name is not None:
            data["name"] = self.name
        return {**self.extra, **data}


@dataclass(frozen=True)
class MetricVerdict:
    """Verdict and evidence for one selected metric."""

    metric: str
    status: VerdictStatus
    message: str
    baseline: float | None = None
    candidate: float | None = None
    regression: float | None = None
    allowed_regression: float | None = None
    unit: str = ""


@dataclass(frozen=True)
class ComparisonVerdict:
    """Deterministic verdict for one result pair and profile."""

    status: VerdictStatus
    metrics: tuple[MetricVerdict, ...]
    dimension_mismatches: tuple[str, ...] = ()
    profile_name: str | None = None
    profile_schema: str = COMPARISON_PROFILE_SCHEMA
    profile_schema_version: int = COMPARISON_PROFILE_SCHEMA_VERSION
    profile_sha256: str = ""


def evaluate_comparison_profile(
    result: CompareResult,
    profile: ComparisonProfile,
) -> ComparisonVerdict:
    """Evaluate existing metric deltas against one versioned profile."""
    metric_by_name = {metric.name: metric for metric in result.metrics}
    verdicts = tuple(
        _evaluate_metric(
            name,
            metric_by_name.get(name),
            policy,
            policy.missing or profile.missing or MissingMetricPolicy.FAIL,
        )
        for name, policy in profile.metrics.items()
    )
    config_by_key = {row.key: row for row in result.config_rows}
    mismatches = tuple(
        dimension
        for dimension in profile.required_dimensions
        if dimension not in config_by_key or config_by_key[dimension].status != "same"
    )
    if mismatches or any(item.status is VerdictStatus.FAIL for item in verdicts):
        status = VerdictStatus.FAIL
    elif any(item.status is VerdictStatus.WARN for item in verdicts):
        status = VerdictStatus.WARN
    elif verdicts and all(item.status is VerdictStatus.SKIP for item in verdicts):
        status = VerdictStatus.SKIP
    else:
        status = VerdictStatus.PASS
    return ComparisonVerdict(
        status=status,
        metrics=verdicts,
        dimension_mismatches=mismatches,
        profile_name=profile.name,
        profile_sha256=_profile_digest(profile),
    )


def _evaluate_metric(
    name: str,
    metric: MetricDiff | None,
    policy: MetricPolicy,
    missing: MissingMetricPolicy,
) -> MetricVerdict:
    if metric is None or metric.baseline is None or metric.candidate is None:
        status = {
            MissingMetricPolicy.FAIL: VerdictStatus.FAIL,
            MissingMetricPolicy.WARN: VerdictStatus.WARN,
            MissingMetricPolicy.IGNORE: VerdictStatus.SKIP,
        }[missing]
        return MetricVerdict(name, status, "Metric is unavailable.")
    if metric.unit != policy.unit:
        return MetricVerdict(
            name,
            VerdictStatus.FAIL,
            f"Metric unit is {metric.unit!r}, expected {policy.unit!r}.",
            unit=metric.unit,
        )
    baseline = _number(metric.baseline)
    candidate = _number(metric.candidate)
    if baseline is None or candidate is None:
        return MetricVerdict(name, VerdictStatus.FAIL, "Metric values are not numeric.")
    delta = candidate - baseline
    if policy.direction is MetricDirection.SMALLER:
        regression = max(0.0, delta)
    elif policy.direction is MetricDirection.LARGER:
        regression = max(0.0, -delta)
    else:
        regression = abs(delta)
    allowed = max(
        float(policy.max_regression_abs or 0.0),
        abs(baseline) * float(policy.max_regression_pct or 0.0) / 100.0,
    )
    status = VerdictStatus.PASS if regression <= allowed else VerdictStatus.FAIL
    message = (
        "Within regression tolerance."
        if status is VerdictStatus.PASS
        else "Regression exceeds tolerance."
    )
    return MetricVerdict(
        metric=name,
        status=status,
        message=message,
        baseline=baseline,
        candidate=candidate,
        regression=regression,
        allowed_regression=allowed,
        unit=metric.unit,
    )


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _profile_digest(profile: ComparisonProfile) -> str:
    canonical = json.dumps(
        profile.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _dimension_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ReportError("Comparison profile required_dimensions must be an array of names.")
    return tuple(value)


def _from_dict(cls, data: dict[str, Any], transforms: dict[str, Any] | None = None):
    if not isinstance(data, dict):
        raise ReportError(f"Expected JSON object for {cls.__name__}.")
    transforms = transforms or {}
    known = {item.name for item in fields(cls) if item.name != "extra"}
    try:
        values = {
            key: transforms.get(key, lambda value: value)(value)
            for key, value in data.items()
            if key in known
        }
        values["extra"] = {key: value for key, value in data.items() if key not in known}
        return cls(**values)
    except ReportError:
        raise
    except (TypeError, ValueError) as exc:
        raise ReportError(f"Invalid {cls.__name__}: {exc}") from exc
