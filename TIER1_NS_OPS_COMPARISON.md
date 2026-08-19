# ExecuTorch Tier-1 ns-ops comparison (arm vs ns-cmsis-nn)

Hardware measurements captured 2026-08-19 on an Apollo510 EVB (Cortex-M55,
96 MHz LP clock, J-Link 1160003180, RTT transport), 25 iterations / 5 warmup,
median aggregation, CPU+MVE+memory PMU presets, all runs `validity: valid`.

Runtime: nsx-executorch `main` @ `4a257def0c3ebd4ecd6a5d412f087d297f1b3492`
(the PR #2 merge adding out-of-tree `cortex_m_ns::` Tier-1 operators), pinned
ExecuTorch submodule `3a97429` (v1.3.0), arm-cmsis-nn `6d21a6f`, ns-cmsis-nn
v7.29.2 `6317264`. The HPX compatibility baseline was re-pinned to this
commit in this working tree.

## Models

Both models are deterministic random-weight int8 exports of the SAME PyTorch
module per pair, produced by `tools/export/executorch_tier1/export_tier1.py`
via the new `nsx_cortex_m.export(kernel_provider=...)` AOT package:

- **tier1** — channels_last conv trunk (16x32x32 activations):
  conv → hardswish → conv → leaky_relu → sub → relu.
  - arm PTE: `aten::leaky_relu.out`, `aten::sub.out`, `aten::clamp.out`
    (the relu) run as float portable kernels inside a dq…q island; hardswish
    uses the stock `cortex_m::minimum` + `cortex_m::quantized_mul`
    decomposition.
  - ns PTE: `cortex_m_ns::quantized_hardswish/leaky_relu/sub/relu`, zero
    portable ops.
- **tier1mean** — contiguous-layout micro model: `mean(dim=(2,3), keepdim)`.
  - arm PTE: float portable `aten::mean.out`; ns PTE:
    `cortex_m_ns::quantized_mean`.
  - mean is excluded from the conv trunk because the ns qualifier rejects
    channels_last input AND the portable `mean.out` kernel hard-fails
    `Method::execute()` with InvalidArgument (error 18) on channels_last
    data, on both providers.

Configs: `configs/executorch/tier1_{arm,ns}.yaml`,
`configs/executorch/tier1mean_{arm,ns}.yaml` (ns runs set
`engine.config.ns_ops: true` → `NSX_EXECUTORCH_ENABLE_NS_OPS=ON`).

## Kernel-registration verification

Three independent checks confirm the right kernels execute:

1. **Link-time symbols** (`tools/verify_executorch_kernels.py`, via
   `arm-none-eabi-nm` on the built firmware):
   - arm builds: portable registration table + named
     `torch::executor::native::` kernels; NO `arm_convolve_weight_sum`, NO
     `cortex_m_ns` symbols.
   - ns builds: ns-cmsis-nn v7.29.2 weight-sum ABI
     (`arm_convolve_weight_sum`, `arm_convolve_s8_get_weights_sum_size`) and
     the `cortex_m_ns::native::quantized_*_out` kernels (in the mean build
     the wrapper is inlined by size optimization, but the backing
     `arm_mean_s8` / `arm_mean_reduce_spatial_mve_s8` ns kernels are
     present).
2. **Load-time fail-fast contract**: a PTE containing `cortex_m_ns::` ops
   fails `Method::load()` with "operator missing" on any build without
   `NSX_EXECUTORCH_ENABLE_NS_OPS=ON`; every ns run loading successfully is
   itself proof of registration. Same for the selective portable list — the
   arm runs only load because `NSX_EXECUTORCH_PORTABLE_SELECT_OPS_LIST`
   registered exactly the PTE's aten fallbacks.
3. **Per-instruction PMU signature**: the instruction streams (recorded in
   `examples/models/tier1/tier1_manifest.json`) match the executed layer
   counts exactly (12 arm / 8 ns instructions for tier1; 5 / 3 for
   tier1mean).

## Results — tier1 (conv trunk)

| # | arm op | arm cycles | ns op | ns cycles |
|---|---|---:|---|---:|
| 0 | cortex_m::quantize | 92,689 | cortex_m::quantize | 92,831 |
| 1 | cortex_m::quantized_conv2d | 1,005,563 | cortex_m::quantized_conv2d | 1,001,585 |
| 2 | cortex_m::minimum (hardswish) | 8,443 | cortex_m_ns::quantized_hardswish | 153,135 |
| 3 | cortex_m::quantized_mul (hardswish) | 137,958 | cortex_m::quantized_conv2d | 1,000,661 |
| 4 | cortex_m::dequantize | 51,605 | cortex_m_ns::quantized_leaky_relu | 144,761 |
| 5 | cortex_m::quantized_conv2d | 1,004,539 | cortex_m_ns::quantized_sub | 222,067 |
| 6 | cortex_m::dequantize | 52,057 | cortex_m_ns::quantized_relu | 95,050 |
| 7 | aten::leaky_relu (portable, float) | 43,743 | cortex_m::dequantize | 52,226 |
| 8 | aten::sub (portable, float) | 560,030 | | |
| 9 | cortex_m::quantize | 86,982 | | |
| 10 | aten::clamp = relu (portable) | 413,279 | | |
| 11 | cortex_m::dequantize | 52,519 | | |

| metric | arm | ns | delta |
|---|---:|---:|---:|
| clean E2E cycles | 3,507,826 | 2,761,860 | **-21.3%** |
| Tier-1 op region* | 1,156,091 | 461,878 | **-60.0% (2.5x)** |
| planned arena | 196,608 B | 98,304 B | -50% (no float intermediates) |

\* arm region = dq + leaky_relu + sub + q + clamp (the float island the
portable fallbacks force); ns region = leaky_relu + sub + relu.

Convolutions are effectively identical across providers (<0.5%), confirming
the delta comes from the Tier-1 ops, not the conv path.

## Results — tier1mean (mean micro model)

| # | arm op | arm cycles | ns op | ns cycles |
|---|---|---:|---|---:|
| 0 | cortex_m::quantize | 87,874 | cortex_m::quantize | 92,963 |
| 1 | cortex_m::dequantize | 53,439 | cortex_m_ns::quantized_mean | **4,234** |
| 2 | aten::mean (portable, float) | **741,211** | cortex_m::dequantize | 740 |
| 3 | cortex_m::quantize | 866 | | |
| 4 | cortex_m::dequantize | 776 | | |

| metric | arm | ns | delta |
|---|---:|---:|---:|
| clean E2E cycles | 883,076 | 97,848 | **-88.9% (9.0x)** |
| mean op alone | 741,211 | 4,234 | **-99.4% (175x)** |

The ns kernel is MVE-vectorized (`arm_mean_reduce_spatial_mve_s8`) and runs
on int8 in place, while the portable path dequantizes 16K values to float,
reduces scalar-by-scalar, and requantizes.

## Reproduction

```sh
# Export PTEs (deterministic, seed 20260819)
PYTHONPATH=$NSX_EXECUTORCH/external/executorch/src:$NSX_EXECUTORCH/aot \
  python tools/export/executorch_tier1/export_tier1.py --output-dir examples/models/tier1

# Profile each side
hpx profile --config configs/executorch/tier1_arm.yaml --jlink-serial 1160003180
hpx profile --config configs/executorch/tier1_ns.yaml  --jlink-serial 1160003180

# Verify linked kernels against the declared flavor
python tools/verify_executorch_kernels.py --provider arm --expect-portable <arm hpx_profiler ELF>
python tools/verify_executorch_kernels.py --provider ns --ns-ops <ns hpx_profiler ELF>

# Cross-PTE per-instruction comparison (hpx compare enforces same-model SHA)
python tools/export/executorch_tier1/compare_tier1.py \
  --manifest examples/models/tier1/tier1_manifest.json \
  --model tier1 --baseline results/tier1_arm --candidate results/tier1_ns
```

## Known issues found along the way

- `hpx profile` exits 0 even when the firmware reports
  `HPX_ERROR=executorch stage=execute error=18` and no results are written.
- nsx-executorch `ExportResult.portable_fallback_ops` under-reports (misses
  `aten::relu.out` / `aten::leaky_relu.out` edge spellings); derive
  `portable_ops` from the serialized PTE operator list instead.
- `hpx compare` cannot compare cross-PTE (arm vs ns) runs by design; a
  source-model-aware opt-in would make this workflow first-class.
- Portable `aten::mean.out` cannot execute on channels_last tensors
  (InvalidArgument at execute), so a channels_last graph that keeps mean
  portable is unrunnable on BOTH providers — models must keep mean on
  contiguous tensors or lower it to `cortex_m_ns::quantized_mean`.
