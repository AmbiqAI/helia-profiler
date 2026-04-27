# `hpx boards`

List all boards registered in the platform registry.

## Synopsis

```bash
hpx boards
```

## Output

```
Supported boards:

  apollo510_evb     Cortex-M55  AP5 / Apollo510   PMU=full   MVE=yes  PSRAM=yes
  apollo510b_evb    Cortex-M55  AP5 / Apollo510b  PMU=full   MVE=yes  PSRAM=yes
  apollo5b_evb      Cortex-M55  AP5 / Apollo5b    PMU=full   MVE=yes  PSRAM=yes
  apollo330mP_evb   Cortex-M55  AP5 / Apollo330P  PMU=full   MVE=yes  PSRAM=yes
  apollo4p_evb      Cortex-M4   AP4 / Apollo4p    PMU=DWT    MVE=no   PSRAM=no
  apollo3p_evb      Cortex-M4   AP3 / Apollo3p    PMU=DWT    MVE=no   PSRAM=no
```

Use any of these names with `--board` or `target.board:` in YAML.

## See also

- [Boards & Platforms](../guide/boards.md) — what each SoC family
  supports.
