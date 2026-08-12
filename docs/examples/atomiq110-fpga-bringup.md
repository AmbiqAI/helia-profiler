# Atomiq110 FPGA Bring-up (No Power)

**Goal:** prove `hpx` works end-to-end on `atomiq110_fpga_turbo` with
`helia-rt` and Ethos-U85 platform firmware support, using PMU-only profiling.

## Setup

Use the checked-in config:

```yaml title="examples/quickstart/hpx_rt_atomiq110_fpga.yml"
model:
  path: examples/quickstart/kws_model.tflite
  arena_size: 131072
  arena_location: tcm
  weights_location: mram

engine:
  type: helia-rt
  config:
    variant: release-with-logs

target:
  board: atomiq110_fpga_turbo
  toolchain: arm-none-eabi-gcc
  transport: rtt

profiling:
  pmu_counters:
    cpu: default
    memory: default
    mve: default
  per_layer: true
  iterations: 5
  warmup: 2

power:
  enabled: false

output:
  format: csv
  dir: ./results/atomiq110_fpga_rt
```

Preflight (recommended before first run):

```bash
hpx probes list
hpx probes match --board atomiq110_fpga_turbo
hpx ports list --all
hpx target reset --board atomiq110_fpga_turbo --kind debug
```

## Run

From any directory:

```bash
uv --directory /path/to/helia-profiler run hpx profile \
  --config /path/to/helia-profiler/examples/quickstart/hpx_rt_atomiq110_fpga.yml
```

Repeatability check (3 runs):

```bash
for i in 1 2 3; do
  uv --directory /path/to/helia-profiler run hpx profile \
    --config /path/to/helia-profiler/examples/quickstart/hpx_rt_atomiq110_fpga.yml \
    --output-dir /path/to/helia-profiler/results/atomiq110_fpga_rt_run${i}
done
```

## What you get

Each run writes:

| File | What to check |
|---|---|
| `summary.json` | `engine: helia-rt`, non-zero `total_cycles`, stable totals run-to-run |
| `profile_results.csv` | per-layer rows exist, counters are non-zero for active ops |
| `run_metadata.json` | board/toolchain/transport resolved as expected |

For FPGA bring-up, treat PMU-only success as:

1. Build/flash/capture completes without manual intervention.
2. Per-layer PMU output is produced on every run.
3. No intermittent RTT/capture failures across repeated runs.

## Where to go deeper

- [Boards & Platforms](../guide/boards.md) — details for `atomiq110_fpga_turbo`.
- [Validating a Board Setup](../guides/validating-a-board-setup.md) — scaling
  from single-case smoke to wider matrices.
