# `hpx validate`

Run the hardware-in-the-loop validation suite (MLPerf Tiny models).

## Synopsis

```bash
hpx validate [--models IDS] [--engines LIST] [--boards LIST]
             [--models-file YAML | --model-paths PATH,...]
             [--comparison-group NAME] [--model-arena-size BYTES]
             [--toolchains LIST] [--interfaces LIST] [--memories LIST]
             [--power off|on|both] [--power-boards BOARD,...]
             [--suite NAME] [--repeat N]
             [--jlink-serials BOARD=SERIAL,...] [--power-serials BOARD=SERIAL,...]
             [--power-gpios BOARD=GATE:STATE:GO,...] [--timeout SECONDS]
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
| `--models-file` | YAML registry defining custom model IDs, paths, arena sizes, and comparison groups. |
| `--model-paths` | Comma-separated `.tflite` paths for a quick ad hoc comparison. |
| `--comparison-group` | Shared decision group for `--model-paths` models (default: `custom`). |
| `--model-arena-size` | Arena size for `--model-paths` models (default: 524288 bytes). |
| `--engines` | Comma-separated engines: `helia-rt`, `helia-aot`, `tflm` (aliases `rt`, `aot`). Default: all. TFLM validation uses upstream CMSIS-NN. |
| `--boards` | Comma-separated board IDs (default: `apollo510_evb`). |
| `--toolchains` | Comma-separated toolchains: `gcc`, `armclang`/`acfe`, `atfe` (default: board defaults). |
| `--interfaces`, `--transports` | Comma-separated transports: `rtt`, `uart`, `swo`, `usb_cdc` (default: board defaults). |
| `--memories` | Comma-separated placement presets: `auto`, `tcm`, `sram`, `mram`, `psram` (default: board defaults). |
| `--power` | Power matrix: `off` (default), `on` (only Joulescope runs), or `both`. |
| `--power-boards` | Restrict powered cases to these boards; other selected boards run unpowered. By default, `--power` applies to every selected board. |
| `--suite` | Preset suite: `smoke`, `models-rt`, `models-aot`, or `complete`. Explicit axis flags always win. |
| `--jlink-serials` | `board=serial` entries for multi-board validation. |
| `--power-serials` | `board=Joulescope-serial` entries for powered multi-board validation; required when multiple Joulescopes are visible. |
| `--power-gpios` | `board=gate:state:go` entries for boards without registered power-sync wiring (for example, `apollo330mP_evb=5:6:7`). |
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
- `complete` — combined RT + AOT + TFLM/CMSIS-NN sweep across all MLPerf Tiny models on
  Apollo510 + Apollo330mP with gcc + ATfE.

## Custom models and comparison groups

Use a YAML registry when variants need explicit IDs or different arena sizes.
Relative paths are resolved from the registry file:

```yaml
models:
  kws-base:
    path: models/kws_base.tflite
    comparison_group: kws
    arena_size: 65536

  kws-pruned:
    path: models/kws_pruned.tflite
    comparison_group: kws
    arena_size: 49152
```

```bash
hpx validate --suite smoke --models-file variants.yml
```

For a quick comparison, paths can be supplied directly. Their IDs are derived
from filename stems and all paths share the requested comparison group:

```bash
hpx validate --suite smoke \
  --model-paths models/kws_base.tflite,models/kws_pruned.tflite \
  --comparison-group kws \
  --model-arena-size 65536
```

The Rich summary calculates `fastest`, `smallest`, and Pareto decisions only
within each comparison group. Built-in models use their own IDs as groups, so
unrelated workloads are not ranked against one another.

## Examples

```bash
hpx validate                          # default reliability matrix, power off
hpx validate --list                   # preview what would run
hpx validate --models kws,ic          # subset by model
hpx validate --suite smoke            # quick single-case sanity check
hpx validate --suite complete         # full RT + AOT + TFLM/CMSIS-NN hardware sweep
hpx validate --suite complete --power on --power-boards apollo510_evb
hpx validate -k kws-aot               # pytest-style keyword filter
hpx validate --boards apollo3p_evb --repeat 2
```

## Dashboard provenance

Each case in `validation_manifest.json` includes a
`provenance.runtime` object for dashboard ingestion. It contains the selected
compiler and its version, the CMake version, and the resolved engine type and
version when the engine publishes one (heliaRT or heliaAOT). These fields are
provenance, not part of the case identity used for comparisons.

Schema v3 also includes `resources.binary_sections`,
`resources.runtime_memory`, and `resources.memory_plan` for each case in both
machine-readable validation reports. The memory plan exposes per-region
capacity, used/free bytes, overflow state, and named consumers so dashboards do
not need to parse individual run summaries.

Schema v4 adds dashboard-ready power fields for powered cases and preserves the
complete per-run power object as `power_metrics`. The portable artifact index
points to `<case>/detailed/power_summary.csv`; powered validation cases enable
that detailed output automatically.
