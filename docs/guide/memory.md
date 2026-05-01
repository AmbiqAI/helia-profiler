# Memory Placement

Where you place the **tensor arena** (activations / scratch space) and the
**model weights** (the `.tflite` flatbuffer or AOT weight blobs) often
matters more than the model itself. heliaPROFILER lets you control both
through a single knob — `model_location` — and picks a sensible default
when you don't.

This guide explains the memory tiers on Ambiq SoCs, what `model_location`
does, and how to read the placement decisions in your reports.

---

## The memory tiers

Ambiq SoCs typically expose four tiers of memory. From fastest to slowest:

| Tier        | What it is                                          | Latency          | Where it sits         | Typical size (AP510) |
|-------------|------------------------------------------------------|------------------|------------------------|----------------------|
| **TCM**     | Tightly-Coupled Memory — DTCM/ITCM private to the CM55 core | Single-cycle     | Inside the core        | DTCM 512 KB, ITCM 256 KB |
| **SRAM**    | Shared on-chip SRAM                                  | A few cycles     | On-chip, outside core  | 3 MB                  |
| **MRAM**    | Non-volatile flash (program + rodata)                | Slowest on-chip  | On-chip, NVM           | 4 MB                  |
| **PSRAM**   | External pseudo-static RAM                           | External-bus     | Off-chip               | up to ~32 MB         |

Two things to remember:

* **TCM is *not* SRAM.** TCM is private to the CM55 core and runs at
  single-cycle latency. SRAM is shared, on-chip, but lives *outside* the
  core and is a few cycles slower. Reports keep the two distinct.
* **MRAM is flash.** Putting weights "in MRAM" means they stay in
  rodata and are read from non-volatile memory. There's no boot-time
  copy.
* **TCM/SRAM placement of weights costs boot-time copy.** When weights
  live in TCM or SRAM the runtime initialises those sections from NVM at
  boot. The benchmark numbers don't include that cost, but the binary
  does include the source bytes.

> AP3 / AP4 boards expose a single combined TCM region; AP510 splits it
> into DTCM (data) and ITCM (instructions). heliaPROFILER only places
> data in DTCM — ITCM is reserved for code.

---

## `model_location`: the one knob

Set `model_location` in your YAML or pass `--model-location` on the CLI:

```yaml
model:
  path: model.tflite
  arena_size: 65536
  model_location: auto    # default
```

```bash
heliaprofile run --model model.tflite --model-location tcm
```

Five values are accepted:

| Value     | Arena lives in | Weights live in | Notes                                                       |
|-----------|----------------|------------------|-------------------------------------------------------------|
| `auto`    | greedy fastest | greedy fastest   | **Default.** Best-fit policy, see below.                    |
| `tcm`     | TCM            | TCM              | Both copied into DTCM at boot. Best-case benchmark numbers. |
| `sram`    | SRAM           | SRAM             | Both in shared on-chip SRAM. Frees TCM for stack/BSS.       |
| `mram`    | TCM (or SRAM)  | MRAM             | Legacy default: weights stay in flash rodata.               |
| `psram`   | SRAM           | PSRAM (external) | Weights uploaded to PSRAM at runtime. Opt-in only.          |

`auto` and `psram` are special-cased; the others place arena and weights
in exactly the regions the value names.

---

## How `auto` decides

`auto` is **greedy fastest-fit, with the arena prioritised over weights**.
It walks down the memory hierarchy and places things where they fit:

1. If both arena and weights fit in TCM → both go in TCM.
2. Else, arena in TCM, weights in SRAM (or MRAM if SRAM is full).
3. Else, arena in SRAM, weights in MRAM.
4. Else (arena doesn't fit anywhere fast) → MRAM weights, SRAM arena, and
   you'll see a memory-overflow error from the placement validator.

The arena gets the faster region on ties because it's accessed every
inference cycle, whereas weights are streamed once per layer and benefit
less from a single-cycle hit.

`auto` **never** chooses PSRAM — that path requires the runtime upload
handshake and you have to opt in explicitly with `--model-location psram`.

A small slack budget (~8 KB in TCM, ~32 KB in SRAM) is reserved for
stack, heap, and BSS so the rest of the firmware still builds.

---

## Worked examples (Apollo510)

Apollo510 has DTCM 512 KB, SRAM 3 MB, MRAM 4 MB, PSRAM up to 32 MB.

### Tiny KWS model (~50 KB weights, ~30 KB arena)

```bash
heliaprofile run --model kws.tflite --arena-size 30720
```

`auto` policy → both fit in DTCM with room to spare → arena=TCM,
weights=TCM. Best-case latency.

### Mid-size vision model (~700 KB weights, 256 KB arena)

`auto` policy → arena fits in DTCM, weights too big for DTCM → arena=TCM,
weights=SRAM. Still much faster than MRAM weights.

### Large model (~5 MB weights, 1 MB arena)

`auto` policy → arena too big for DTCM, fits in SRAM; weights too big for
SRAM → arena=SRAM, weights=MRAM. Or, opt in to PSRAM with
`--model-location psram` to free up SRAM.

---

## Reading placement in reports

The run report's **Memory Plan** table lists every consumer (arena, model
weights, AOT per-tensor allocations) by physical region (DTCM, SRAM,
MRAM, PSRAM) along with the SoC's capacity. Overflow triggers an early
`PlatformError` *before* firmware is built, with a hint pointing at the
knobs you can turn.

```
Memory plan (tflm):
  DTCM     65,536 /  512,000 B (12.8%)
  SRAM     50,176 / 3,145,728 B ( 1.6%)
  MRAM          0 / 4,194,304 B ( 0.0%)
```

In this run, `auto` decided arena fits in DTCM (the 65 KB row) and the
model flatbuffer (50 KB) goes in SRAM.

---

## When to override `auto`

* **Repeatable benchmarks across configs:** pin to `tcm` or `mram` so a
  small model-size change doesn't cross a tier boundary mid-experiment.
* **Compare placement effects:** run the same model with `--model-location
  tcm` vs `mram` and diff the cycle counts. The TCM-vs-MRAM gap is the
  cost of running weights from flash.
* **Power experiments:** weights in TCM may let the SoC power-gate MRAM
  during inference. Use `--model-location tcm` and compare Joulescope
  traces.
* **Large models:** `--model-location psram` is the only option once
  weights exceed SRAM capacity.

---

## Engine-specific notes

* **heliaRT / TFLM** (interpreter): a single tensor arena holds all
  activations; weights are the model flatbuffer. Both can be steered by
  `model_location`.
* **heliaAOT**: the AOT compiler emits per-tensor section attributes
  (`PUT_IN_DTCM`, `PUT_IN_SRAM`, …) so it does its own redistribution.
  `model_location` still selects the *default* tier and arena region; AOT
  refines from there. The Memory Plan table reflects AOT's actual
  allocation.

---

## See also

* [`heliaprofile run` reference](../reference/profile.md) — full CLI flags
* [Boards and SoCs](boards.md) — per-SoC memory capacities
* [Engines](engines.md) — heliaRT vs heliaAOT trade-offs
