# Validation Bundle Comparison

`hpx validate` produces a portable validation bundle containing
`validation_manifest.json` and one artifact directory per validation case.
Two completed bundles can be compared without hardware:

```bash
hpx compare results/baseline-validation results/candidate-validation \
  --validation \
  --output-dir results/validation-compare
```

The output directory is required and must be empty.

## Architecture

Bundle comparison is a thin validation-specific layer:

1. Load and validate both manifests.
2. Resolve artifact paths relative to each bundle root.
3. Match cases by structured identity.
4. Reuse the existing run-level `compare_runs()` implementation for eligible
   pairs.
5. Write validation-level JSON, Markdown, and per-case comparison artifacts.

It does not invoke profiling, pytest, NSX, probes, or target hardware.

## Manifest Contract

New validation runs emit manifest schema v2. The comparison loader also accepts
schema v1 with compatibility warnings; unknown versions are rejected.

Schema v2 records:

- explicit case identity and repeat attempt;
- requested memory and power configuration;
- validation health issues;
- artifact paths and availability;
- available model, compiler, and effective-placement provenance.

Manifest artifact paths must be relative. Absolute paths, traversal with `..`,
and symlink escapes outside the bundle root are rejected.

## Matching

The identity key contains:

- model ID;
- engine;
- board;
- normalized toolchain ID;
- transport;
- requested memory configuration;
- requested power configuration;
- repeat attempt.

Repeats match by exact attempt number. Extra attempts are reported as
`baseline_only` or `candidate_only`.

For schema v1, the loader infers an attempt only from a terminal `-runNN`
case-ID suffix and otherwise uses attempt 1. This inference is reported as a
compatibility warning.

J-Link serials, repository and model SHAs, exact compiler versions, and
effective memory placement are provenance rather than identity. Their
differences remain visible in comparison results.

## Eligibility and Outcomes

A matched pair is eligible when both validation statuses are `pass` and all
required run artifacts exist and parse. Recorded health issues are warnings,
not eligibility gates.

Each case has one explicit outcome:

- `compared`;
- `baseline_only` or `candidate_only`;
- `failed` or `skipped`;
- `ineligible` for missing required artifacts;
- `compare_error` for a case-local artifact parsing/comparison error.

Case-local outcomes do not make the command fail. Malformed or unsafe bundles
and top-level output/orchestration errors return a nonzero exit code.
Performance deltas are informational until threshold policy is added.

## Comparison Semantics

Metric deltas are candidate minus baseline. Power metrics are included when
available. If layer counts or operation sequences differ, run-level metrics are
still reported but per-layer deltas are omitted because index alignment would
be misleading.

## Outputs

```text
validation-compare/
├── validation_compare.json
├── validation_compare.md
└── case_compares/
    └── <case-identity>-attemptNN/
        ├── compare_summary.json
        └── layer_diff.csv
```

`validation_compare.json` is the stable machine-readable contract.
`validation_compare.md` is a concise human report. Generated artifact
references are relative so the comparison directory can be moved or published.

The output directory is never merged with existing content or overwritten.
It must also be outside both input bundles so comparison cannot mutate its
inputs.

## Deferred Policy

This implementation intentionally excludes:

- regression thresholds;
- automatic baseline selection or download;
- GitHub Actions workflow changes;
- dashboards;
- statistical aggregation across repeats.
