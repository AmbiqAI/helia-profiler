# Atomiq110 NPU Profiling (Ethos-U85)

**Goal:** profile a Vela-compiled model on the Ethos-U85 NPU on
`atomiq110_fpga_turbo`, capturing both ARM PMU counters and the NPU's own
PMU (`ethos_npu` group) per layer.

## Prerequisites

- An Atomiq110 FPGA image whose NPU bitstream matches the SDK generation
  used by `nsx-npu` (a mismatch fails NPU init at boot — the firmware
  reports `HPX_ERROR=npu_init_failed ... hint=bitstream_sdk_generation_mismatch`).
- A **Vela-compiled** model (`.tflite` containing `ethos-u` custom ops).
  A ready-to-use one ships in the repo
  (`examples/quickstart/kws_model_vela.tflite`, KWS micronet-m compiled for
  `ethos-u85-256`); compile your own with:

```bash
vela --accelerator-config ethos-u85-256 model_INT8.tflite
```

`hpx` cross-checks model and config: a Vela model without
`engine.backend: ethos_u` is rejected, and vice versa.

## Setup

Use the checked-in config:

```yaml title="examples/quickstart/hpx_npu_atomiq110_fpga.yml"
model:
  path: examples/quickstart/kws_model_vela.tflite
  arena_size: 524288
  arena_location: sram
  weights_location: mram

engine:
  type: helia-rt
  backend: ethos_u
  config:
    variant: release-with-logs

target:
  board: atomiq110_fpga_turbo
  toolchain: arm-none-eabi-gcc
  transport: rtt

profiling:
  pmu_counters:
    cpu: default
    ethos_npu: default
  per_layer: true
  iterations: 5
  warmup: 2

power:
  enabled: false

output:
  format: csv
  dir: ./results/atomiq110_npu
```

The `ethos_npu` default preset samples, per profiled layer:

| Counter | Meaning |
|---|---|
| `ETHOSU_PMU_CYCLE` | Total NPU cycles while the command stream runs |
| `ETHOSU_PMU_NPU_ACTIVE` | Cycles the NPU is active |
| `ETHOSU_PMU_MAC_ACTIVE` | Cycles the MAC engine is active |
| `ETHOSU_PMU_SRAM_RD_DATA_BEAT_RECEIVED` | SRAM read data beats |

Use `ethos_npu: all` for the full 8-event catalogue (runs a second pass),
or list explicit `ETHOSU_PMU_*` names.

## heliaAOT variant

The same profile also runs with the ahead-of-time engine: heliaAOT compiles
the Vela `ethos-u` op into a generated NPU kernel instead of dispatching it
through the interpreter. Use the checked-in
`examples/quickstart/hpx_aot_npu_atomiq110_fpga.yml`, which differs only in the
engine block:

```yaml
engine:
  type: helia-aot
  backend: ethos_u
```

With the NPU backend, arena and weights must stay NPU-reachable
(SRAM/MRAM/PSRAM): the Ethos-U is an AXI master that cannot access the
M55's TCMs, so `hpx` rejects explicit `tcm` placement and steers automatic
placement to SRAM/MRAM.

## Run

```bash
uv --directory /path/to/helia-profiler run hpx profile \
  --config examples/quickstart/hpx_npu_atomiq110_fpga.yml
```

Successful boot logs `HPX_NPU=ethos-u85 init=ok` before allocation.

## Reading the results

Vela fuses every supported subgraph into a single `ethos-u` custom op that
executes as one atomic NPU command stream. Consequences:

- A fully offloaded model shows **one** `CUSTOM(ethos-u)` layer carrying
  all NPU counters; CPU-resident layers show zeros in `ethos_npu` columns.
- Per-layer visibility *inside* a fused command stream does not exist at
  runtime — the counters are the NPU's aggregate view of that dispatch.
  Use Vela's `--verbose-performance` static estimates for intra-graph
  breakdowns.
- `ETHOSU_PMU_NPU_ACTIVE / ETHOSU_PMU_CYCLE` is the NPU utilization of the
  dispatch; `MAC_ACTIVE / NPU_ACTIVE` indicates compute- vs
  memory-boundedness.

## See also

- [Atomiq110 FPGA Bring-up](atomiq110-fpga-bringup.md) — CPU-only
  bring-up flow and probe/port preflight.
- [Configuration Reference](../reference/configuration.md) — `pmu_counters`
  groups and `engine.backend`.
