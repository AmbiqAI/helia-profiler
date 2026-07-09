# Toolchain Comparison

**Goal:** run the same model/engine/board through GCC, armclang, and ATfE to
see how compiler choice affects cycle counts.

## Setup

No config file needed — pass `--toolchain` on the CLI and keep everything
else constant. Confirm all three toolchains are installed first (see
[Toolchains](../guide/toolchains.md)) and run `hpx doctor` to check the
baseline tool checks pass.

## Run

Six commands, six result directories — three toolchains × two engines:

```bash
MODEL=examples/quickstart/kws_model.tflite

hpx profile $MODEL --engine helia-rt --toolchain gcc      \
    --output-dir results_compare/rt_gcc

hpx profile $MODEL --engine helia-rt --toolchain armclang \
    --output-dir results_compare/rt_armclang

hpx profile $MODEL --engine helia-rt --toolchain atfe     \
    --output-dir results_compare/rt_atfe

hpx profile $MODEL --engine helia-aot --toolchain gcc      \
    --engine-config configs/aot.yml \
    --output-dir results_compare/aot_gcc

hpx profile $MODEL --engine helia-aot --toolchain armclang \
    --engine-config configs/aot.yml \
    --output-dir results_compare/aot_armclang

hpx profile $MODEL --engine helia-aot --toolchain atfe     \
    --engine-config configs/aot.yml \
    --output-dir results_compare/aot_atfe
```

(`configs/aot.yml` should set `cmsis_nn_path`. See
[Engines → heliaAOT](../guide/engines.md#heliaaot).)

## What you get

```bash
for dir in results_compare/*/; do
  total=$(jq -r '.total_cycles' "$dir/summary.json")
  printf "%-30s %12s cycles\n" "$(basename "$dir")" "$total"
done
```

```
rt_gcc                            2,014,841 cycles
rt_armclang                       1,874,429 cycles
rt_atfe                           1,881,203 cycles
aot_gcc                           1,965,501 cycles
aot_armclang                      1,869,210 cycles
aot_atfe                          1,872,977 cycles
```

(Illustrative — the relative gap between toolchains, not the absolute
counts, is what to look at. Run the commands above to get your own numbers
for your model and board.)

To see *where* the toolchain difference comes from, line up the per-layer
CSVs for one engine:

```bash
paste -d, \
    <(cut -d, -f2,3 results_compare/rt_gcc/profile_results.csv) \
    <(cut -d, -f3   results_compare/rt_armclang/profile_results.csv) \
  | head
```

## What this is not

This does **not** isolate the engine choice from the toolchain choice — it
varies both. To isolate the engine effect, hold toolchain constant and only
flip `--engine` (see [Engine Comparison](engine-comparison.md)). To isolate
placement effects, also hold `--arena-location` / `--weights-location`
constant (see [Memory Placement Tuning](../guides/memory-placement-tuning.md)).

## Where to go deeper

- [Toolchains](../guide/toolchains.md) — install steps and trade-offs.
- [Engine Comparison](engine-comparison.md) — engine-only sweep.
- [Per-Layer Breakdown](per-layer.md) — drill into individual ops.
