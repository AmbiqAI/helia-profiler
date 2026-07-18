"""Result validity, comparability, and regression policy."""

from .comparability import (
    ComparabilityAssessment,
    ComparabilityIssue,
    ComparabilitySeverity,
    assess_comparability,
)
from .comparison_profile import (
    ComparisonProfile,
    ComparisonVerdict,
    MetricDirection,
    MetricPolicy,
    MetricVerdict,
    MissingMetricPolicy,
    VerdictStatus,
    evaluate_comparison_profile,
)
from .validity import RunEvaluation, evaluate_run

__all__ = [
    "ComparabilityAssessment",
    "ComparabilityIssue",
    "ComparabilitySeverity",
    "ComparisonProfile",
    "ComparisonVerdict",
    "MetricDirection",
    "MetricPolicy",
    "MetricVerdict",
    "MissingMetricPolicy",
    "RunEvaluation",
    "VerdictStatus",
    "assess_comparability",
    "evaluate_comparison_profile",
    "evaluate_run",
]
