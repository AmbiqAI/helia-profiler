# Vanilla TFLM Baseline

**Goal:** profile the vanilla TFLM port as a baseline for comparison with
heliaRT and heliaAOT.

The `tflm` engine uses the `nsx-tflite-micro` NSX module. It is intentionally
separate from heliaRT: the baseline does not enable Ambiq's HELIA kernels.
Choose either the reference kernels or upstream CMSIS-NN with
`engine.backend`.

## Setup

Start with `examples/quickstart/hpx_tflm_baseline.yml`:

```yaml
engine:
  type: tflm
  backend: cmsis_nn    # reference or cmsis_nn
```

The bundled KWS model is used by default. Replace `model.path` with another
`.tflite` model and adjust `model.arena_size` if the first run reports a larger
arena requirement.

## Run

```bash
hpx profile --config examples/quickstart/hpx_tflm_baseline.yml
```

To compare the two TFLM kernel choices, make a copy of the config and change
`backend` to `reference`. Keep the model, placement, board, toolchain, and
profiling settings identical when comparing results.

## What you get

The output contains the same cycle, PMU, memory, and per-layer reports as the
other profiler engines. The result is a baseline measurement for the vanilla
TFLM port; it should not be interpreted as an Ambiq-optimized runtime result.

## Where to go deeper

- [Inference Engines](../guide/engines.md)
- [Configuration](../guide/configuration.md)
- [Engine Comparison](engine-comparison.md)
