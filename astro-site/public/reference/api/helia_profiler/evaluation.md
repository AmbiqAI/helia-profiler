# helia_profiler.evaluation

Comparing two runs and judging one: the comparability rules that decide whether a comparison is meaningful, and the metric policies that turn numbers into a verdict.

Every name on this page is imported from `helia_profiler`.

**API tier:** `experimental`

Generated from the `src/helia_profiler` tree `872d67ad4e9083b1eacd7d6d3859148437b96b59`.

## helia_profiler.MetricDirection

`class` · `python`

```python
MetricDirection()
```

Preferred candidate direction for one metric.

**API tier:** `experimental`

Source: `src/helia_profiler/evaluation/comparison_profile.py:23`

### helia_profiler.MetricDirection.SMALLER

`constant` · `python`

```python
SMALLER = 'smaller'
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:26`

### helia_profiler.MetricDirection.LARGER

`constant` · `python`

```python
LARGER = 'larger'
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:27`

### helia_profiler.MetricDirection.EQUAL

`constant` · `python`

```python
EQUAL = 'equal'
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:28`

## helia_profiler.MissingMetricPolicy

`class` · `python`

```python
MissingMetricPolicy()
```

Verdict when a selected metric is unavailable.

**API tier:** `experimental`

Source: `src/helia_profiler/evaluation/comparison_profile.py:31`

### helia_profiler.MissingMetricPolicy.FAIL

`constant` · `python`

```python
FAIL = 'fail'
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:34`

### helia_profiler.MissingMetricPolicy.WARN

`constant` · `python`

```python
WARN = 'warn'
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:35`

### helia_profiler.MissingMetricPolicy.IGNORE

`constant` · `python`

```python
IGNORE = 'ignore'
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:36`

## helia_profiler.RunEvaluation

`class` · `python`

```python
RunEvaluation(
    validity: ResultValidity,
    issues: tuple[ResultIssue, ...] = (),
    gate_arbitration: GateArbitration | None = None,
) -> None
```

`dataclass`

Authoritative validity and structured issues for one completed run.

**API tier:** `experimental`

Source: `src/helia_profiler/evaluation/validity.py:33`

### helia_profiler.RunEvaluation.validity

`attribute` · `python`

```python
validity: ResultValidity
```

Source: `src/helia_profiler/evaluation/validity.py:37`

### helia_profiler.RunEvaluation.issues

`attribute` · `python`

```python
issues: tuple[ResultIssue, ...] = ()
```

Source: `src/helia_profiler/evaluation/validity.py:38`

### helia_profiler.RunEvaluation.gate_arbitration

`attribute` · `python`

```python
gate_arbitration: GateArbitration | None = None
```

Source: `src/helia_profiler/evaluation/validity.py:42`

## helia_profiler.ComparabilityIssue

`class` · `python`

```python
ComparabilityIssue(code: str, severity: ComparabilitySeverity, message: str, context: dict[str, Any] = dict()) -> None
```

`dataclass`

One machine-readable compatibility decision.

**API tier:** `experimental`

Source: `src/helia_profiler/evaluation/comparability.py:34`

### helia_profiler.ComparabilityIssue.code

`attribute` · `python`

```python
code: str
```

Source: `src/helia_profiler/evaluation/comparability.py:38`

### helia_profiler.ComparabilityIssue.severity

`attribute` · `python`

```python
severity: ComparabilitySeverity
```

Source: `src/helia_profiler/evaluation/comparability.py:39`

### helia_profiler.ComparabilityIssue.message

`attribute` · `python`

```python
message: str
```

Source: `src/helia_profiler/evaluation/comparability.py:40`

### helia_profiler.ComparabilityIssue.context

`attribute` · `python`

```python
context: dict[str, Any] = field(default_factory=dict)
```

Source: `src/helia_profiler/evaluation/comparability.py:41`

## helia_profiler.VerdictStatus

`class` · `python`

```python
VerdictStatus()
```

Regression policy outcome.

**API tier:** `experimental`

Source: `src/helia_profiler/evaluation/comparison_profile.py:39`

### helia_profiler.VerdictStatus.PASS

`constant` · `python`

```python
PASS = 'pass'
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:42`

### helia_profiler.VerdictStatus.WARN

`constant` · `python`

```python
WARN = 'warn'
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:43`

### helia_profiler.VerdictStatus.FAIL

`constant` · `python`

```python
FAIL = 'fail'
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:44`

