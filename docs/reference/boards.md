# `hpx boards`

List all boards registered in the platform registry.

## Synopsis

```bash
hpx boards
```

## Output

```
Board                    SoC          Core         Backends             Domains                   Channel
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 apollo3p_evb             apollo3p     cortex-m4    dwt                  cpu                       stable
 apollo3p_evb_cygnus      apollo3p     cortex-m4    dwt                  cpu                       preview
 apollo4p_evb             apollo4p     cortex-m4    dwt                  cpu                       preview
 apollo4l_evb             apollo4l     cortex-m4    dwt                  cpu                       preview
 apollo4l_blue_evb        apollo4l     cortex-m4    dwt                  cpu                       preview
 apollo4p_blue_kbr_evb    apollo4p     cortex-m4    dwt                  cpu                       preview
 apollo4p_blue_kxr_evb    apollo4p     cortex-m4    dwt                  cpu                       preview
 apollo510_evb            apollo510    cortex-m55   dwt, armv8m-pmu      cpu, mve                  stable
 apollo510b_evb           apollo510b   cortex-m55   dwt, armv8m-pmu      cpu, mve                  preview
 apollo5b_evb             apollo5b     cortex-m55   dwt, armv8m-pmu      cpu, mve                  preview
 apollo330mP_evb          apollo330P   cortex-m55   dwt, armv8m-pmu      cpu, mve                  preview
 apollo510dL_evb          apollo510L   cortex-m55   dwt, armv8m-pmu      cpu, mve                  preview
 atomiq110_fpga_turbo     atomiq110    cortex-m55   dwt, armv8m-pmu      cpu, mve, ethos_npu       preview
```

!!! warning "Experimental Atomiq110 support"
    HPX support for the Atomiq110 SoC and `atomiq110_fpga_turbo` board is
    experimental. It is best-effort, is not a release blocker, and may change
    or be removed in any minor release. It is outside the compatibility
    guarantees for production-silicon targets.

    This FPGA target's clock rates, cycle counts, latency, power, and energy
    measurements describe the FPGA image only and are not representative of
    production silicon. The literal `preview` value above is preserved because
    it is the board's registered channel; this documentation-only status does
    not change CLI behavior or NSX module resolution.

Cortex-M55 boards (AP5/AP330 family) expose the full Armv8-M PMU plus MVE
counters; Cortex-M4 boards (AP3/AP4 family) expose CPU counters via DWT
only. `atomiq110_fpga_turbo` additionally has an Ethos-U85 NPU, exposing
the `ethos_npu` counter group (requires `engine.backend: ethos_u`). Use any
board name in the `Board` column with `--board` or
`target.board:` in YAML.

## See also

- [Boards & Platforms](../guide/boards.md) — what each SoC family
  supports.
