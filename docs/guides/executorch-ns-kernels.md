# ExecuTorch NS Tier-1 Kernels

ExecuTorch PTEs exported for Cortex-M can be lowered against two CMSIS-NN
providers. The `arm` provider uses upstream arm-cmsis-nn and leaves ops
outside the stock `cortex_m::` set as **portable ATen fallbacks** — float
kernels wrapped in a dequantize/requantize island. The `ns` provider adds
Ambiq's out-of-tree `cortex_m_ns::` Tier-1 operators (sub, hardswish, mean,
relu/relu6/hardtanh/clamp, leaky_relu) backed by ns-cmsis-nn, so those ops
stay int8 end to end.

This page is the measured evidence for that difference. See
[Inference Engines](../guide/engines.md#ns-tier-1-kernels-ns_ops) for how to
turn the NS kernels on.

!!! info "Measurement provenance"
    Captured 2026-08-19 on an Apollo510 EVB (Cortex-M55, 96 MHz LP clock,
    RTT transport), 25 iterations / 5 warmup, median aggregation, CPU + MVE +
    memory PMU presets. Every run reported `validity: valid`.

    Runtime: nsx-executorch `main` @ `4a257def` (the PR #2 merge adding the
    out-of-tree `cortex_m_ns::` operators), ExecuTorch submodule `3a97429`
    (v1.3.0), arm-cmsis-nn `6d21a6f`, ns-cmsis-nn v7.29.2 `6317264`. Numbers
    are from one specific bench and one runtime pin — treat the *ratios* as
    the transferable result, not the absolute cycle counts.

## Models

Each pair is two deterministic random-weight int8 exports of the **same**
PyTorch module, produced by
`tools/export/executorch_tier1/export_tier1.py` through
`nsx_cortex_m.export(kernel_provider=...)`:

**`tier1`** — channels_last conv trunk (16×32×32 activations):
conv → hardswish → conv → leaky_relu → sub → relu.

- arm PTE: `aten::leaky_relu.out`, `aten::sub.out`, and `aten::clamp.out`
  (the relu) run as float portable kernels inside a dq…q island; hardswish
  uses the stock `cortex_m::minimum` + `cortex_m::quantized_mul`
  decomposition.
- ns PTE: `cortex_m_ns::quantized_hardswish/leaky_relu/sub/relu`, zero
  portable ops.

**`tier1mean`** — contiguous-layout micro model: `mean(dim=(2,3), keepdim)`.

- arm PTE: float portable `aten::mean.out`; ns PTE:
  `cortex_m_ns::quantized_mean`.
- mean is kept out of the conv trunk deliberately — see
  [Caveats](#caveats).

Configs live in `configs/executorch/tier1_{arm,ns}.yaml` and
`configs/executorch/tier1mean_{arm,ns}.yaml`; the `ns` runs set
`engine.config.ns_ops: true`. The `tier1_{arm,ns}_atfe.yaml` variants build
the same PTEs with ATfE (requires `ATFE_ROOT`), so a gcc-vs-ATfE toolchain
comparison is one pair of runs plus
`hpx compare results/tier1_arm results/tier1_arm_atfe`.

## Verifying the right kernels ran

Three independent checks, because "the build succeeded" does not prove which
kernels linked:

1. **Link-time symbols** — `tools/verify_executorch_kernels.py` runs
   `arm-none-eabi-nm` over the built firmware. `arm` builds show the portable
   registration table and named `torch::executor::native::` kernels, and *no*
   `arm_convolve_weight_sum` or `cortex_m_ns` symbols. `ns` builds show the
   ns-cmsis-nn v7.29.2 weight-sum ABI (`arm_convolve_weight_sum`,
   `arm_convolve_s8_get_weights_sum_size`) plus the
   `cortex_m_ns::native::quantized_*_out` kernels.
2. **Load-time fail-fast** — a PTE containing `cortex_m_ns::` ops fails
   `Method::load()` with "operator missing" on any build without
   `NSX_EXECUTORCH_ENABLE_NS_OPS=ON`. Every `ns` run that loads at all is
   itself proof of registration. The same holds for the selective portable
   list: the `arm` runs only load because
   `NSX_EXECUTORCH_PORTABLE_SELECT_OPS_LIST` registered exactly the PTE's
   ATen fallbacks.
3. **Per-instruction PMU signature** — the captured instruction streams match
   the operator counts recorded in
   `examples/models/tier1/tier1_manifest.json` exactly (12 arm / 8 ns
   instructions for `tier1`; 5 / 3 for `tier1mean`).

## Results — `tier1` (conv trunk)

| # | arm op | arm cycles | ns op | ns cycles |
|---|---|---:|---|---:|
| 0 | `cortex_m::quantize` | 92,689 | `cortex_m::quantize` | 92,831 |
| 1 | `cortex_m::quantized_conv2d` | 1,005,563 | `cortex_m::quantized_conv2d` | 1,001,585 |
| 2 | `cortex_m::minimum` (hardswish) | 8,443 | `cortex_m_ns::quantized_hardswish` | 153,135 |
| 3 | `cortex_m::quantized_mul` (hardswish) | 137,958 | `cortex_m::quantized_conv2d` | 1,000,661 |
| 4 | `cortex_m::dequantize` | 51,605 | `cortex_m_ns::quantized_leaky_relu` | 144,761 |
| 5 | `cortex_m::quantized_conv2d` | 1,004,539 | `cortex_m_ns::quantized_sub` | 222,067 |
| 6 | `cortex_m::dequantize` | 52,057 | `cortex_m_ns::quantized_relu` | 95,050 |
| 7 | `aten::leaky_relu` (portable, float) | 43,743 | `cortex_m::dequantize` | 52,226 |
| 8 | `aten::sub` (portable, float) | 560,030 | | |
| 9 | `cortex_m::quantize` | 86,982 | | |
| 10 | `aten::clamp` = relu (portable) | 413,279 | | |
| 11 | `cortex_m::dequantize` | 52,519 | | |

| Metric | arm | ns | Delta |
|---|---:|---:|---:|
| Clean E2E cycles | 3,507,826 | 2,761,860 | **−21.3%** |
| Tier-1 op region[^region] | 1,156,091 | 461,878 | **−60.0% (2.5×)** |
| Planned arena | 196,608 B | 98,304 B | −50% (no float intermediates) |

[^region]:
    arm region = dq + leaky_relu + sub + q + clamp — the float island the
    portable fallbacks force. ns region = leaky_relu + sub + relu.

Convolutions are effectively identical across providers (<0.5%), which
confirms the delta comes from the Tier-1 ops rather than the conv path.

## Results — `tier1mean` (mean micro model)

| # | arm op | arm cycles | ns op | ns cycles |
|---|---|---:|---|---:|
| 0 | `cortex_m::quantize` | 87,874 | `cortex_m::quantize` | 92,963 |
| 1 | `cortex_m::dequantize` | 53,439 | `cortex_m_ns::quantized_mean` | **4,234** |
| 2 | `aten::mean` (portable, float) | **741,211** | `cortex_m::dequantize` | 740 |
| 3 | `cortex_m::quantize` | 866 | | |
| 4 | `cortex_m::dequantize` | 776 | | |

| Metric | arm | ns | Delta |
|---|---:|---:|---:|
| Clean E2E cycles | 883,076 | 97,848 | **−88.9% (9.0×)** |
| `mean` op alone | 741,211 | 4,234 | **−99.4% (175×)** |

The ns kernel is MVE-vectorized (`arm_mean_reduce_spatial_mve_s8`) and works
on int8 in place. The portable path dequantizes 16K values to float, reduces
scalar-by-scalar, and requantizes.

## Reproducing

`$NSX_EXECUTORCH` is the qualified `nsx-executorch` checkout.

```bash
# 1. Export both PTEs (deterministic, seed 20260819)
PYTHONPATH="$NSX_EXECUTORCH/external/executorch/src:$NSX_EXECUTORCH/aot" \
  python tools/export/executorch_tier1/export_tier1.py \
    --output-dir examples/models/tier1

# 2. Profile each side
hpx profile --config configs/executorch/tier1_arm.yaml
hpx profile --config configs/executorch/tier1_ns.yaml

# 3. Verify the linked kernels match the declared provider
python tools/verify_executorch_kernels.py --provider arm --expect-portable <arm ELF>
python tools/verify_executorch_kernels.py --provider ns  --ns-ops         <ns ELF>

# 4. Per-instruction cross-PTE comparison
python tools/export/executorch_tier1/compare_tier1.py \
  --manifest examples/models/tier1/tier1_manifest.json \
  --model tier1 --baseline results/tier1_arm --candidate results/tier1_ns
```

Add `--jlink-serial <serial>` to step 2 if more than one probe is connected.

## Caveats

- **`mean` and channels_last.** The ns qualifier rejects channels_last input
  for `mean`, *and* the portable `aten::mean.out` kernel fails
  `Method::execute()` with `InvalidArgument` (error 18) on channels_last
  data. A channels_last graph that keeps `mean` portable is therefore
  unrunnable on **both** providers. Keep `mean` on contiguous tensors, or
  lower it to `cortex_m_ns::quantized_mean`.
- **Cross-PTE comparison.** `hpx compare` requires the same model SHA, so it
  will not compare an arm run against an ns run. Use
  `tools/export/executorch_tier1/compare_tier1.py`, which matches operators
  through the export manifest instead.
- **Portable-op reporting.** `nsx-executorch`'s
  `ExportResult.portable_fallback_ops` under-reports — it misses the
  `aten::relu.out` / `aten::leaky_relu.out` edge spellings. Derive the
  portable op list from the serialized PTE operator list instead.
