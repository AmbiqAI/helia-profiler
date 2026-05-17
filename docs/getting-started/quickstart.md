# Quick Start

Move from one-off CLI invocations to a YAML config you can check into git
and re-run later. This page assumes you've already completed
[your first profile](first-profile.md).

## Why a config file?

Two reasons:

- **Reproducibility** — record the exact engine, toolchain, counter
  selection, and iteration count next to the model.
- **Less typing** — long CLI invocations get tedious. The CLI is best for
  one-off overrides; YAML is best for the things that don't change.

The config file and CLI flags merge: anything in YAML is the baseline, and
any CLI flag you pass overrides the matching field. See
[Configuration](../guide/configuration.md) for the full schema.

## A minimal config

```yaml title="hpx.yml"
model:
  path: kws_model.tflite

engine:
  type: helia-rt

target:
  board: apollo510_evb
```

Run it:

```bash
hpx profile --config hpx.yml
```

Everything else falls back to defaults — RTT transport, GCC toolchain, CPU
counter defaults, 100 iterations, results to `./results/`.

## A real-world config

This is a fuller example annotated with what each field controls. Pick the
fields you care about; delete the rest.

```yaml title="hpx.yml"
model:
  path: kws_model.tflite
  arena_size: 131072            # (1)!
  model_location: auto          # (2)!

engine:
  type: helia-rt
  config:
    variant: release-with-logs  # (3)!

target:
  board: apollo510_evb
  toolchain: arm-none-eabi-gcc  # (4)!
  transport: rtt                # (5)!

profiling:
  pmu_counters:                 # (6)!
    cpu: default
    memory: default
  per_layer: true
  iterations: 100
  warmup: 5

output:
  format: csv
  dir: ./results
  model_explorer: true
```

1.  Tensor arena size in bytes. Required for TFLM/heliaRT. Set to ~1.5× the
    `allocated_arena` value reported in your first run's `summary.json`.
2.  `auto` (default — greedy fastest-fit), or pin explicitly to `tcm`,
    `sram`, `mram`, or `psram`. See [Memory Placement](../guide/memory.md).
3.  heliaRT library variant. `release-with-logs` keeps SWO printf available
    for debugging; `release` is leaner.
4.  Toolchain. See [Toolchains](../guide/toolchains.md) for `armclang` and
    `atfe` setup.
5.  Capture transport. See [Transports](../guide/transports.md).
6.  PMU counter selection. `default` = curated set (4 per group); `all` =
    every counter (multi-pass). Or list explicit counter names.

## Common workflows

### Compare two runs

Keep configs side-by-side, run each, point them at different output
directories:

```bash
hpx profile --config hpx_rt.yml      --output-dir results/rt
hpx profile --config hpx_aot.yml     --output-dir results/aot
```

Diff the `summary.json` files or open both `profile_results.csv` files side
by side in a spreadsheet.

### Override one field on the fly

Every config field is also a CLI flag:

```bash
hpx profile --config hpx.yml --iterations 10 --board apollo3p_evb
```

### Try a different toolchain

```bash
hpx profile --config hpx.yml --toolchain armclang
```

See [Toolchains](../guide/toolchains.md) for what each toolchain costs to
install and the cycle-count differences.

### Add power capture

```bash
hpx profile --config hpx.yml --power --power-duration 10
```

Requires a Joulescope JS110 or JS220. See
[Power Measurement](../guide/power.md).

## What's next?

- [Configuration](../guide/configuration.md) — full YAML schema reference
- [Inference Engines](../guide/engines.md) — RT vs AOT vs TFLM trade-offs
- [Toolchains](../guide/toolchains.md) — GCC vs armclang vs ATfE
- [Examples](../examples/index.md) — end-to-end recipes for common scenarios
