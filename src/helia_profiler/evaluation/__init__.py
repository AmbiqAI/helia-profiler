"""Run evaluation: validity, comparability/compare, and model-cost analysis.

Three blocks share this package today: run-validity (``validity``),
comparison (``compare``/``comparability``/``comparison_profile``/
``run_metrics``), and the model-cost core (``model_analysis``,
``layer_attribution``, ``softmax_preflight`` — the #229 extraction seam).
"""

from .comparability import (
    ComparabilityAssessment,
    ComparabilityIssue,
    ComparabilitySeverity,
    assess_comparability,
)
from .compare import (
    CompareResult,
    ConfigDiffRow,
    CounterDiff,
    LayerDiffRow,
    MetricDiff,
    RunArtifacts,
    compare_runs,
    write_compare_artifacts,
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
from .model_analysis import (
    LayerOps,
    ModelAnalysis,
    analyze_for_engine,
    analyze_model,
    is_available,
)

__all__ = [
    "ComparabilityAssessment",
    "ComparabilityIssue",
    "ComparabilitySeverity",
    "CompareResult",
    "ConfigDiffRow",
    "CounterDiff",
    "ComparisonProfile",
    "ComparisonVerdict",
    "MetricDirection",
    "MetricDiff",
    "MetricPolicy",
    "MetricVerdict",
    "MissingMetricPolicy",
    "RunEvaluation",
    "RunArtifacts",
    "LayerDiffRow",
    "LayerOps",
    "ModelAnalysis",
    "VerdictStatus",
    "assess_comparability",
    "analyze_for_engine",
    "analyze_model",
    "compare_runs",
    "evaluate_comparison_profile",
    "evaluate_run",
    "is_available",
    "write_compare_artifacts",
]
