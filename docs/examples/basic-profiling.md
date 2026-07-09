# Basic Profiling

**Goal:** profile a TFLite model with default settings to get total cycle
counts and a per-layer breakdown.

## Setup

The minimal command needs only a model and a board — it uses the default
engine (heliaRT), default PMU counters (`basic_cpu`), and writes results to
`./results/`:

```bash
hpx profile my_model.tflite --board apollo510_evb
```

For repeatable runs, use a YAML config instead:

```yaml title="hpx_basic.yml"
model:
  path: my_model.tflite
  arena_size: 65536            # 64 KB — adjust for your model

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
  per_layer: true
  iterations: 5
  warmup: 2

output:
  format: csv
  dir: ./results/basic
```

## Run

```bash
hpx profile --config hpx_basic.yml
```

## What you get

```
============================================================
heliaPROFILER Results
============================================================
  arena_size: 65536
  allocated_arena: 29780
  model_size: 53936
  layers: 13
  total_cycles: 2,016,376

  Top layers by cycles:
    CONV_2D                           338,176 ( 16.8%)
    DEPTHWISE_CONV_2D                 206,245 ( 10.2%)
    CONV_2D                           207,749 ( 10.3%)
============================================================
```

| File | What to look at |
|---|---|
| `summary.json` | Total cycles, top layers, memory usage |
| `profile_results.csv` | Open in a spreadsheet — sort by `cycles` column |
| `run_metadata.json` | Verify board, toolchain, model hash |

!!! tip "Arena sizing"
    If the firmware reports OOM, increase `arena_size`. The `allocated_arena`
    field in `summary.json` shows actual usage — set `arena_size` to at least
    1.5× that value.

## Where to go deeper

- [Configuration](../guide/configuration.md) — full YAML schema and CLI merge rules.
- [Output & Results](../guide/output.md) — every result file and field.
- [Per-Layer Breakdown](per-layer.md) — capture more PMU counters per layer.
