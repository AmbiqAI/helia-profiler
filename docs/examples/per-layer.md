# Per-Layer Breakdown

**Goal:** capture every available PMU counter (CPU, memory, MVE) for each
layer in a model, not just the default `basic_cpu` set.

## Setup

```yaml title="hpx_full.yml"
model:
  path: my_model.tflite
  arena_size: 131072

engine:
  type: helia-rt
  config:
    variant: release-with-logs
    dist_path: path/to/helia_rt_v1_7_0

target:
  board: apollo510_evb

profiling:
  pmu_counters:
    cpu: all
    memory: all
    mve: all
  per_layer: true
  iterations: 5
  warmup: 2

output:
  format: csv
  dir: ./results/full_sweep
  model_explorer: true
  detailed: true             # get per-preset CSVs and memory.json
```

## Run

```bash
hpx profile --config hpx_full.yml
```

Requesting `all` for three groups runs multiple PMU passes to cover every
counter — see [Multi-pass profiling](../guide/pmu-counters.md#multi-pass-profiling)
for how passes are scheduled and merged.

## What you get

```bash
cat results/full_sweep/summary.json | python -m json.tool
```

```json
{
  "layers": 13,
  "total_cycles": 2016376,
  "cache": {
    "l1d_hit_rate_pct": 91.2
  },
  "top_layers": [
    {"op": "CONV_2D", "cycles": 338176, "pct": 16.8}
  ]
}
```

`profile_results.csv` has one row per layer with every requested counter as
a column. With `--detailed`, `detailed/memory.json` adds per-layer cache
counters and arena allocation.

## Where to go deeper

- [PMU Counters](../guide/pmu-counters.md) — counter groups, aggregation,
  derived metrics (L1D hit rate, MVE instruction share), and how to read
  `profile_results.csv` with pandas.
- [Model Explorer Overlays](../guide/model-explorer.md) — visualize
  per-layer hot spots on the model graph.
- [Basic Profiling](basic-profiling.md) — the minimal-counter starting point.
