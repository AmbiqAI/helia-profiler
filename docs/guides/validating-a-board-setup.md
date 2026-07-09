# Validating a Board Setup

You've wired up a new EVB (or a bench you haven't touched in a while) and
before you trust any profiling numbers off it, you want proof the whole
chain — toolchain, probe, flash, capture — actually works. `hpx validate`
runs the canonical MLPerf Tiny models end-to-end against real hardware and
reports pass/fail per case. This guide walks the path from "does anything
work" to a wider confidence sweep.

## 1. Confirm the host side first

Before touching hardware, make sure the toolchain and Python dependencies
are present:

```bash
hpx doctor
```

```
Toolchain Check
╭────┬────────────────────────────────────┬────────────────────────────────────╮
│ ✓  │ ARM GCC toolchain                  │ /usr/local/arm-gnu-toolchain/bin/… │
│ ✓  │ CMake (>= 3.24)                    │ /home/you/.local/bin/cmake         │
│ ✓  │ SEGGER J-Link commander            │ /usr/bin/JLinkExe                  │
╰────┴────────────────────────────────────┴────────────────────────────────────╯

  All required tools found.
```

## 2. Confirm the probe is visible

```bash
hpx probes list
```

```
serial      product                    connection
----------  -------------------------  ----------
801000001  J-Link-OB-Apollo4-CortexM  USB
```

If you have more than one probe connected and need to know which one HPX
will pick for a given board:

```bash
hpx probes match --board apollo510_evb
```

This resolves the serial using HPX's normal selection policy — the same
logic `hpx profile` and `hpx validate` use — so you can catch a
misidentified probe before a run, not during one.

## 3. Run the smoke suite

`--suite smoke` is the fastest useful check: one model (`kws`), heliaRT,
GCC, RTT, `auto` placement, power off.

```bash
hpx validate --suite smoke --list
```

```
Registered models: ad, ic, kws, vww
Registered boards: apollo330mP_evb, apollo3p_evb, apollo4p_blue_kxr_evb, apollo510_evb

1 case(s) would run:

  apollo510_evb-kws-rt-arm-none-eabi-gcc-rtt-auto      helia-rt   arm-none-eabi-gcc   rtt   auto
```

`--list` previews the matrix without touching hardware — always check it
before running blind, especially once you start widening axes. Drop
`--list` to actually run it:

```bash
hpx validate --suite smoke
```

## 4. Interpret the results

Each case gets its own artifact directory under `--output-dir` (default
`./results/validation`), plus two summary reports at the top level:
`validation_report.md` (human-readable, one row per case with status,
duration, cycles, and any error note) and `validation_report.json` /
`validation_manifest.json` (machine-readable, with paths to each case's
`summary.json`, `run_metadata.json`, and build/work directories).

```bash
cat results/validation/validation_report.md
```

```
# heliaPROFILER - Hardware Validation Report

- total: **1**
- pass: **1**
- fail: **0**
- skip: **0**

| Case | Status | Duration (s) | Toolchain | Interface | Memory | Layers | Cycles | ... |
|------|--------|-------------:|-----------|-----------|--------|-------:|-------:|-----|
| apollo510_evb-kws-rt-... | pass | 42.3 | arm-none-eabi-gcc | rtt | auto | 13 | 2016376 | ... |
```

A `fail` row's **Notes** column carries the error — check the case's own
`summary.json`/`run_metadata.json` (paths are in the manifest) for the full
picture before re-running.

## 5. Widen the matrix once smoke passes

Each axis is an independent comma-separated flag; unset axes fall back to
board defaults (or, for `--suite`, the suite's preset defaults). Explicit
flags always win over the suite preset:

```bash
hpx validate --models kws,ic --engines helia-rt --boards apollo510_evb \
    --toolchains gcc,atfe --interfaces rtt --memories auto,tcm --list
```

Two broader presets exist for common sweeps:

```bash
hpx validate --suite models-rt --list    # 16 cases: 2 boards x 4 models x 2 toolchains, helia-rt
hpx validate --suite models-aot --list   # 12 cases: 3 boards x 4 models, gcc, helia-aot
```

For multi-board setups, map each board to its own probe serial so the
runner doesn't have to guess:

```bash
hpx validate --boards apollo510_evb,apollo3p_evb \
    --jlink-serials apollo510_evb=801000001,apollo3p_evb=801000002
```

## 6. Use `--repeat` for stability checks

A single pass proves the chain *can* work; it doesn't prove it works
reliably (flaky USB, marginal wiring, intermittent flash). `--repeat N`
runs each selected case N times and reports every run:

```bash
hpx validate --suite smoke --repeat 3
```

Require every repeat to pass before trusting a bench for unattended runs —
a case that's flaky at `--repeat 2` will be flaky in CI.

## Where to go deeper

- `hpx validate --help` — every flag, alias, and the built-in suite
  descriptions.
- [Hardware Validation Artifacts](../guide/hardware-ci.md) — what's written
  to disk and how to wire `hpx validate` into CI.
- [Boards & Platforms](../guide/boards.md) — board IDs and per-board
  defaults referenced by the axis flags above.