### helia_profiler.VerdictStatus.SKIP

`constant` · `python`

```python
SKIP = 'skip'
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:45`

## helia_profiler.ComparabilityAssessment

`class` · `python`

```python
ComparabilityAssessment(issues: tuple[ComparabilityIssue, ...] = ()) -> None
```

`dataclass`

Whether run-level and per-layer deltas may be computed.

**API tier:** `experimental`

Source: `src/helia_profiler/evaluation/comparability.py:44`

### helia_profiler.ComparabilityAssessment.issues

`attribute` · `python`

```python
issues: tuple[ComparabilityIssue, ...] = ()
```

Source: `src/helia_profiler/evaluation/comparability.py:48`

### helia_profiler.ComparabilityAssessment.run_metrics_comparable

`attribute` · `python`

```python
run_metrics_comparable: bool
```

Source: `src/helia_profiler/evaluation/comparability.py:51`

### helia_profiler.ComparabilityAssessment.layers_comparable

`attribute` · `python`

```python
layers_comparable: bool
```

Source: `src/helia_profiler/evaluation/comparability.py:55`

### helia_profiler.ComparabilityAssessment.power_metrics_comparable

`attribute` · `python`

```python
power_metrics_comparable: bool
```

Source: `src/helia_profiler/evaluation/comparability.py:70`

### helia_profiler.ComparabilityAssessment.memory_metrics_comparable

`attribute` · `python`

```python
memory_metrics_comparable: bool
```

