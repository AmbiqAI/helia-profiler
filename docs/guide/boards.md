# Boards & Platforms

!!! note "Under construction"
    This page will cover the platform model and supported boards.

heliaPROFILER has a two-level platform model: **Board → SoC**.

## Supported Boards

```bash
hpx boards
```

| Board | SoC | Core | PMU | MVE |
|---|---|---|---|---|
| apollo510_evb | apollo510 | Cortex-M55 | full | yes |
| apollo510b_evb | apollo510b | Cortex-M55 | full | yes |
| apollo5b_evb | apollo5b | Cortex-M55 | full | yes |
| apollo330mP_evb | apollo330P | Cortex-M55 | full | yes |
| apollo4p_evb | apollo4p | Cortex-M4 | dwt | no |
| apollo3p_evb | apollo3p | Cortex-M4 | dwt | no |

## SoC Families

- **AP5** (Cortex-M55) — Full Armv8-M PMU with 8 counters, 70+ events, MVE/Helium
- **AP4** (Cortex-M4) — DWT cycle counter only
- **AP3** (Cortex-M4) — DWT cycle counter only
