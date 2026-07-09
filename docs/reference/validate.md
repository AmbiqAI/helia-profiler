# `hpx validate`

Run the hardware-in-the-loop validation suite (MLPerf Tiny models).

## Synopsis

```bash
hpx validate [--models IDS] [--engines LIST] [--boards LIST]
             [--toolchains LIST] [--interfaces LIST] [--memories LIST]
             [--power off|on|both] [--suite NAME] [--repeat N]
             [--jlink-serials BOARD=SERIAL,...] [--timeout SECONDS]
             [--output-dir DIR] [--junit-xml FILE] [-k EXPR] [--list]
```

## Description

Runs canonical MLPerf Tiny models end-to-end against a real EVB and
J-Link probe (and optionally a Joulescope for power runs). Each selected
case is a full profile run — build, flash, capture, report — with
pass/fail criteria, making this the recommended way to validate a board
setup or gate hardware changes in CI.

See [Validating a Board Setup](../guides/validating-a-board-setup.md) and
[Hardware CI](../guide/hardware-ci.md) for workflow-oriented guidance.

## Options

| Flag | Description |
| --- | --- |
| `--models` | Comma-separated model IDs (default: all). See `hpx validate --list`. |
| `--engines` | Comma-separated engines: `helia-rt`, `helia-aot` (aliases `rt`, `aot`). Default: both. |
| `--boards` | Comma-separated board IDs (default: `apollo510_evb`). |
| `--toolchains` | Comma-separated toolchains: `gcc`, `armclang`/`acfe`, `atfe` (default: board defaults). |
| `--interfaces`, `--transports` | Comma-separated transports: `rtt`, `uart`, `swo`, `usb_cdc` (default: board defaults). |
| `--memories` | Comma-separated placement presets: `auto`, `tcm`, `sram`, `mram`, `psram` (default: board defaults). |
| `--power` | Power matrix: `off` (default), `on` (only Joulescope runs), or `both`. |
| `--suite` | Preset suite: `smoke`, `models-rt`, `models-aot`, or `complete`. Explicit axis flags always win. |
| `--jlink-serials` | `board=serial` entries for multi-board validation. |
| `--repeat` | Repeat each selected case N times for stress testing (default: 1). |
| `--timeout` | Per-case timeout in seconds (default: 900). |
| `--output-dir` | Per-case artifacts + summary report location (default: `./results/validation`). |
| `--junit-xml` | Emit a JUnit-XML report (for CI consumption). |
| `-k` | Pytest-style keyword expression to filter cases (e.g. `kws-aot`). |
| `--list` | List matching cases and exit without running. |

## Preset suites

- `smoke` — quick single-case check: KWS, heliaRT, gcc, RTT, auto memory.
- `models-rt` — RT sweep across all MLPerf Tiny models on Apollo510 +
  Apollo330mP with gcc + ATfE.
- `models-aot` — AOT sweep across all MLPerf Tiny models on Apollo510 +
  Apollo330mP with gcc + ATfE.
- `complete` — combined RT + AOT sweep across all MLPerf Tiny models on
  Apollo510 + Apollo330mP with gcc + ATfE.

## Examples

```bash
hpx validate                          # default reliability matrix, power off
hpx validate --list                   # preview what would run
hpx validate --models kws,ic          # subset by model
hpx validate --suite smoke            # quick single-case sanity check
hpx validate --suite complete         # full RT + AOT hardware sweep
hpx validate -k kws-aot               # pytest-style keyword filter
hpx validate --boards apollo3p_evb --repeat 2
```
