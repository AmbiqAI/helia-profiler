# Inference Engines

!!! note "Under construction"
    This page will detail each supported engine and its trade-offs.

heliaPROFILER supports three inference engines. Each run uses exactly one.

| Engine | `--engine` value | Description |
|---|---|---|
| Stock TFLM | `tflm` | TensorFlow Lite Micro with CMSIS-NN kernels |
| heliaRT | `helia-rt` | Ambiq's optimized TFLM fork |
| heliaAOT | `helia-aot` | Ahead-of-time compiled model (no interpreter) |

## Choosing an Engine

- **tflm** — baseline reference, widest model coverage
- **helia-rt** — drop-in faster replacement for TFLM
- **helia-aot** — best performance, requires AOT compilation step
