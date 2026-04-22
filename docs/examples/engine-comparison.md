# Engine Comparison

Compare the same model across heliaRT and heliaAOT to understand
performance and size trade-offs.

## Setup

You need two config files — one per engine. Both use the same model and board.

### heliaRT config

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
    memory: all
    mve: all
  per_layer: true
  iterations: 5
  warmup: 2

output:
  format: csv
  dir: ./results/comparison_rt
  detailed: true
```

### heliaAOT config

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
    memory: all
    mve: all
  per_layer: true
  iterations: 5
  warmup: 2

output:
  format: csv
  dir: ./results/comparison_aot
  detailed: true
```

## Run both profiles

```bash
hpx profile --config hpx_rt.yml
hpx profile --config hpx_aot.yml
```

## Compare results

### Quick comparison from summary.json

```python
import json

with open("results/comparison_rt/summary.json") as f:
    rt = json.load(f)
with open("results/comparison_aot/summary.json") as f:
    aot = json.load(f)

print(f"{'Metric':<25} {'heliaRT':>12} {'heliaAOT':>12} {'Δ':>8}")
print("-" * 60)
print(f"{'Total cycles':<25} {rt['total_cycles']:>12,.0f} {aot['total_cycles']:>12,.0f} {(aot['total_cycles']/rt['total_cycles']-1)*100:>7.1f}%")
print(f"{'Layers':<25} {rt['layers']:>12} {aot['layers']:>12}")

if "binary" in rt and "binary" in aot:
    print(f"{'Binary (text)':<25} {rt['binary']['text']:>12,} {aot['binary']['text']:>12,} {(aot['binary']['text']/rt['binary']['text']-1)*100:>7.1f}%")
    print(f"{'Binary (total)':<25} {rt['binary']['total']:>12,} {aot['binary']['total']:>12,} {(aot['binary']['total']/rt['binary']['total']-1)*100:>7.1f}%")

if "memory" in rt and "memory" in aot:
    print(f"{'Arena allocated':<25} {rt['memory'].get('allocated_arena',0):>12,} {aot['memory'].get('allocated_arena',0):>12,}")
```

### Example output

```
Metric                       heliaRT     heliaAOT        Δ
------------------------------------------------------------
Total cycles                2,016,376    2,026,920     0.5%
Layers                             13           13
Binary (text)                 573,968       63,052   -89.0%
Binary (total)                752,436       96,100   -87.2%
Arena allocated                29,780       14,336
```

### What to look for

| Metric | heliaRT | heliaAOT | Insight |
|---|---|---|---|
| **Binary size** | ~570 KB | ~96 KB | AOT eliminates interpreter overhead |
| **Total cycles** | ~2.0M | ~2.0M | Similar — both use CMSIS-NN kernels |
| **Arena usage** | 29 KB | 14 KB | AOT uses optimized memory planning |
| **Cache behavior** | Baseline | Different access patterns | Check per-layer `DTCM_ACCESS` |

!!! tip "When to use which"
    - **heliaRT** gives you the interpreter's flexibility with good performance
    - **heliaAOT** gives you the smallest binary and often the best memory
      utilization, at the cost of model-specific compilation
