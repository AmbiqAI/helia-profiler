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
- App flash load address (see below)
- Default sync GPIO pin (most built-in EVBs register a board-specific pin,
  e.g. `29` for `apollo510_evb` / `apollo510b_evb`, `22` for the Apollo4
  Plus EVBs, `61` for the Apollo4 Lite EVBs, and `26` for the Apollo3 Plus
  EVBs; `10` is only the fallback for boards without a registered override)
- `ble_reset_gpio_pin` on a "Blue" board — the GPIO wired to the onboard
  Cooper BLE controller's reset line, held low during power captures so an
  idling radio does not sit in your measurement. Inherited from `based_on`;
  set it explicitly only if your board routes it differently. Leave it unset
  on boards with no onboard radio.

`starter_profile_board` lets a custom board reuse the NSX starter-profile module
graph from a built-in board while keeping its own board ID, channel, sync pin,
and SoC metadata in HPX.

Unrecognized keys are rejected, in `custom_socs`, `custom_boards`, and the
`memory` block alike. A misspelt key used to be discarded in silence, leaving
the built-in value in place while the config looked accepted.

### App flash load address

`app_flash_load_addr` is the first flash address above your part's
bootloader-reserved region — the address NSX programs application images at.
It is *not* the base of the MRAM region.

You rarely need to set it. HPX only uses it as a fallback for one operation
(flashing the dedicated power-measurement binary when the NSX-generated
`flash_cmds.jlink` recipe is missing); when that recipe exists it is used
verbatim and this value is ignored.

- `based_on: <soc>` inherits the address of that part, along with everything
  else. This is the usual case and needs nothing from you.
- Set it explicitly when your part's bootloader reservation differs from the
  part you based it on:

    ```yaml
    target:
      custom_socs:
        oem_ap5_variant:
          based_on: apollo510
          app_flash_load_addr: 0x22000000
    ```

- A custom SoC with no `app_flash_load_addr` **and** no `based_on` has no
  address at all, and HPX will refuse the fallback flash rather than guess one
  from your `family:` tag. `family` records a core tier here, not a memory map
  — two parts can share it and load at completely different addresses — and a
  guessed address is likely enough to be accepted by the silicon while landing
  your image at the wrong offset. Declare the address, or declare a `based_on`.
- Writing `app_flash_load_addr: null` explicitly is the same refusal, and it
  overrides inheritance: use it when you want a `based_on` part's memory and
  clock facts but do not trust its flash window. Leaving the key out is what
  inherits.

#### Upgrading an existing config

!!! warning "Behaviour change"

    A custom SoC that declares **no `based_on` and no `app_flash_load_addr`**
    used to inherit its family's address. It now resolves to *no address*, and
    the fallback flash refuses to run instead of programming at that inherited
    value. The values that stop being handed out are `0x00410000` (`ap5`),
    `0x00018000` (`ap4`) and `0x0000c000` (`ap3`).

    Nothing else changes: an entry with a `based_on` keeps inheriting exactly
    what it inherited before, including the worked example above.

    If you hit this, add **one** of these two keys to the `custom_socs` entry.
    Either name the characterised part to inherit from:

    ```yaml
    target:
      custom_socs:
        my_part:
          based_on: apollo510
    ```

    ...or state the address yourself:

    ```yaml
    target:
      custom_socs:
        my_part:
          app_flash_load_addr: 0x00410000
    ```

    Only pass the old family value verbatim if you have checked it against your
    part's own bootloader reservation. Being handed it unchecked is the failure
    this change exists to close.

The same applies to the programmatic API. `build_platform_registry(socs=...)`
takes `SocDef` objects directly and has no `based_on` mechanism at all, so a
`SocDef` you construct yourself must carry the address as a field:

```python
from helia_profiler.platform import SocDef, build_platform_registry, get_soc

base = get_soc("apollo510")
registry = build_platform_registry(
    socs={
        "apollo510_custom": SocDef(
            name="apollo510_custom",
            # ...platform facts...
            app_flash_load_addr=0x00410000,  # required; previously implied by `family`
        )
    }
)
```

Without it, `soc.capabilities.memory.app_flash_load_addr` is `None`. That is
also true of a `SocDef` built for a test fixture — only the built-in registry
is treated as characterised.

See [Architecture → Adding an Engine](../architecture/adding-an-engine.md)
for the analogous engine path.
