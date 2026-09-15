# Compile with Vela and Profile on the NPU

**Goal:** take a plain INT8 LiteRT (TFLite) model, compile it for the
Ethos-U85 NPU with Arm's Vela compiler using Ambiq's system configuration,
and profile the result on the Atomiq110 FPGA — no prior Ethos-U experience
assumed.

!!! warning "Experimental FPGA target"
    HPX support for Atomiq110 and `atomiq110_fpga_turbo` is best-effort and
    experimental — see
    [Atomiq110 NPU Profiling](atomiq110-npu-profiling.md) for the full
    caveats. FPGA measurements are not representative of production silicon.

A runnable notebook version of this recipe ships in the repo:
`examples/notebooks/vela_npu_demo.ipynb`.

## Ethos-U in 30 seconds

The Ethos-U85 is a neural processing unit driven by a *command stream*
compiled ahead of time. The compiler is **Vela**: it takes an INT8
quantized `.tflite` model, replaces every subgraph the NPU supports with a
single `ethos-u` custom operator containing the compiled command stream,
and leaves anything unsupported as ordinary CPU ops. At runtime, the
`ethos-u` op is dispatched to the NPU as one atomic unit; everything else
runs on the Cortex-M55.

Two inputs decide what Vela produces:

- `--accelerator-config` — the NPU variant (product + MACs/cycle). This is
  baked into the command stream and **must match the target**; HPX
  preflight rejects a mismatch before touching hardware. Atomiq110 is
  `ethos-u85-256`.
- `--config` + `--system-config` + `--memory-mode` — an `.ini` file
  describing the *system around the NPU* (clocks, memory ports,
  latencies). This shapes Vela's scheduling decisions and static
  performance estimates. It does not affect correctness.

## Prerequisites

- A checkout of this repo and a working `uv` — the model and configs used
  below are all checked in.
- An Atomiq110 FPGA board (see the
  [Atomiq110 recipe](atomiq110-npu-profiling.md) for bitstream
  prerequisites) with a J-Link probe attached.
- The `analysis` extra for HPX preflight validation:
  `pip install 'helia-profiler[analysis]'` (already present when running
  via `uv --directory <repo> run`).

Vela itself is **not** an HPX dependency — it is a model-preparation tool
you run once per model. No permanent install is needed:

```bash
uvx --from ethos-u-vela==4.5.0 vela --version   # prints 4.5.0
```

`uvx` downloads Vela into a cache and runs it without touching your
environment. If you prefer a persistent install, use
`uv tool install ethos-u-vela==4.5.0` or
`pip install ethos-u-vela==4.5.0`.

## The Ambiq system configuration

Ambiq's Vela settings live in a checked-in ini file:
`examples/quickstart/ambiq_vela.ini`. It declares three system configs —
the NPU clock tiers against the fixed 250 MHz SRAM/AXI memory fabric:

| Section | NPU clock | Use |
|---|---|---|
| `Ambiq_ULP_SRAM` | 100 MHz | ultra-low-power operating point |
| `Ambiq_LP_SRAM` | 250 MHz | low-power operating point (**demo default**) |
| `Ambiq_HP_SRAM` | 500 MHz | high-performance operating point |

and two memory modes:

| Section | Weights/consts | Tensor arena |
|---|---|---|
| `Sram_Only` | SRAM | SRAM |
| `Shared_Sram` | read-only Axi1 (MRAM) | SRAM (**demo default**) |

`Shared_Sram` matches the board reality: the model's weights stay in MRAM
and the working arena lives in SRAM shared with the M55 — the same
placement the HPX profile config requests (`weights_location: mram`,
`arena_location: sram`).

## Compile

Start from the checked-in INT8 KWS model:

```bash
cd /path/to/helia-profiler
uvx --from ethos-u-vela==4.5.0 vela \
  --accelerator-config ethos-u85-256 \
  --config examples/quickstart/ambiq_vela.ini \
  --system-config Ambiq_LP_SRAM \
  --memory-mode Shared_Sram \
  --output-dir ./vela_out \
  examples/quickstart/kws_model.tflite
```

Vela writes `./vela_out/kws_model_vela.tflite` plus a summary of its
static estimates. Two things to notice:

- The compiled model contains a single `CUSTOM(ethos-u)` op — the whole
  KWS graph fit on the NPU.
- The reported cycle/bandwidth numbers are *estimates from the ini's
  system model*, at the LP tier's 250 MHz. The FPGA runs everything at
  25 MHz, so treat estimates as relative guidance and the profiler as the
  source of truth.

## Profile

The checked-in profile config already points at a Vela-compiled KWS model;
point it at your fresh output:

```bash
uv --directory /path/to/helia-profiler run hpx profile \
  ./vela_out/kws_model_vela.tflite \
  --config examples/quickstart/hpx_rt_npu_atomiq110.yml
```

Successful boot logs `HPX_NPU=ethos-u85 init=ok`. The run produces
per-layer CSVs with two counter families:

- `ARM_PMU_*` — the Cortex-M55's view (cycles, instructions, stalls)
- `ETHOSU_PMU_*` — the NPU's own counters for the `ethos-u` dispatch,
  plus `NPU_DISPATCHED` marking layers that actually ran on the NPU

## What you get

```csv
"Layer","Op","ETHOSU_PMU_CYCLE","ETHOSU_PMU_NPU_ACTIVE","ETHOSU_PMU_MAC_ACTIVE","ETHOSU_PMU_SRAM_RD_DATA_BEAT_RECEIVED","NPU_DISPATCHED","overflow"
0,ethos-u,224128,191320,169227,61,1,0
```

Quick reads: `NPU_ACTIVE / CYCLE` is NPU utilization for the dispatch;
`MAC_ACTIVE / NPU_ACTIVE` separates compute-bound from memory-bound.
Remember the whole fused graph is **one** layer at runtime — for
intra-graph breakdowns use Vela's `--verbose-performance` estimates.

## Where to go deeper

- [Atomiq110 NPU Profiling](atomiq110-npu-profiling.md) — counter presets,
  heliaAOT variant, FPGA caveats.
- [PMU Counters guide](../guide/pmu-counters.md) — the full `ethos_npu`
  counter catalogue.
- [Configuration Reference](../reference/configuration.md) —
  `engine.backend` and `pmu_counters`.
