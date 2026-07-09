# `hpx compare`

Compare two hpx result directories.

## Synopsis

```bash
hpx compare BASELINE CANDIDATE [--output-dir DIR] [--top-layers N]
```

## Description

Compares two completed profile runs — for example the same model built
with two different toolchains, engines, or memory placements — and
reports totals and the largest per-layer deltas.

Both arguments are hpx result directories (the `--output-dir` of a
previous `hpx profile` run).

## Options

| Flag | Description |
| --- | --- |
| `--output-dir` | Write `compare_summary.json` and `layer_diff.csv` to this directory. |
| `--top-layers` | Number of per-layer deltas to show in terminal output (default: 10). |

## Examples

```bash
hpx compare results/rt_gcc results/rt_atfe
hpx compare results/rt results/aot --output-dir results/rt_vs_aot
```

For an end-to-end walkthrough, see
[Engine Comparison](../examples/engine-comparison.md) and
[Toolchain Comparison](../examples/toolchain-comparison.md).
