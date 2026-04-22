# Quick Start

This guide walks through profiling a TFLite model end-to-end. You'll go from
a `.tflite` file to per-layer cycle counts in a single command.

## Prerequisites

- [Installed heliaPROFILER and toolchain](install.md)
- An Ambiq EVB connected via J-Link USB (e.g. Apollo510 EVB)

## 1. Check your setup

```bash
hpx doctor
```

All required tools should show ✓. If anything is missing, see [Installation](install.md).

## 2. Profile with defaults

```bash
hpx profile my_model.tflite --board apollo510_evb
```

This single command will:

1. Generate a profiler firmware app (NSX project)
2. Build it with `arm-none-eabi-gcc`
3. Flash it to the connected EVB
4. Capture PMU counter data over SWO
5. Write results to `./results/`

!!! tip
    If you don't have a model handy, the repo includes a test fixture:
    ```bash
    hpx profile tests/fixtures/kws_ref_model.tflite --board apollo510_evb
    ```

## 3. View results

Results are written to `./results/` by default:

```
results/
├── summary.json           # High-level totals (cycles, memory, cache)
├── profile_results.csv    # Per-layer PMU breakdown
├── run_metadata.json      # Config, toolchain, platform info
└── model_explorer/        # Model Explorer overlay JSONs
    ├── me_overlay_ARM_PMU_CPU_CYCLES.json
    ├── me_overlay_ARM_PMU_INST_RETIRED.json
    └── ...
```

Open `summary.json` for a quick overview:

```json
{
  "engine": "helia-rt",
  "layers": 13,
  "total_cycles": 2016376,
  "overflow_detected": false,
  "top_layers": [
    {"op": "CONV_2D", "cycles": 338176, "pct": 16.8},
    {"op": "CONV_2D", "cycles": 207749, "pct": 10.3}
  ],
  "memory": {
    "arena_size": 131072,
    "allocated_arena": 29780,
    "model_size": 53936
  }
}
```

## 4. Use a config file

For repeatable runs, create a YAML config:

```yaml title="hpx.yml"
model:
  path: my_model.tflite
  arena_size: 131072

engine:
  type: helia-rt

target:
  board: apollo510_evb

profiling:
  pmu_counters:
    cpu: all
    memory: all
  per_layer: true
  iterations: 5
  warmup: 2

output:
  format: csv
  dir: ./results
  model_explorer: true
```

```bash
hpx profile --config hpx.yml
```

## 5. Compare engines

Profile the same model with a different engine by changing one field:

=== "heliaRT"

    ```bash
    hpx profile my_model.tflite --engine helia-rt
    ```

=== "heliaAOT"

    ```bash
    hpx profile my_model.tflite --engine helia-aot
    ```

See [Engine Comparison](../examples/engine-comparison.md) for a detailed walkthrough.

## What's next?

- [Configuration](../guide/configuration.md) — full config reference
- [Engines](../guide/engines.md) — choosing between TFLM, heliaRT, heliaAOT
- [PMU Counters](../guide/pmu-counters.md) — understanding CPU, memory, and MVE events
- [Examples](../examples/index.md) — recipes for common scenarios
