# Toolchain Comparison

Run the same model and engine through GCC, armclang, and ATfE, then
compare cycle counts.

## Goal

Isolate the effect of toolchain choice on inference latency. Engine,
counters, transport, and board are held constant; only `--toolchain`
changes between runs.

## Prerequisites

- All three toolchains installed and on `PATH`
  ([Toolchains](../guide/toolchains.md)).
- A model, e.g. the bundled `examples/quickstart/kws_model.tflite`.
- Apollo510 EVB connected via J-Link.

Run `hpx doctor` first to confirm armclang / atfe show ✓.

## Run all three

Three commands, three result directories:

```bash
MODEL=examples/quickstart/kws_model.tflite

hpx profile $MODEL --engine helia-rt --toolchain gcc      \
    --output-dir results_compare/rt_gcc

hpx profile $MODEL --engine helia-rt --toolchain armclang \
    --output-dir results_compare/rt_armclang

hpx profile $MODEL --engine helia-aot --toolchain gcc      \
    --engine-config configs/aot.yml \
    --output-dir results_compare/aot_gcc

hpx profile $MODEL --engine helia-aot --toolchain armclang \
    --engine-config configs/aot.yml \
    --output-dir results_compare/aot_armclang
```

(`configs/aot.yml` should set `cmsis_nn_path`. See
[Engines → heliaAOT](../guide/engines.md#heliaaot).)

## Compare summaries

```bash
for dir in results_compare/*/; do
  total=$(jq -r '.total_cycles' "$dir/summary.json")
  printf "%-30s %12.0f cycles\n" "$(basename $dir)" "$total"
done
```

Reference numbers from this repo's `results/results_*`:

| Run | Total cycles | vs heliaRT/GCC |
|---|---|---|
| heliaRT + gcc | 2,014,841 | 1.00× |
| heliaRT + armclang | 1,874,429 | 0.93× |
| heliaAOT + gcc | 1,965,501 | 0.98× |
| heliaAOT + armclang | 1,869,210 | 0.93× |

Two takeaways on this model:

- **armclang gives a consistent ~5–7% speedup** over GCC, both for
  heliaRT and heliaAOT.
- **heliaAOT is only marginally faster than heliaRT** for this small
  KWS model — the gap is roughly the same as the toolchain gap. Bigger
  AOT wins typically appear on convolution-heavy models with larger
  feature maps.

## Per-layer diff

To see where the toolchain difference comes from, line up the per-layer
CSVs:

```bash
paste -d, \
    <(cut -d, -f2,3 results_compare/rt_gcc/profile_results.csv) \
    <(cut -d, -f3   results_compare/rt_armclang/profile_results.csv) \
  | head
```

The CONV_2D layers usually account for most of the spread; reduction and
softmax layers are largely identical across toolchains.

## What this is not

This example does **not** isolate the engine choice from the toolchain
choice — it varies both. To isolate the engine effect, hold toolchain
constant and only flip `--engine`. To isolate placement effects,
also hold `--model-location` constant.

## Next

- [Engine Comparison](engine-comparison.md) — engine-only sweep.
- [Toolchains](../guide/toolchains.md) — install steps and trade-offs.
- [Per-layer breakdown](per-layer.md) — drill into individual ops.
