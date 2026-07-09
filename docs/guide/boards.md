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
| `apollo4p_evb` | apollo4p | Cortex-M4 | DWT only | No | Yes | Preview |
| `apollo4l_evb` | apollo4l | Cortex-M4 | DWT only | No | Yes | Preview |
| `apollo4l_blue_evb` | apollo4l | Cortex-M4 | DWT only | No | Yes | Preview |
| `apollo4p_blue_kbr_evb` | apollo4p | Cortex-M4 | DWT only | No | Yes | Preview |
| `apollo4p_blue_kxr_evb` | apollo4p | Cortex-M4 | DWT only | No | Yes | Preview |
| `apollo3p_evb` | apollo3p | Cortex-M4 | DWT only | No | Yes | Stable |
| `apollo3p_evb_cygnus` | apollo3p | Cortex-M4 | DWT only | No | Yes | Preview |

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
| `weights_location: psram` | ✓ | ✓ (all built-in EVBs ship PSRAM) |
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
| `apollo510b_evb` | `AP510BFA-CBR` |
| `apollo5b_evb` | `AP510NFA-CBR` |
| `apollo330mP_evb` | `Apollo330P_510L` |
| `apollo4p_evb` | `AMAP42KP-KBR` |
| `apollo4l_evb` | `AMAP42KL-KBR` |
| `apollo4l_blue_evb` | `AMAP42KL-KBR` |
| `apollo4p_blue_kbr_evb` | `AMAP42KP-KBR` |
| `apollo4p_blue_kxr_evb` | `AMAP42KP-KBR` |
| `apollo3p_evb` | `AMA3B2KK-KBR` |
| `apollo3p_evb_cygnus` | `AMA3B2KK-KBR` |

If you have multiple probes connected, pin one with
`--jlink-serial <SN>` or `target.jlink_serial: "<SN>"`.

## Adding a new board

HPX now supports config-scoped custom boards. For a board that behaves like an
existing EVB, add a `target.custom_boards` entry in your config and inherit from
the closest built-in board:

```yaml
target:
  board: apollo510_lab
  custom_boards:
    apollo510_lab:
      based_on: apollo510_evb
      channel: dev
      default_sync_gpio_pin: 27
```

If you are bringing up a genuinely new SoC/board combination, define a custom
SoC first, then point a custom board at it:

```yaml
target:
  board: apollo510_custom_board
  custom_socs:
    apollo510_custom:
      based_on: apollo510
      jlink_device: AP510-CUSTOM
      rtt_scan_ranges:
        - [0x21000000, 0x100000]
  custom_boards:
    apollo510_custom_board:
      soc: apollo510_custom
      channel: dev
      starter_profile_board: apollo510_evb
```

The important fields are still the same platform facts as the built-in registry:

- Board name and SoC family
- J-Link device string
- Memory layout (MRAM/SRAM/TCM/PSRAM sizes)
- Default sync GPIO pin (`29` for `apollo510_evb` / `apollo510b_evb`; `10` for most other built-in EVBs)

`starter_profile_board` lets a custom board reuse the NSX starter-profile module
graph from a built-in board while keeping its own board ID, channel, sync pin,
and SoC metadata in HPX.

See [Architecture → Adding an Engine](../architecture/adding-an-engine.md)
for the analogous engine path.