Per-region used/free rows are gated on the link family (#206).

Source: `src/helia_profiler/evaluation/comparability.py:74`

### helia_profiler.ComparabilityAssessment.metric_group_comparable

`method` · `python`

```python
metric_group_comparable(group: str) -> bool
```

Whether one metric group's rows may be computed (#206): no
METRIC_BLOCKING issue carrying that group's tag.

Source: `src/helia_profiler/evaluation/comparability.py:60`

## helia_profiler.MetricPolicy

`class` · `python`

```python
MetricPolicy(
    direction: MetricDirection,
    unit: str,
    max_regression_pct: float | None = None,
    max_regression_abs: float | None = None,
    missing: MissingMetricPolicy | None = None,
    extra: dict[str, Any] = dict(),
) -> None
```

`dataclass`

Tolerance and availability policy for one named comparison metric.

**API tier:** `experimental`

Source: `src/helia_profiler/evaluation/comparison_profile.py:48`

### helia_profiler.MetricPolicy.direction

`attribute` · `python`

```python
direction: MetricDirection
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:52`

### helia_profiler.MetricPolicy.unit

`attribute` · `python`

```python
unit: str
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:53`

### helia_profiler.MetricPolicy.max_regression_pct

`attribute` · `python`

```python
max_regression_pct: float | None = None
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:54`

### helia_profiler.MetricPolicy.max_regression_abs

`attribute` · `python`

```python
max_regression_abs: float | None = None
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:55`

### helia_profiler.MetricPolicy.missing

`attribute` · `python`

```python
missing: MissingMetricPolicy | None = None
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:56`

### helia_profiler.MetricPolicy.extra

`attribute` · `python`

```python
extra: dict[str, Any] = field(default_factory=dict, repr=False)
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:57`

### helia_profiler.MetricPolicy.from_dict

`method` · `python`

```python
from_dict(data: dict[str, Any]) -> Self
```

`classmethod`

Source: `src/helia_profiler/evaluation/comparison_profile.py:78`

### helia_profiler.MetricPolicy.to_dict

`method` · `python`

```python
to_dict() -> dict[str, Any]
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:89`

## helia_profiler.ComparisonProfile

`class` · `python`

```python
ComparisonProfile(
    schema: str,
    schema_version: int,
    metrics: dict[str, MetricPolicy],
    missing: MissingMetricPolicy | None = None,
    required_dimensions: tuple[str, ...] = (),
    name: str | None = None,
    extra: dict[str, Any] = dict(),
) -> None
```

`dataclass`

Open v1 profile selecting deterministic metric regression policies.

**API tier:** `experimental`

Source: `src/helia_profiler/evaluation/comparison_profile.py:98`

### helia_profiler.ComparisonProfile.schema

`attribute` · `python`

```python
schema: str
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:102`

### helia_profiler.ComparisonProfile.schema_version

`attribute` · `python`

```python
schema_version: int
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:103`

### helia_profiler.ComparisonProfile.metrics

`attribute` · `python`

```python
metrics: dict[str, MetricPolicy]
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:104`

### helia_profiler.ComparisonProfile.missing

`attribute` · `python`

```python
missing: MissingMetricPolicy | None = None
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:105`

### helia_profiler.ComparisonProfile.required_dimensions

`attribute` · `python`

```python
required_dimensions: tuple[str, ...] = ()
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:106`

### helia_profiler.ComparisonProfile.name

`attribute` · `python`

```python
name: str | None = None
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:107`

### helia_profiler.ComparisonProfile.extra

`attribute` · `python`

```python
extra: dict[str, Any] = field(default_factory=dict, repr=False)
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:108`

### helia_profiler.ComparisonProfile.from_dict

`method` · `python`

```python
from_dict(data: dict[str, Any]) -> Self
```

`classmethod`

Source: `src/helia_profiler/evaluation/comparison_profile.py:140`

### helia_profiler.ComparisonProfile.load

`method` · `python`

```python
load(path: str | Path) -> Self
```

`classmethod`

Source: `src/helia_profiler/evaluation/comparison_profile.py:154`

### helia_profiler.ComparisonProfile.to_dict

`method` · `python`

```python
to_dict() -> dict[str, Any]
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:165`

## helia_profiler.CompareResult

`class` · `python`

```python
CompareResult(
    baseline: RunArtifacts,
    candidate: RunArtifacts,
    config_rows: list[ConfigDiffRow],
    metrics: list[MetricDiff],
    layer_rows: list[LayerDiffRow],
    warnings: list[str] = list(),
    comparability: ComparabilityAssessment = ComparabilityAssessment(),
    verdict: ComparisonVerdict | None = None,
) -> None
```

`dataclass`

Full comparison between two profile runs.

**API tier:** `experimental`

Source: `src/helia_profiler/evaluation/compare.py:120`

### helia_profiler.CompareResult.baseline

`attribute` · `python`

```python
baseline: RunArtifacts
```

Source: `src/helia_profiler/evaluation/compare.py:124`

### helia_profiler.CompareResult.candidate

`attribute` · `python`

```python
candidate: RunArtifacts
```

Source: `src/helia_profiler/evaluation/compare.py:125`

### helia_profiler.CompareResult.config_rows

`attribute` · `python`

```python
config_rows: list[ConfigDiffRow]
```

Source: `src/helia_profiler/evaluation/compare.py:126`

### helia_profiler.CompareResult.metrics

`attribute` · `python`

```python
metrics: list[MetricDiff]
```

Source: `src/helia_profiler/evaluation/compare.py:127`

### helia_profiler.CompareResult.layer_rows

`attribute` · `python`

```python
layer_rows: list[LayerDiffRow]
```

Source: `src/helia_profiler/evaluation/compare.py:128`

### helia_profiler.CompareResult.warnings

`attribute` · `python`

```python
warnings: list[str] = field(default_factory=list)
```

Source: `src/helia_profiler/evaluation/compare.py:129`

### helia_profiler.CompareResult.comparability

`attribute` · `python`

```python
comparability: ComparabilityAssessment = field(default_factory=ComparabilityAssessment)
```

Source: `src/helia_profiler/evaluation/compare.py:130`

### helia_profiler.CompareResult.verdict

`attribute` · `python`

```python
verdict: ComparisonVerdict | None = None
```

Source: `src/helia_profiler/evaluation/compare.py:131`

## helia_profiler.assess_comparability

`function` · `python`

```python
assess_comparability(baseline: RunArtifacts, candidate: RunArtifacts) -> ComparabilityAssessment
```

Compare identity, validity, topology, and intentional run dimensions.

**API tier:** `experimental`

Source: `src/helia_profiler/evaluation/comparability.py:154`

## helia_profiler.MetricVerdict

`class` · `python`

```python
MetricVerdict(
    metric: str,
    status: VerdictStatus,
    message: str,
    baseline: float | None = None,
    candidate: float | None = None,
    regression: float | None = None,
    allowed_regression: float | None = None,
    unit: str = '',
) -> None
```

`dataclass`

Verdict and evidence for one selected metric.

**API tier:** `experimental`

Source: `src/helia_profiler/evaluation/comparison_profile.py:180`

### helia_profiler.MetricVerdict.metric

`attribute` · `python`

```python
metric: str
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:184`

### helia_profiler.MetricVerdict.status

`attribute` · `python`

```python
status: VerdictStatus
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:185`

### helia_profiler.MetricVerdict.message

`attribute` · `python`

```python
message: str
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:186`

### helia_profiler.MetricVerdict.baseline

`attribute` · `python`

```python
baseline: float | None = None
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:187`

### helia_profiler.MetricVerdict.candidate

`attribute` · `python`

```python
candidate: float | None = None
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:188`

### helia_profiler.MetricVerdict.regression

`attribute` · `python`

```python
regression: float | None = None
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:189`

### helia_profiler.MetricVerdict.allowed_regression

`attribute` · `python`

```python
allowed_regression: float | None = None
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:190`

### helia_profiler.MetricVerdict.unit

`attribute` · `python`

```python
unit: str = ''
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:191`

## helia_profiler.evaluate_run

`function` · `python`

```python
evaluate_run(ctx: PipelineContext) -> RunEvaluation
```

Evaluate captured results without mutating pipeline state.

**API tier:** `experimental`

Source: `src/helia_profiler/evaluation/validity.py:192`

## helia_profiler.ComparisonVerdict

`class` · `python`

```python
ComparisonVerdict(
    status: VerdictStatus,
    metrics: tuple[MetricVerdict, ...],
    dimension_mismatches: tuple[str, ...] = (),
    profile_name: str | None = None,
    profile_schema: str = COMPARISON_PROFILE_SCHEMA,
    profile_schema_version: int = COMPARISON_PROFILE_SCHEMA_VERSION,
    profile_sha256: str = '',
) -> None
```

`dataclass`

Deterministic verdict for one result pair and profile.

**API tier:** `experimental`

Source: `src/helia_profiler/evaluation/comparison_profile.py:194`

### helia_profiler.ComparisonVerdict.status

`attribute` · `python`

```python
status: VerdictStatus
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:198`

### helia_profiler.ComparisonVerdict.metrics

`attribute` · `python`

```python
metrics: tuple[MetricVerdict, ...]
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:199`

### helia_profiler.ComparisonVerdict.dimension_mismatches

`attribute` · `python`

```python
dimension_mismatches: tuple[str, ...] = ()
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:200`

### helia_profiler.ComparisonVerdict.profile_name

`attribute` · `python`

```python
profile_name: str | None = None
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:201`

### helia_profiler.ComparisonVerdict.profile_schema

`attribute` · `python`

```python
profile_schema: str = COMPARISON_PROFILE_SCHEMA
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:202`

### helia_profiler.ComparisonVerdict.profile_schema_version

`attribute` · `python`

```python
profile_schema_version: int = COMPARISON_PROFILE_SCHEMA_VERSION
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:203`

### helia_profiler.ComparisonVerdict.profile_sha256

`attribute` · `python`

```python
profile_sha256: str = ''
```

Source: `src/helia_profiler/evaluation/comparison_profile.py:204`

## helia_profiler.evaluate_comparison_profile

`function` · `python`

```python
evaluate_comparison_profile(result: CompareResult, profile: ComparisonProfile) -> ComparisonVerdict
```

Evaluate existing metric deltas against one versioned profile.

**API tier:** `experimental`

Source: `src/helia_profiler/evaluation/comparison_profile.py:207`

## helia_profiler.ComparabilitySeverity

`class` · `python`

```python
ComparabilitySeverity()
```

Effect of one comparability issue on comparison output.

Defined here so the comparability registry can bind severity to code
without importing from ``evaluation``; ``evaluation.comparability``
re-exports it, which remains the canonical public import path.

**API tier:** `experimental`

Source: `src/helia_profiler/results/issues.py:320`

### helia_profiler.ComparabilitySeverity.BLOCKING

`constant` · `python`

```python
BLOCKING = 'blocking'
```

Source: `src/helia_profiler/results/issues.py:328`

### helia_profiler.ComparabilitySeverity.LAYER_BLOCKING

`constant` · `python`

```python
LAYER_BLOCKING = 'layer_blocking'
```

Source: `src/helia_profiler/results/issues.py:329`

### helia_profiler.ComparabilitySeverity.METRIC_BLOCKING

`constant` · `python`

```python
METRIC_BLOCKING = 'metric_blocking'
```

Source: `src/helia_profiler/results/issues.py:330`

### helia_profiler.ComparabilitySeverity.INFORMATIVE

`constant` · `python`

```python
INFORMATIVE = 'informative'
```

Source: `src/helia_profiler/results/issues.py:331`
