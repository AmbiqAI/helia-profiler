# Per-Layer Breakdown

Analyze operator-level cycle counts, cache behavior, and MVE utilization
across all layers of a model.

## Full counter sweep

To capture every available PMU counter per layer:

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

```bash
hpx profile --config hpx_full.yml
```

This will run approximately 20 PMU passes to cover all 70+ counters.

## Analyzing the results

### 1. Start with the summary

```bash
cat results/full_sweep/summary.json | python -m json.tool
```

Look at `top_layers` to find hot spots, then check `cache.l1d_hit_rate_pct`
for memory efficiency.

### 2. Dive into per-layer data

Open `profile_results.csv` in a spreadsheet or use pandas:

```python
import pandas as pd

df = pd.read_csv("results/full_sweep/profile_results.csv")

# Top layers by cycle count
print(df.sort_values("cycles", ascending=False)[["op", "cycles"]].head())

# Cache efficiency per layer
df["l1d_hit_rate"] = 1 - df["ARM_PMU_L1D_CACHE_MISS_RD"] / df["ARM_PMU_L1D_CACHE_RD"]
print(df[["op", "l1d_hit_rate", "ARM_PMU_DTCM_ACCESS"]])
```

### 3. Check MVE utilization

```python
# MVE instruction mix per layer
mve_cols = [c for c in df.columns if "MVE" in c]
print(df[["op"] + mve_cols])

# What fraction of instructions are MVE?
df["mve_pct"] = df["ARM_PMU_MVE_INST_RETIRED"] / df["ARM_PMU_INST_RETIRED"] * 100
print(df[["op", "mve_pct"]])
```

### 4. Detailed memory breakdown

With `--detailed`, check `detailed/memory.json`:

```bash
cat results/full_sweep/detailed/memory.json | python -m json.tool
```

This shows per-layer cache counter values, arena allocation, and aggregate
cache totals with the derived L1D hit rate.

## Interpreting key metrics

| Metric | What it tells you |
|---|---|
| `cycles` | Total wall-clock cycles for the layer |
| `ARM_PMU_INST_RETIRED` | Instruction count — higher = more work |
| `ARM_PMU_STALL_BACKEND` | Cycles stalled on data dependencies / memory |
| `ARM_PMU_L1D_CACHE_MISS_RD` | Cache misses — triggers slow memory fetches |
| `ARM_PMU_DTCM_ACCESS` | TCM hits — fast on-chip SRAM accesses |
| `ARM_PMU_MVE_INST_RETIRED` | MVE/Helium instructions — vectorized computation |
| `ARM_PMU_MVE_STALL` | MVE pipeline stalls |

!!! tip "Look for stalls"
    High `STALL_BACKEND` relative to `cycles` means the layer is
    memory-bound. Check `L1D_CACHE_MISS_RD` and `BUS_ACCESS` to understand
    where the bottleneck is.
