# Memory Placement Tuning

You've got a model profiling cleanly with default `auto` placement, and now
you want to know: is `auto` making the best choice, and can you shrink the
arena further? This guide walks the pattern for tuning placement
deliberately instead of leaving everything on `auto`.

For the full mechanics of placement (tiers, `auto`'s algorithm, engine
differences), see [Memory Placement](../guide/memory.md) — this guide is
the *workflow*, not the reference.

## 1. Start with `auto` and read the Memory Plan

Run once with the default `auto` placement (no placement fields at
all):

```bash
hpx profile my_model.tflite --board apollo510_evb --arena-size 131072
```

The terminal report prints a **Memory Plan** table — every consumer
(arena, weights) by physical region, against the SoC's capacity:

```
Memory plan (helia-rt):
  DTCM     65,536 /  512,000 B (12.8%)
  SRAM     50,176 / 3,145,728 B ( 1.6%)
  MRAM          0 / 4,194,304 B ( 0.0%)
```

The same data is in `summary.json` under `memory_plan` (`regions[]`, each
with `region`, `capacity`, `used`, `free`, `overflow`), and `memory.
allocated_arena` reports what the firmware actually used out of the arena
you requested.

```bash
python -c "import json; d=json.load(open('results/summary.json')); \
print(d['memory']['allocated_arena'], d['memory_plan']['regions'])"
```

## 2. Understand the region trade-offs

From fastest/smallest to slowest/largest: **TCM** (single-cycle, private to
the core, smallest), **SRAM** (a few cycles, shared, mid-size), **MRAM**
(flash — fine for read-only weights, no boot copy, but slowest), **PSRAM**
(external, largest, requires an explicit opt-in and a runtime upload
handshake). `auto` places the arena first (it's touched every inference)
and lets weights fall back to a slower tier when the arena needs the fast
one. See [The memory tiers](../guide/memory.md#the-memory-tiers) for the
full table and boot-copy caveats.

## 3. Pin placement explicitly

Once you understand where `auto` put things, pin it explicitly so a small
model-size change doesn't silently cross a tier boundary between runs:

```yaml
model:
  path: my_model.tflite
  arena_size: 131072
  arena_location: tcm
  weights_location: mram
```

```bash
hpx profile my_model.tflite --arena-location tcm --weights-location mram
```

## 4. Compare runs apples-to-apples

When comparing two placement policies (or comparing placement across an
engine/toolchain sweep), **hold placement constant across every other axis
you vary**, and only flip the placement fields between the two runs you're
actually comparing. Otherwise a cycle-count delta could come from
toolchain, board, or engine differences instead of placement.

```bash
hpx profile my_model.tflite --arena-location tcm --weights-location tcm \
    --output-dir results/place_tcm_tcm
hpx profile my_model.tflite --arena-location tcm --weights-location mram \
    --output-dir results/place_tcm_mram
```

```python
import json

tcm = json.load(open("results/place_tcm_tcm/summary.json"))
mram = json.load(open("results/place_tcm_mram/summary.json"))
print(f"tcm/tcm:  {tcm['total_cycles']:,} cycles")
print(f"tcm/mram: {mram['total_cycles']:,} cycles")
```

```
tcm/tcm:  1,842,003 cycles
tcm/mram: 2,016,376 cycles
```

Read this in **relative** terms — "moving weights from TCM to MRAM cost
about 9% more cycles on this model" — rather than quoting the absolute
counts as representative of your hardware; run your own comparison to get
numbers for your model and board.

## 5. Tighten `arena_size` from `allocated_arena`

`arena_size` is a request; `allocated_arena` in `summary.json` (or the
terminal report) is what the firmware actually used. Once you've run a
representative profile, set `arena_size` to somewhat above the reported
`allocated_arena` — enough headroom for input variation, not the original
guess:

```bash
python -c "import json; print(json.load(open('results/summary.json'))['memory']['allocated_arena'])"
# 98304
```

```yaml
model:
  arena_size: 114688   # ~1.15x allocated_arena, not the original guess
```

Re-run and confirm `allocated_arena` is unchanged and no region shows
`overflow: true`.

## 6. What an overflow error looks like

If a placement request doesn't fit, `hpx profile` fails **before firmware is
built**, with a hint pointing at the knobs to turn:

```
Error: Memory plan does not fit:
  DTCM: 540672 B used > 524288 B capacity (over by 16384 B)
  Hint: DTCM is over capacity.  Try one of:
    * shrink the tensor arena (--arena-size);
    * pick a less-aggressive placement
      (--model-location auto / mram);
    * move weights to PSRAM (--model-location psram) if the
      board has PSRAM;
    * reduce model size (quantise / prune); or
    * pick a larger-memory board.
```

Requesting a region the board doesn't have (e.g. `--arena-location tcm` on
a board with no DTCM, or `--weights-location psram` with no PSRAM wired up)
fails the same way, before any build work happens.

## Where to go deeper

- [Memory Placement](../guide/memory.md) — tiers, `auto`'s algorithm,
  worked examples, and engine-specific placement (heliaAOT per-tensor
  attributes).
- [`hpx profile` reference](../reference/profile.md) — every placement
  flag.
- [Output & Results](../guide/output.md) — the full `summary.json` schema.
