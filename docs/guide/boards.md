# Boards & Platforms

heliaPROFILER uses a two-level hardware model: **Board → SoC**. The board
identifies the physical EVB and dictates J-Link device strings, USB IDs,
and pin defaults. The SoC determines CPU architecture, PMU capabilities,
and memory layout.

## Supported boards

Run `hpx boards` to see the live list. As of this release:

| Board | SoC | Core | PMU | MVE | PSRAM | Channel |
|---|---|---|---|---|---|---|
| `apollo510_evb` | apollo510 | Cortex-M55 | Full Armv8-M | Yes | Yes | Stable |
| `apollo510b_evb` | apollo510b | Cortex-M55 | Full Armv8-M | Yes | Yes | Preview |
| `apollo5b_evb` | apollo5b | Cortex-M55 | Full Armv8-M | Yes | Yes | Preview |
| `apollo330mP_evb` | apollo330P | Cortex-M55 | Full Armv8-M | Yes | Yes | Preview |
| `apollo4p_evb` | apollo4p | Cortex-M4 | DWT only | No | No | Stable |
| `apollo3p_evb` | apollo3p | Cortex-M4 | DWT only | No | No | Stable |

!!! tip "Apollo510 EVB is the default"
    If `--board` is not specified, the profiler targets `apollo510_evb`.
    This is the most fully-featured target and the recommended starting
    point.

## SoC families

### AP5 — Cortex-M55 (Apollo510, Apollo510b, Apollo5b, Apollo330P)

- Full **Armv8-M PMU** with 8 configurable event counters plus a
  dedicated cycle counter.
- 70+ PMU events across CPU, memory, and MVE groups.
- **MVE / Helium** SIMD support — vectorized CMSIS-NN kernels.
- Per-layer counter breakdown is fully supported.

!!! note "Apollo330P is in the AP5 family"
    Despite the "3" in the name, Apollo330P uses a Cortex-M55 core. It
    belongs to the AP5 family and gets full PMU + MVE.

### AP4 — Cortex-M4 (Apollo4p)

- **DWT cycle counter only** — no configurable PMU events.
- No MVE/Helium support.
- PMU group selections (`cpu`, `memory`, `mve`) are ignored — only the
  cycle count is captured.

### AP3 — Cortex-M4 (Apollo3p)

- Same DWT-only profile as AP4.
- Smallest memory budget of the supported boards.

## What this means for your config

| Capability | AP5 | AP4 / AP3 |
|---|---|---|
| Per-layer cycle counts | ✓ | ✓ |
| Per-layer PMU counter detail | ✓ | (cycles only) |
| MVE counter group | ✓ | (rejected at preflight) |
| `model_location: psram` | ✓ (board-dependent) | ✗ |
| Power capture | ✓ | ✓ |

When you target an AP4/AP3 board, the profiler **warns** about ignored
PMU/MVE selections and falls back to cycle-count-only capture. The
config itself is not rejected — it's reduced.

## SDK / NSX module mapping

Each SoC family maps to an AmbiqSuite SDK tier, which determines which
NSX modules are pulled into the firmware build. This is fully automatic;
you only choose `target.board`.

| Family | SDK Tier | NSX modules |
|---|---|---|
| AP5 | r5 | `nsx-ambiqsuite-r5`, `nsx-ambiq-hal-r5`, `nsx-ambiq-bsp-r5` |
| AP4 | r4 | `nsx-ambiqsuite-r4`, `nsx-ambiq-hal-r4`, `nsx-ambiq-bsp-r4` |
| AP3 | r3 | `nsx-ambiqsuite-r3`, `nsx-ambiq-hal-r3`, `nsx-ambiq-bsp-r3` |

## J-Link device strings

The profiler passes the right device string to JLinkExe automatically.
For reference:

| Board | J-Link device |
|---|---|
| `apollo510_evb` | `AP510NFA-CBR` |
| `apollo510b_evb` | `AP510L1-CBR` |
| `apollo5b_evb` | `AP5B-CBR` |
| `apollo330mP_evb` | `AP330P-CBR` |
| `apollo4p_evb` | `AMA4B2KP-KXR` |
| `apollo3p_evb` | `AMAP3B-KBR` |

If you have multiple probes connected, pin one with
`--jlink-serial <SN>` or `target.jlink_serial: "<SN>"`.

## Adding a new board

Boards are registered in code (not config). Adding a new EVB means
adding an entry to the platform registry with:

- Board name and SoC family
- J-Link device string
- USB VID/PID for USB CDC transport
- Memory layout (MRAM/SRAM/TCM/PSRAM sizes)
- Default sync GPIO pin

See [Architecture → Adding an Engine](../architecture/adding-an-engine.md)
for the analogous engine path; the board path follows the same pattern
in `src/helia_profiler/platform.py`.
