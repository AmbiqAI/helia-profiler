# `hpx boards`

List all boards registered in the platform registry.

## Synopsis

```bash
hpx boards
```

## Output

```
Board                    SoC            Core           Backends             Dom
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 apollo3p_evb             apollo3p       cortex-m4      dwt                  cpu
 apollo3p_evb_cygnus      apollo3p       cortex-m4      dwt                  cpu
 apollo4p_evb             apollo4p       cortex-m4      dwt                  cpu
 apollo4l_evb             apollo4l       cortex-m4      dwt                  cpu
 apollo4l_blue_evb        apollo4l       cortex-m4      dwt                  cpu
 apollo4p_blue_kbr_evb    apollo4p       cortex-m4      dwt                  cpu
 apollo4p_blue_kxr_evb    apollo4p       cortex-m4      dwt                  cpu
 apollo510_evb            apollo510      cortex-m55     dwt, armv8m-pmu      cpu
                                                                             mve
 apollo510b_evb           apollo510b     cortex-m55     dwt, armv8m-pmu      cpu
                                                                             mve
 apollo5b_evb             apollo5b       cortex-m55     dwt, armv8m-pmu      cpu
                                                                             mve
 apollo330mP_evb          apollo330P     cortex-m55     dwt, armv8m-pmu      cpu
                                                                             mve
```

Cortex-M55 boards (AP5/AP330 family) expose the full Armv8-M PMU plus MVE
counters; Cortex-M4 boards (AP3/AP4 family) expose CPU counters via DWT
only. Use any board name in the `Board` column with `--board` or
`target.board:` in YAML.

## See also

- [Boards & Platforms](../guide/boards.md) — what each SoC family
  supports.
