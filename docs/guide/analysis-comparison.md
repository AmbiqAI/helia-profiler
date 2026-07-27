# Analysis and Run Comparison

Profiling answers what happened on a board. Two related workflows help before
and after that measurement:

- `hpx analyze` inspects a model on the host without hardware.
- `hpx compare` compares two completed, compatible profile result bundles.

## Analyze a model without hardware

Install the analysis dependencies once:

```bash
pip install 'helia-profiler[analysis]'
```

Then inspect the raw LiteRT graph:

```bash
hpx analyze model.tflite
hpx analyze model.tflite --format json --output analysis.json
```

The report includes per-operator MACs, parameter counts, and tensor sizes.
`--engine helia-rt` evaluates the original graph as the interpreter sees it.
With the AOT extra installed, `--engine helia-aot` compiles and analyzes the
transformed graph; add `--compare` to show original and transformed graphs
side by side:

```bash
pip install 'helia-profiler[aot]'
hpx analyze model.tflite --engine helia-aot --compare
```

This is useful for checking operator coverage and graph transforms before
starting a firmware build. See the [`hpx analyze` reference](../reference/analyze.md)
for every option.

## Compare two profile runs

Keep each run in a separate output directory:

```bash
hpx profile model.tflite --engine helia-rt \
  --output-dir results/rt
hpx profile model.tflite --engine helia-aot \
  --output-dir results/aot
hpx compare results/rt results/aot \
  --output-dir results/rt-vs-aot
```

HPX verifies declared result-manifest paths, sizes, and SHA-256 digests before
reading a bundle. It then applies typed comparability rules:

- invalid results or different model hashes block the comparison;
- different layer topology suppresses only per-layer deltas;
- incompatible power scope or integrity suppresses only power deltas;
- engine, toolchain, board, clock, transport, and placement differences remain
  visible as experimental dimensions.

The terminal highlights totals and the largest layer deltas.
`--output-dir` also writes `compare_summary.json` and `layer_diff.csv`.

## Add a regression policy

A versioned comparison profile turns selected metrics into a deterministic
pass, warning, or failure:

```json title="regression-profile.json"
{
  "schema": "hpx.comparison-profile",
  "schema_version": 1,
  "name": "apollo510-runtime",
  "required_dimensions": ["board", "cpu_clock"],
  "metrics": {
    "total_cycles": {
      "direction": "smaller",
      "unit": "cycles",
      "max_regression_pct": 3.0
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
hpx compare results/baseline results/change \
  --profile regression-profile.json \
  --output-dir results/regression
```

Profiles support `smaller`, `larger`, and `equal` directions, percentage and
absolute tolerances, required configuration dimensions, and explicit policy
for missing metrics. A failed regression returns a non-zero exit status. See
the [`hpx compare` reference](../reference/compare.md) for the full contract.
