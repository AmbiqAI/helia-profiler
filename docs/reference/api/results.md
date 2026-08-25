# Results

The typed data returned by [`profile()`](profile.md). Core measurement records
are frozen against field reassignment, while run metadata is enriched as the
pipeline executes. Dynamic nested collections such as counters, engine
extensions, power samples, and metadata remain mutable for compatibility and
efficient capture assembly. Treat collections on returned results as read-only.

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

## Dependency lock provenance

`read_dependency_lock_provenance()` is a read-only provider for later
diagnostics collectors. Pass a prepared `profiler_app`, its `nsx.lock` or
`hpx-dependencies.json`, or the parent fingerprint workspace. It verifies the
exact lock SHA-256 and returns a frozen typed surface; it does not resolve,
synchronize, sanitize, or write files.

The stable join keys are `baseline_fingerprint`
(`CompatibilityBaseline.fingerprint`) and `lock_sha256`.

::: helia_profiler.read_dependency_lock_provenance

::: helia_profiler.DependencyLockProvenance

## Field-diagnostics support bundle

`collect_support_bundle()` is the diagnostics collector `read_dependency_lock_provenance()`
was reserved for: it gathers doctor checks/versions, the compatibility
baseline, the exact Stage 5 lock provenance (when `workspace` is given), a
module inventory, an optional sanitized resolved config, and optional
probe/port summaries — redacting absolute paths, credentialed URLs, tokens,
and device serials by default (see `helia_profiler.redact`) — into one
in-memory `SupportBundleCollection`. `write_support_bundle()` archives it
deterministically (stable member order and byte content for identical
inputs); `verify_support_bundle()` re-checks an archive's structure and
per-member digests, rejecting unsafe or disallowed member paths. Every
section is collected best-effort: a missing workspace, config, or optional
tool marks just that section unavailable with a reason instead of failing
the whole bundle.

::: helia_profiler.SupportBundleOptions

::: helia_profiler.collect_support_bundle

::: helia_profiler.write_support_bundle

::: helia_profiler.verify_support_bundle

::: helia_profiler.SupportBundleManifest

::: helia_profiler.SupportBundleSection

## Validity and comparability

The same pure policy functions drive manifests, summary output, comparisons,
and programmatic consumers. Invalid runs and model mismatches block run-level
deltas. Topology differences suppress only per-layer deltas. Power scope,
mode, firmware, or integrity differences suppress only power metrics, while
intentional engine (type and measured runtime version), toolchain, clock,
board, transport, and placement changes remain informative comparison
dimensions.

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
