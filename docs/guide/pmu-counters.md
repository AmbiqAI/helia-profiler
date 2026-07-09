# PMU Counters

The Armv8-M PMU on Cortex-M55 has 8 configurable event counters plus a
dedicated cycle counter. Since there are 70+ possible events, heliaPROFILER
profiles in multiple passes — each pass configures a different set of 8
counters, runs the model, and the results are merged.

## Counter groups

Counters are organized into three groups:

### CPU counters

Core execution events — instructions, branches, stalls, exceptions.

| Counter | Description |
|---|---|
| `ARM_PMU_CPU_CYCLES` | Total CPU cycles |
| `ARM_PMU_INST_RETIRED` | Instructions retired |
| `ARM_PMU_LD_RETIRED` | Load instructions retired |
| `ARM_PMU_ST_RETIRED` | Store instructions retired |
| `ARM_PMU_BR_RETIRED` | Branch instructions retired |
| `ARM_PMU_BR_MIS_PRED_RETIRED` | Branch mispredictions |
| `ARM_PMU_BR_IMMED_RETIRED` | Immediate branches |
| `ARM_PMU_BR_RETURN_RETIRED` | Return branches |
| `ARM_PMU_PC_WRITE_RETIRED` | PC writes (indirect branches) |
| `ARM_PMU_EXC_TAKEN` | Exceptions taken |
| `ARM_PMU_EXC_RETURN` | Exception returns |
| `ARM_PMU_STALL` | Total pipeline stalls |
| `ARM_PMU_STALL_FRONTEND` | Frontend stalls |
| `ARM_PMU_STALL_BACKEND` | Backend stalls |
| `ARM_PMU_UNALIGNED_LDST_RETIRED` | Unaligned load/store |
| `ARM_PMU_LE_RETIRED` | Loop-end instructions |
| `ARM_PMU_LE_CANCEL` | Loop-end cancellations |
| `ARM_PMU_SW_INCR` | Software increment |

### Memory counters

Cache, TCM, and bus activity.

| Counter | Description |
|---|---|
| `ARM_PMU_L1D_CACHE` | L1 data cache accesses |
| `ARM_PMU_L1D_CACHE_RD` | L1 data cache reads |
| `ARM_PMU_L1D_CACHE_REFILL` | L1 data cache refills (misses that allocate) |
| `ARM_PMU_L1D_CACHE_MISS_RD` | L1 data cache read misses |
| `ARM_PMU_L1D_CACHE_WB` | L1 data cache writebacks |
| `ARM_PMU_L1D_CACHE_ALLOCATE` | L1 data cache allocations |
| `ARM_PMU_L1I_CACHE` | L1 instruction cache accesses |
| `ARM_PMU_L1I_CACHE_REFILL` | L1 instruction cache refills |
| `ARM_PMU_DTCM_ACCESS` | Data TCM accesses |
| `ARM_PMU_ITCM_ACCESS` | Instruction TCM accesses |
| `ARM_PMU_MEM_ACCESS` | Total memory accesses |
| `ARM_PMU_BUS_ACCESS` | Bus accesses (off-chip) |
| `ARM_PMU_BUS_CYCLES` | Bus cycles |
| `ARM_PMU_MEMORY_ERROR` | Memory errors |

### MVE counters

Helium/MVE (M-Profile Vector Extension) activity. Only available on Cortex-M55.

| Counter | Description |
|---|---|
| `ARM_PMU_MVE_INST_RETIRED` | Total MVE instructions |
| `ARM_PMU_MVE_INT_RETIRED` | MVE integer instructions |
| `ARM_PMU_MVE_INT_MAC_RETIRED` | MVE integer MAC instructions |
| `ARM_PMU_MVE_FP_RETIRED` | MVE floating-point instructions |
| `ARM_PMU_MVE_FP_MAC_RETIRED` | MVE FP MAC instructions |
| `ARM_PMU_MVE_LDST_RETIRED` | MVE load/store instructions |
| `ARM_PMU_MVE_LD_RETIRED` | MVE loads |
| `ARM_PMU_MVE_ST_RETIRED` | MVE stores |
| `ARM_PMU_MVE_LDST_CONTIG_RETIRED` | Contiguous MVE load/stores |
| `ARM_PMU_MVE_LDST_NONCONTIG_RETIRED` | Non-contiguous (scatter/gather) |
| `ARM_PMU_MVE_PRED` | Predicated MVE instructions |
| `ARM_PMU_MVE_STALL` | MVE stall cycles |
| `ARM_PMU_MVE_STALL_RESOURCE` | MVE resource stalls |
| `ARM_PMU_MVE_STALL_RESOURCE_MEM` | MVE memory resource stalls |
| `ARM_PMU_MVE_STALL_RESOURCE_FP` | MVE FP resource stalls |
| `ARM_PMU_MVE_STALL_RESOURCE_INT` | MVE integer resource stalls |
| `ARM_PMU_MVE_STALL_BREAK` | MVE stall breaks |
| `ARM_PMU_MVE_STALL_DEPENDENCY` | MVE dependency stalls |

