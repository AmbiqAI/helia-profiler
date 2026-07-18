# Results

The typed data returned by [`profile()`](profile.md). All result types are
frozen dataclasses — see the [architecture overview](../../architecture/index.md)
for why bare dicts aren't used between pipeline stages (the one exception is
`LayerResult.counters`, whose keys are dynamic PMU counter names).

::: helia_profiler.ProfileResult

::: helia_profiler.PmuResult

::: helia_profiler.PresetResult

::: helia_profiler.LayerResult

::: helia_profiler.FirmwareMeta

::: helia_profiler.RunMetadata

::: helia_profiler.NsxModuleRef

::: helia_profiler.PowerResult

## Result bundles

The result manifest is a small stable envelope around open provenance,
comparability, and extension data. Loading preserves unknown fields so newer
producers can evolve additively without older tools silently deleting data.

::: helia_profiler.load_result_manifest

::: helia_profiler.ResultManifest

::: helia_profiler.ResultArtifact

::: helia_profiler.ResultIssue

::: helia_profiler.RunStatus

::: helia_profiler.ResultValidity

## Validity and comparability

The same pure policy functions drive manifests, summary output, comparisons,
and programmatic consumers. Invalid runs and model mismatches block run-level
deltas. Topology differences suppress only per-layer deltas. Power scope,
mode, firmware, or integrity differences suppress only power metrics, while
intentional engine, toolchain, clock, board, transport, and placement changes
remain informative comparison dimensions.

::: helia_profiler.evaluate_run

::: helia_profiler.RunEvaluation

::: helia_profiler.assess_comparability

::: helia_profiler.ComparabilityAssessment

::: helia_profiler.ComparabilityIssue

::: helia_profiler.ComparabilitySeverity

## Regression profiles

Versioned comparison profiles apply deterministic direction, unit, tolerance,
missing-metric, and required-dimension policy to an existing `CompareResult`.
They remain separate from the loose result-bundle schema.

::: helia_profiler.ComparisonProfile

::: helia_profiler.MetricPolicy

::: helia_profiler.MetricDirection

::: helia_profiler.MissingMetricPolicy

::: helia_profiler.evaluate_comparison_profile

::: helia_profiler.ComparisonVerdict

::: helia_profiler.MetricVerdict

::: helia_profiler.VerdictStatus
