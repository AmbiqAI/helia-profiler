# `hpx compare`

Compare two hpx result directories.

## Synopsis

```bash
hpx compare BASELINE CANDIDATE [--profile FILE] [--output-dir DIR] [--top-layers N]
```

## Description

Compares two completed profile runs — for example the same model built
with two different toolchains, engines, or memory placements — and
reports totals and the largest per-layer deltas.

Both arguments are hpx result directories (the `--output-dir` of a
previous `hpx profile` run).

When `result_manifest.json` is present, `hpx compare` verifies every declared
artifact's path, size, and SHA-256 digest before reading results. Legacy result
directories without a manifest remain supported during the pre-1.0 transition.
Per-layer results may come from either `profile_results.csv` or
`profile_results.json`.

Comparison applies typed compatibility policy before calculating deltas:

- invalid results and different model hashes block the comparison;
- different layer topology suppresses only per-layer deltas;
- incompatible power scope, mode, firmware, or integrity suppresses only power
  metrics;
- engine, toolchain, board, clock, transport, and placement differences remain
  visible as informative dimensions.

`compare_summary.json` includes the structured compatibility issues and the
run, layer, and power comparability decisions, so automation does not need to
parse warning text.

## Options

| Flag | Description |
| --- | --- |
| `--output-dir` | Write `compare_summary.json` and `layer_diff.csv` to this directory. |
| `--profile` | Apply a versioned JSON comparison profile and emit a regression verdict. |
| `--top-layers` | Number of per-layer deltas to show in terminal output (default: 10). |

## Comparison profiles

A comparison profile is deterministic policy over metrics already present in
`CompareResult`. Version 1 intentionally does not invent repeat statistics or
aggregation that result bundles do not yet carry. Each selected metric defines:

- preferred direction: `smaller`, `larger`, or `equal`;
- required unit, including `""` for dimensionless values;
- optional percentage and absolute regression allowances;
- optional missing-metric behavior: `fail`, `warn`, or `ignore`.

Either tolerance may permit a change, so evaluation uses the larger absolute
allowance. Profiles may also require stable config dimensions such as `engine`,
`toolchain`, `board`, `cpu_clock`, or `pmu_counters`.

```json
{
  "schema": "hpx.comparison-profile",
  "schema_version": 1,
  "name": "runtime-smoke",
  "required_dimensions": ["board", "cpu_clock"],
  "metrics": {
    "total_cycles": {
      "direction": "smaller",
      "unit": "cycles",
      "max_regression_pct": 3.0,
      "max_regression_abs": 1000
    },
    "power.energy_per_inference_j": {
      "direction": "smaller",
      "unit": "J",
      "max_regression_pct": 5.0,
      "missing": "warn"
    }
  }
}
```

```bash
hpx compare results/baseline results/candidate \
  --profile regression-profile.json \
  --output-dir results/comparison
```

The verdict records the profile schema/version and SHA-256 of canonical profile
JSON, making CI and future web-tool decisions reproducible. `--profile` applies
to individual profile comparisons; validation-bundle profile policy remains a
separate follow-up.

## Examples

```bash
hpx compare results/rt_gcc results/rt_atfe
hpx compare results/rt results/aot --output-dir results/rt_vs_aot
```

For an end-to-end walkthrough, see
[Engine Comparison](../examples/engine-comparison.md) and
[Toolchain Comparison](../examples/toolchain-comparison.md).
