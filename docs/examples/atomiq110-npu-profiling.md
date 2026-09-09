# Atomiq110 NPU Profiling (Ethos-U85)

**Goal:** profile a Vela-compiled model on the Ethos-U85 NPU on
`atomiq110_fpga_turbo`, capturing both ARM PMU counters and the NPU's own
PMU (`ethos_npu` group) per layer.

## Prerequisites

- The `analysis` extra (`pip install 'helia-profiler[analysis]'`) — preflight
  validates the model's Vela accelerator config against the target NPU, which
  needs `ai-edge-litert`.
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

`hpx` cross-checks model and config: `engine.backend: ethos_u` without a
Vela model is always rejected. The reverse check (a Vela model on a CPU
backend) needs the `analysis` extra listed above; without it the mismatch
surfaces at runtime as an unresolved `ethos-u` custom op.

## Setup

Use the checked-in config:

```yaml title="examples/quickstart/hpx_rt_npu_atomiq110.yml"
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

Use `ethos_npu: all` for the full 9-event catalogue (runs extra passes),
or list explicit `ETHOSU_PMU_*` names.

## heliaAOT variant

The same profile also runs with the ahead-of-time engine: heliaAOT compiles
the Vela `ethos-u` op into a generated NPU kernel instead of dispatching it
through the interpreter. Use the checked-in
`examples/quickstart/hpx_aot_npu_atomiq110.yml`, which differs only in the
engine block:

```yaml
engine:
  type: helia-aot
  backend: ethos_u
```

With the NPU backend, automatic placement defaults NPU-visible buffers to
SRAM/MRAM; explicit `tcm` placement is honored (the NPU reaches TCM through
the M55's AHB slave port while the core is awake).

## Run

```bash
uv --directory /path/to/helia-profiler run hpx profile \
  --config examples/quickstart/hpx_rt_npu_atomiq110.yml
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

- [Configuration Reference](../reference/configuration.md) — `pmu_counters`
  groups and `engine.backend`.
