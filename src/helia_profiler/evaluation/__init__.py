"""Run evaluation: validity, comparability/compare, and engine-dispatched analysis.

Two blocks live here — run-validity (``validity``) and comparison
(``compare``/``comparability``/``comparison_profile``/``run_metrics``) —
plus ``engine_analysis``, the engine-dispatch shim over the model-cost
core that now lives in :mod:`helia_profiler.modelcost` (#229 D4).
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
from .engine_analysis import analyze_for_engine
from ..modelcost import (
    ETHOS_U_OP_NAME,
    LayerOps,
    ModelAnalysis,
    analyze_model,
    is_available,
    vela_accelerator_config,
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
    "ETHOS_U_OP_NAME",
    "MetricDirection",
    "MetricDiff",
    "MetricPolicy",
    "MetricVerdict",
    "MissingMetricPolicy",
    "RunEvaluation",
    "RunArtifacts",
    "LayerDiffRow",
    "VerdictStatus",
    "assess_comparability",
    "analyze_for_engine",
    "analyze_model",
    "compare_runs",
    "evaluate_comparison_profile",
    "evaluate_run",
    "is_available",
    "vela_accelerator_config",
    "write_compare_artifacts",
]