## Selecting counters

### Select all counters in a group

```yaml
profiling:
  pmu_counters:
    cpu: all       # all CPU counters
    memory: all    # all memory/cache counters
    mve: all       # all MVE counters
```

### Select specific counters

```yaml
profiling:
  pmu_counters:
    cpu: [ARM_PMU_CPU_CYCLES, ARM_PMU_INST_RETIRED, ARM_PMU_STALL]
    memory: [ARM_PMU_L1D_CACHE, ARM_PMU_L1D_CACHE_MISS_RD, ARM_PMU_DTCM_ACCESS]
```

### Select all groups at once

```yaml
profiling:
  pmu_counters:
    cpu: all
    memory: all
    mve: all
```

Or via CLI:

```bash
hpx profile model.tflite --pmu-counters cpu:all --pmu-counters memory:all --pmu-counters mve:all
```

## Multi-pass profiling

The PMU has only 8 configurable counters. When you request more counters than
fit in a single pass, heliaPROFILER automatically:

1. **Plans passes** — groups counters into sets of 8
2. **Runs each pass** — builds and captures once per pass
3. **Averages iterations** — each pass runs `iterations` inferences
4. **Merges results** — combines all passes into unified per-layer results

The `profile_results.csv` always shows merged results across all passes. With
`--detailed`, you also get per-pass CSV files in the `detailed/` subfolder.

!!! info "Pass count"
    Requesting `cpu: all`, `memory: all`, `mve: all` (70+ counters) requires
    approximately 20 PMU passes. Each pass builds, flashes, and captures
    independently. This is the most thorough profiling mode but takes longer.

## Derived metrics

The report automatically computes derived metrics from raw counters:

| Metric | Formula | Included in |
|---|---|---|
| L1D hit rate | `1 - (L1D_CACHE_MISS_RD / L1D_CACHE_RD) × 100%` | `summary.json`, `memory.json` |
| MVE instruction share | `MVE_INST_RETIRED / INST_RETIRED × 100%` | Terminal summary |
| MVE MAC density | `MVE_INT_MAC_RETIRED / MVE_INST_RETIRED` | Terminal summary |
| MVE load/store density | `MVE_LDST_RETIRED / MVE_INST_RETIRED` | Terminal summary |
| MVE stall share | `MVE_STALL / CPU_CYCLES × 100%` | Terminal summary |

## DWT-only fallback

On Cortex-M4 targets (AP3, AP4), only the cycle counter is available. The
profiler warns you and captures cycle counts only — PMU counter selections are
silently ignored.

### Apollo4: the debugger must stay attached

On Apollo4 (Cortex-M4) the `DWT->CYCCNT` cycle counter lives in the core
**debug power domain**. That domain is powered only while a debugger asserts
the Debug Access Port's `CDBGPWRUPREQ` signal — which is *not* memory-mapped
and therefore cannot be set by firmware running on the core. If the host
releases the J-Link probe after reset, the domain powers down mid-run and every
per-layer cycle count reads back as **0**.

The RTT and SWO transports already hold a debugger attached for the whole
capture, so they are unaffected. For the UART and USB transports heliaPROFILER
detects Apollo4 automatically and keeps a `pylink` session attached for the
entire capture (reset and go are driven through that session), so per-layer
cycles are captured correctly. No configuration is required.

Other families (AP3, AP5) do not gate the debug domain this way — and the AP5
secure bootloader prefers the probe released — so they keep releasing the probe
after reset as before.
