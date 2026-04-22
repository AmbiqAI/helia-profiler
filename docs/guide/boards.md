# Boards & Platforms

heliaPROFILER uses a two-level platform model: **Board → SoC**. The board
identifies the physical hardware, while the SoC determines CPU architecture,
PMU capabilities, memory layout, and available peripherals.

## Supported boards

Run `hpx boards` to see the current list:

| Board | SoC | Core | PMU | MVE | Channel |
|---|---|---|---|---|---|
| `apollo510_evb` | apollo510 | Cortex-M55 | Full | Yes | stable |
| `apollo510b_evb` | apollo510b | Cortex-M55 | Full | Yes | preview |
| `apollo5b_evb` | apollo5b | Cortex-M55 | Full | Yes | preview |
| `apollo330mP_evb` | apollo330P | Cortex-M55 | Full | Yes | preview |
| `apollo4p_evb` | apollo4p | Cortex-M4 | DWT | No | preview |
| `apollo3p_evb` | apollo3p | Cortex-M4 | DWT | No | stable |

!!! tip "Apollo510 EVB is the default"
    If you don't specify `--board`, the profiler targets `apollo510_evb`.
    This is the recommended board for full PMU profiling.

## SoC families

### AP5 — Cortex-M55 (recommended)

- **Full Armv8-M PMU** with 8 configurable event counters
- **70+ PMU events**: cycles, instructions, cache, branches, stalls, MVE
- **MVE / Helium** SIMD support — enables vectorized CMSIS-NN kernels
- Per-layer breakdown with rich counter data

Boards: `apollo510_evb`, `apollo510b_evb`, `apollo5b_evb`, `apollo330mP_evb`

!!! note "Apollo330P is AP5"
    Despite the "3" in its name, Apollo330P has a Cortex-M55 core with full
    PMU and MVE support. It belongs to the AP5 family.

### AP4 — Cortex-M4

- **DWT cycle counter only** — no configurable PMU events
- No MVE/Helium support
- PMU preset selection is ignored; only cycle counts are captured

Boards: `apollo4p_evb`

### AP3 — Cortex-M4

- Same DWT-only limitations as AP4
- Oldest supported family

Boards: `apollo3p_evb`

## PMU tiers

The SoC determines which PMU tier is available:

| Tier | Architecture | Counters | Events | Per-layer? |
|---|---|---|---|---|
| `ARMV8M_PMU` | Cortex-M55 | 8 configurable + cycle counter | 70+ | Yes |
| `DWT_ONLY` | Cortex-M4 | Cycle counter only | 1 | Limited |

When targeting a DWT-only SoC, the profiler warns you and falls back to
cycle-count-only profiling. PMU counter/preset selections are ignored.

## SDK tiers

Each SoC family maps to an AmbiqSuite SDK tier, which determines which NSX
modules are used in the firmware build:

| Family | SDK Tier | NSX Modules |
|---|---|---|
| AP5 | r5 | `nsx-ambiqsuite-r5`, `nsx-ambiq-hal-r5`, `nsx-ambiq-bsp-r5` |
| AP4 | r4 | `nsx-ambiqsuite-r4`, `nsx-ambiq-hal-r4`, `nsx-ambiq-bsp-r4` |
| AP3 | r3 | `nsx-ambiqsuite-r3`, `nsx-ambiq-hal-r3`, `nsx-ambiq-bsp-r3` |

This is handled automatically — you only need to specify the board name.
