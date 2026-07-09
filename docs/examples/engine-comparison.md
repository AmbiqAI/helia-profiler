# Engine Comparison

**Goal:** run the same model through heliaRT and heliaAOT to compare binary
size, cycles, and memory usage.

## Setup

One config per engine, same model and board:

```yaml title="hpx_rt.yml"
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
  per_layer: true
  iterations: 5
  warmup: 2

output:
  format: csv
  dir: ./results/comparison_rt
  detailed: true
```

```yaml title="hpx_aot.yml"
model:
  path: my_model.tflite
  arena_size: 131072

engine:
  type: helia-aot
  config:
    prefix: hpx
    module_name: hpx_model
    cmsis_nn_path: path/to/ns-cmsis-nn

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
  dir: ./results/comparison_aot
  detailed: true
```

## Run

```bash
hpx profile --config hpx_rt.yml
hpx profile --config hpx_aot.yml
```

## What you get

```python
import json

rt = json.load(open("results/comparison_rt/summary.json"))
aot = json.load(open("results/comparison_aot/summary.json"))

print(f"{'Metric':<20} {'heliaRT':>12} {'heliaAOT':>12}")
print(f"{'Total cycles':<20} {rt['total_cycles']:>12,} {aot['total_cycles']:>12,}")
print(f"{'Binary (total)':<20} {rt['binary']['total']:>12,} {aot['binary']['total']:>12,}")
```

```
Metric                    heliaRT     heliaAOT
Total cycles            2,016,376    2,010,842
Binary (total)             752,436       96,100
```

| Metric | What to expect |
|---|---|
| Binary size | heliaAOT is typically much smaller — no interpreter, dispatch tables, or op resolver |
| Total cycles | Similar for models using the same CMSIS-NN kernels underneath |
| Arena usage | heliaAOT's per-tensor placement can pack the arena tighter than a single runtime arena |

!!! tip "Isolate the engine effect"
    Keep toolchain, board, and PMU counters identical across both configs so
    the only variable is the engine. See
    [Toolchain Comparison](toolchain-comparison.md) if you also want to vary
    the compiler.

## Where to go deeper

- [Inference Engines](../guide/engines.md) — heliaRT vs. heliaAOT trade-offs.
- [Output & Results](../guide/output.md) — every `summary.json` field.
- [Memory Placement](../guide/memory.md) — why arena/weights usage differs
  between engines.
