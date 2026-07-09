# PMU Counters

heliaPROFILER captures per-layer hardware performance counters — cycles,
instructions, cache activity, and (on Cortex-M55) MVE/Helium vector unit
activity. This page starts with the minimal config to get a first
per-layer breakdown, then covers the full counter catalogue, multi-pass
mechanics, aggregation, and troubleshooting.

## Quick start

The simplest useful config asks for the curated `default` counter set for
each group you care about:

```yaml
profiling:
  pmu_counters:
    cpu: default
```

```bash
hpx profile model.tflite --pmu-counters cpu:default
```

`per_layer` defaults to `true`, `iterations` to `100`, `warmup` to `5`, and
`aggregation` to `median` — sensible defaults for a first run. Results land
in `profile_results.csv` and the terminal's top-layers table; see
[Reading results](#reading-results).

## Selecting counters

`profiling.pmu_counters` maps a **group** name to a **selection**:

| Selection | Meaning |
|---|---|
| `"default"` | A curated subset for that group — the most useful counters, sized to fit one pass |
| `"all"` | Every counter in the group (multi-pass — see below) |
| `[name, ...]` | An explicit list of counter names |

```yaml
profiling:
  pmu_counters:
    cpu: default             # curated CPU counters
    memory: [ARM_PMU_L1D_CACHE, ARM_PMU_L1D_CACHE_MISS_RD, ARM_PMU_DTCM_ACCESS]
    mve: all                  # every MVE counter (multiple passes)
```

Or via CLI — repeat `--pmu-counters GROUP:SELECT`, with comma-separated
names for an explicit list:

```bash
hpx profile model.tflite \
  --pmu-counters cpu:default \
  --pmu-counters mve:all \
  --pmu-counters mve:ARM_PMU_MVE_INST_RETIRED,ARM_PMU_MVE_STALL
```

!!! note "Legacy `pmu_presets`"
    An older `profiling.pmu_presets` list (`basic_cpu`, `memory`, `mve`,
    `ml_default`) is still accepted and converted internally (each preset
    maps to a `(group, "default")` pair), but emits a deprecation warning.
    Prefer `pmu_counters`.

## Counter groups and tiers

Counters are organized by **compute-unit group**. Which groups are
available depends on the target's **PMU tier**:

| Tier | SoC family | Groups |
|---|---|---|
| Armv8-M PMU | Apollo5 family (Cortex-M55, including Apollo330P) | `cpu`, `mve`, `memory` |
| DWT-only | Apollo3/Apollo3P, Apollo4/Apollo4P/Apollo4L (Cortex-M4) | Cycle counter only — no group selection |

On DWT-only targets, heliaPROFILER warns and captures cycle counts only;
any `pmu_counters` selection is silently ignored for those runs.

### CPU counters

Core execution events — instructions, branches, stalls, exceptions.

| Counter | Description |
|---|---|
| `ARM_PMU_BR_IMMED_RETIRED` | Immediate branch architecturally executed |
| `ARM_PMU_BR_MIS_PRED_RETIRED` | Mispredicted branch instruction architecturally executed |
| `ARM_PMU_BR_RETIRED` | Branch instruction architecturally executed |
| `ARM_PMU_BR_RETURN_RETIRED` | Function return instruction architecturally executed |
| `ARM_PMU_CPU_CYCLES` | Cycle count |
| `ARM_PMU_EXC_RETURN` | Exception return instruction architecturally executed |
| `ARM_PMU_EXC_TAKEN` | Exception entry |
| `ARM_PMU_INST_RETIRED` | Instruction architecturally executed |
| `ARM_PMU_L1I_CACHE_REFILL` | L1 instruction cache refill |
| `ARM_PMU_LD_RETIRED` | Memory-reading instruction architecturally executed |
| `ARM_PMU_LE_CANCEL` | Loop-end instruction not taken |
| `ARM_PMU_LE_RETIRED` | Loop-end instruction executed |
| `ARM_PMU_PC_WRITE_RETIRED` | Software change to the program counter (indirect branch) |
| `ARM_PMU_SE_CALL_NS` | Call to non-secure function (security state change) |
| `ARM_PMU_SE_CALL_S` | Call to secure function (security state change) |
| `ARM_PMU_STALL` | Stall cycle — no instruction/operation sent for execution |
| `ARM_PMU_STALL_BACKEND` | No operation issued because of the backend |
| `ARM_PMU_STALL_FRONTEND` | No operation issued because of the frontend |
| `ARM_PMU_ST_RETIRED` | Memory-writing instruction architecturally executed |
| `ARM_PMU_SW_INCR` | Software update to the `PMU_SWINC` register |
| `ARM_PMU_UNALIGNED_LDST_RETIRED` | Unaligned memory-reading/writing instruction |

`default`: `ARM_PMU_CPU_CYCLES`, `ARM_PMU_INST_RETIRED`,
`ARM_PMU_STALL_FRONTEND`, `ARM_PMU_STALL_BACKEND`.

### Memory counters

Cache, TCM, and bus activity.

| Counter | Description |
|---|---|
| `ARM_PMU_BUS_ACCESS` | Bus access |
| `ARM_PMU_BUS_CYCLES` | Bus cycles |
| `ARM_PMU_DTCM_ACCESS` | Data TCM access |
| `ARM_PMU_ITCM_ACCESS` | Instruction TCM access |
| `ARM_PMU_L1D_CACHE` | L1 data cache access |
| `ARM_PMU_L1D_CACHE_ALLOCATE` | L1 data cache allocation without refill |
| `ARM_PMU_L1D_CACHE_MISS_RD` | L1 data cache read miss |
| `ARM_PMU_L1D_CACHE_RD` | L1 data cache read |
| `ARM_PMU_L1D_CACHE_REFILL` | L1 data cache refill (a miss that allocates) |
| `ARM_PMU_L1D_CACHE_WB` | L1 data cache write-back |
| `ARM_PMU_L1I_CACHE` | L1 instruction cache access |
| `ARM_PMU_LL_CACHE_MISS_RD` | Last-level data cache read miss |
| `ARM_PMU_LL_CACHE_RD` | Last-level data cache read |
| `ARM_PMU_MEMORY_ERROR` | Local memory error |
| `ARM_PMU_MEM_ACCESS` | Data memory access |

`default`: `ARM_PMU_MEM_ACCESS`, `ARM_PMU_L1D_CACHE_REFILL`,
`ARM_PMU_BUS_ACCESS`, `ARM_PMU_BUS_CYCLES`.

### MVE counters

Helium/MVE (M-Profile Vector Extension) activity. Only available on
Cortex-M55 (the Armv8-M PMU tier).

| Counter | Description |
|---|---|
| `ARM_PMU_MVE_FP_HP_RETIRED` | MVE half-precision floating-point instruction |
| `ARM_PMU_MVE_FP_MAC_RETIRED` | MVE floating-point multiply/multiply-accumulate |
| `ARM_PMU_MVE_FP_RETIRED` | MVE floating-point instruction |
| `ARM_PMU_MVE_FP_SP_RETIRED` | MVE single-precision floating-point instruction |
| `ARM_PMU_MVE_INST_RETIRED` | Total MVE instructions |
| `ARM_PMU_MVE_INT_MAC_RETIRED` | MVE multiply/multiply-accumulate (integer) |
| `ARM_PMU_MVE_INT_RETIRED` | MVE integer instruction |
| `ARM_PMU_MVE_LDST_CONTIG_RETIRED` | MVE contiguous load/store |
| `ARM_PMU_MVE_LDST_MULTI_RETIRED` | MVE memory instruction targeting multiple registers |
| `ARM_PMU_MVE_LDST_NONCONTIG_RETIRED` | MVE non-contiguous (scatter/gather) load/store |
| `ARM_PMU_MVE_LDST_RETIRED` | Total MVE load/store instructions |
| `ARM_PMU_MVE_LDST_UNALIGNED_NONCONTIG_RETIRED` | MVE unaligned, non-contiguous load/store |
| `ARM_PMU_MVE_LDST_UNALIGNED_RETIRED` | MVE unaligned load/store |
| `ARM_PMU_MVE_LD_CONTIG_RETIRED` | MVE contiguous load |
| `ARM_PMU_MVE_LD_MULTI_RETIRED` | MVE multi-register load |
| `ARM_PMU_MVE_LD_NONCONTIG_RETIRED` | MVE non-contiguous load |
| `ARM_PMU_MVE_LD_RETIRED` | MVE load |
| `ARM_PMU_MVE_LD_UNALIGNED_RETIRED` | MVE unaligned load |
| `ARM_PMU_MVE_PRED` | Cycles with one or more predicated beats executed |
| `ARM_PMU_MVE_STALL` | MVE stall cycles |
| `ARM_PMU_MVE_STALL_BREAK` | MVE chain-break stall cycles |
| `ARM_PMU_MVE_STALL_DEPENDENCY` | MVE register-dependency stall cycles |
| `ARM_PMU_MVE_STALL_RESOURCE` | MVE resource-conflict stall cycles |
| `ARM_PMU_MVE_STALL_RESOURCE_FP` | MVE floating-point resource-conflict stalls |
| `ARM_PMU_MVE_STALL_RESOURCE_INT` | MVE integer resource-conflict stalls |
| `ARM_PMU_MVE_STALL_RESOURCE_MEM` | MVE memory resource-conflict stalls |
| `ARM_PMU_MVE_ST_CONTIG_RETIRED` | MVE contiguous store |
| `ARM_PMU_MVE_ST_MULTI_RETIRED` | MVE multi-register store |
| `ARM_PMU_MVE_ST_NONCONTIG_RETIRED` | MVE non-contiguous store |
| `ARM_PMU_MVE_ST_RETIRED` | MVE store |
| `ARM_PMU_MVE_ST_UNALIGNED_RETIRED` | MVE unaligned store |
| `ARM_PMU_MVE_VREDUCE_FP_RETIRED` | MVE floating-point vector reduction |
| `ARM_PMU_MVE_VREDUCE_INT_RETIRED` | MVE integer vector reduction |
| `ARM_PMU_MVE_VREDUCE_RETIRED` | MVE vector reduction |

`default`: `ARM_PMU_MVE_INST_RETIRED`, `ARM_PMU_MVE_INT_MAC_RETIRED`,
`ARM_PMU_MVE_LDST_RETIRED`, `ARM_PMU_MVE_STALL`.

## Multi-pass profiling

Cortex-M55's PMU has 8 hardware counter slots, but heliaPROFILER chains
pairs of them into 32-bit logical counters to avoid overflow — so each
firmware pass can capture **4 counters at a time** (per group).
When your selection exceeds that:

1. **Plans passes** — counters are grouped by compute unit, then split into
   batches of 4.
2. **Runs each pass** — builds and captures once per pass, running
   `warmup + iterations` inferences each time.
3. **Merges results** — results across all passes are combined into one
   unified per-layer table.

`profile_results.csv` always shows the merged result across all passes.
With `output.detailed: true`, you also get per-group and per-pass CSVs
under `detailed/` (for example `profile_cpu.csv` merged, plus
`profile_cpu_0.csv`, `profile_cpu_1.csv`, … per pass).

!!! info "Cost of `all`"
    Selecting `cpu: all`, `memory: all`, `mve: all` (70 counters across the
    three groups) requires many passes — each one builds, flashes, and
    captures independently, so a full-catalogue run takes noticeably longer
    than the curated `default` selection. Prefer explicit counter lists or
    `default` for iterative work, and reach for `all` when you need a
    complete sweep.

## Aggregation and iterations

`profiling.iterations` (default `100`) is how many instrumented inferences
each pass runs; `profiling.warmup` (default `5`) runs first and is
discarded. `profiling.aggregation` (default `median`) controls how
per-layer counters are combined across those iterations:

| Method | Behavior |
|---|---|
| `median` *(default)* | Robust to corrupted/outlier iterations |
| `mean` | Simple arithmetic average |
| `trimmed` | Drops the high/low extremes, then averages the rest |

All three methods first reject **structurally-invalid samples** — a
uint32-wrap (finish < start) or a frozen-zero row — before aggregating, and
log how many were rejected.

```yaml
profiling:
  iterations: 200
  warmup: 10
  aggregation: median
```

### Per-layer vs. whole-model

`profiling.per_layer` (default `true`) instruments every layer. Setting it
to `false` captures whole-model counters only, skipping per-layer
instrumentation overhead. heliaPROFILER also always runs a **clean
end-to-end pass** — no per-layer instrumentation, warmed caches — and
reports its cycle count alongside the per-layer sum; a small overhead delta
between the two is normal and shown in the terminal output.

## Reading results

- **Terminal top-layers table** — the top layers by cycle count, with
  percentage of total, MVE instruction ratio, and MACs-per-MVE-instruction
  on Cortex-M55.
- **`profile_results.csv`** — one row per layer with every captured
  counter, merged across passes.
- **`detailed/profile_<group>.csv`** and **`detailed/profile_<group>_<pass>.csv`**
  (with `output.detailed: true`) — merged and per-pass breakdowns.
- **Model Explorer overlay** — one `me_overlay_<COUNTER>.json` per counter
  under `model_explorer/`, for visualizing per-layer hot spots directly on
  the model graph. See [Model Explorer Overlays](model-explorer.md).

### Derived metrics

| Metric | Formula | Included in |
|---|---|---|
| L1D hit rate | `1 - (L1D_CACHE_MISS_RD / L1D_CACHE_RD) × 100%` | `summary.json`, `detailed/memory.json` |
| MVE instruction share | `MVE_INST_RETIRED / INST_RETIRED × 100%` | Terminal summary |
| MVE MAC density | `MVE_INT_MAC_RETIRED / MVE_INST_RETIRED` | Terminal summary |
| MVE load/store density | `MVE_LDST_RETIRED / MVE_INST_RETIRED` | Terminal summary |
| MVE stall share | `MVE_STALL / CPU_CYCLES × 100%` | Terminal summary |

## Apollo4: the debugger must stay attached

On Apollo4 (Cortex-M4, DWT-only tier) the `DWT->CYCCNT` cycle counter lives
in the core **debug power domain**. That domain is powered only while a
debugger asserts the Debug Access Port's `CDBGPWRUPREQ` signal — which is
*not* memory-mapped and therefore cannot be set by firmware running on the
core. If the host releases the J-Link probe after reset, the domain powers
down mid-run and every per-layer cycle count reads back as **0**.

The `rtt` and `swo` transports already hold a debugger attached for the
whole capture, so they are unaffected. For `uart` and `usb_cdc`,
heliaPROFILER detects Apollo4 automatically and keeps a `pylink` session
attached for the entire capture (reset and go are driven through that
session), so per-layer cycles are captured correctly. No configuration is
required.

Other families (Apollo3, Apollo5) do not gate the debug domain this way —
and the Apollo5 secure bootloader prefers the probe released — so they keep
releasing the probe after reset as before.

## `pmu_max_ops` — per-op record budget

Each SoC reserves a fixed amount of firmware memory for per-op PMU records
(`pmu_max_ops`), sized for that board's memory budget — for example 512 on
memory-constrained Apollo330P versus 4096 on Apollo510. Models with more
operators than the reservation allow will not fit; this is a static,
per-SoC limit rather than a config knob.

## Troubleshooting

??? failure "`overflow_detected: true` in `summary.json`"
    A counter saturated its 2³²-wide accumulator during a pass (very long
    or very hot layers on `all`-selected counters). The affected layer's
    `overflow` column is `True` in `profile_results.csv`; treat those rows'
    values as a lower bound, not exact. Reduce `iterations` or select fewer
    / narrower counters for that layer if you need precise saturation-free
    counts.

??? failure "All PMU counter selections silently ignored"
    The target is a DWT-only tier (Apollo3/Apollo3P, Apollo4/Apollo4P/
    Apollo4L). Only the cycle counter is available there; `pmu_counters`
    has no effect. Check `hpx profile --verbose` output for the DWT-only
    warning.

??? failure "Per-layer cycles read back as 0 on Apollo4"
    The debugger was released after reset while using `uart`/`usb_cdc`, and
    the `DWT->CYCCNT` debug power domain powered down mid-capture. This is
    normally handled automatically (heliaPROFILER keeps a `pylink` session
    attached on Apollo4), so if you still see this, check for a custom
    reset/lifecycle override that releases the probe early.

??? failure "Requesting `all` counters takes a long time"
    Expected — `all` across `cpu`, `mve`, and `memory` on Cortex-M55 spans
    70 counters, batched 4 at a time per group, so it runs many independent
    build+flash+capture passes. Narrow to `default` or an explicit counter
    list for faster iteration, and save `all` for a final, thorough sweep.

??? failure "Unknown PMU counter name"
    Counter names are case-sensitive and must match the catalogue exactly
    (e.g. `ARM_PMU_MVE_INT_MAC_RETIRED`, not `MVE_INT_MAC_RETIRED`). Check
    the tables above, or run with an invalid name once to see the full
    sorted list heliaPROFILER reports back in the error.

??? failure "PMU counter group not supported for this target"
    The requested group (for example `mve`) isn't available on the
    target's PMU tier or SoC. Only `cpu`, `mve`, and `memory` exist, and
    `mve` requires the Armv8-M PMU tier (Cortex-M55 / Apollo5 family).
