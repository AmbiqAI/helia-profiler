# `hpx profile`

Build profiler firmware, flash the target, capture PMU/power data, and
write a report.

## Synopsis

```bash
hpx profile [MODEL] [--config FILE] [options]
```

## Positional argument

| Argument | Description |
|---|---|
| `MODEL` | Path to a `.tflite` model file. Optional if `model.path` is set in `--config`. |

## Top-level options

| Flag | Type | Default | Description |
|---|---|---|---|
| `--config FILE` | path | — | YAML config file (`hpx.yml`). CLI flags override its values. |
| `-v`, `--verbose` | count | 0 | Increase log verbosity. `-v` = INFO, `-vv` = DEBUG. |
| `-h`, `--help` | flag | — | Print help and exit. |

## Engine selection

| Flag | Type | Default | Description |
|---|---|---|---|
| `--engine` | `helia-rt` \| `helia-aot` | `helia-rt` | Inference engine. See [Engines](../guide/engines.md). |
| `--engine-config FILE` | path | — | Engine-specific YAML loaded into `engine.config`. |

## Model

| Flag | Type | Default | Description |
|---|---|---|---|
| `--arena-size` | int | (engine-specific) | Tensor arena size in bytes. |
| `--arena-location` | `tcm` \| `sram` \| `psram` | — | Runtime tensor arena placement for heliaRT. Alias: `--runtime-arena-location`. |
| `--weights-location` | `tcm` \| `sram` \| `mram` \| `psram` | — | Runtime model/weights placement for heliaRT. Alias: `--runtime-weights-location`. |
| `--model-location` | `auto` \| `tcm` \| `sram` \| `mram` \| `psram` | `auto` | Compatibility preset for arena + weights. Prefer split placement flags. See [Memory](../guide/memory.md). |

## Target hardware

| Flag | Type | Default | Description |
|---|---|---|---|
| `--board` | string | `apollo510_evb` | Target board. `hpx boards` lists options. |
| `--toolchain` | `arm-none-eabi-gcc` \| `gcc` \| `armclang` \| `atfe` | `arm-none-eabi-gcc` | Cross-compiler. See [Toolchains](../guide/toolchains.md). |
| `--jlink-serial` | string | auto-detect | Pin a specific J-Link probe by serial number. |
| `--transport` | `rtt` \| `usb_cdc` \| `swo` | `rtt` | Capture transport. See [Transports](../guide/transports.md). |
| `--frozen` | flag | off | Use the existing `nsx.lock`/module state as-is instead of re-running dependency resolution/sync. Useful for fast, reproducible offline reruns once a build has already succeeded. |

## Profiling

| Flag | Type | Default | Description |
|---|---|---|---|
| `--pmu-counters NAME=SEL` (repeatable) | list | `cpu=default` | Counter selection per group. `SEL` is `default`, `all`, or a comma-separated counter list. Repeat the flag for multiple groups, e.g. `--pmu-counters cpu:default --pmu-counters mve:all`. |
| `--pmu-presets NAME` (repeatable) | list | `basic_cpu` | Legacy preset names (kept for backward compatibility). Repeat the flag for multiple presets. |
| `--per-layer` | flag | on | Per-layer breakdown (default). |
| `--no-per-layer` | flag | — | Disable per-layer breakdown; capture whole-model only. |
| `--iterations` | int | 100 | Inference iterations averaged in the report. |
| `--warmup` | int | 5 | Warmup iterations before measurement. |

## Power

| Flag | Type | Default | Description |
|---|---|---|---|
| `--power` | flag | off | Enable power capture. See [Power](../guide/power.md). |
| `--power-driver` | `joulescope` \| `joulescope-js110` \| `joulescope-js220` \| `ondevice` | `joulescope` | Power instrument driver. |
| `--power-mode` | `external` \| `internal` | `external` | External Joulescope vs on-device measurement. |
| `--power-duration` | int | 30 | Capture window length in seconds. |
| `--power-firmware` | `dedicated` \| `shared` | `dedicated` | Binary flashed during power capture. `dedicated` uses a transport-free image to avoid transport current contamination; `shared` reuses the transport binary. See [Power](../guide/power.md#dedicated-power-firmware). |
| `--sync-gpio` | int | board default (`29` on `apollo510_evb` / `apollo510b_evb`, `10` on most other built-in EVBs) | GPIO pin the firmware toggles around inference. |

## Output

| Flag | Type | Default | Description |
|---|---|---|---|
| `--output-dir DIR` | path | `./results` | Where to write `summary.json`, `profile_results.csv`, and overlays. |
| `--output-format` | `csv` \| `json` | `csv` | Primary report format. |
| `--no-model-explorer` | flag | — | Skip Model Explorer overlay generation. |
| `--detailed` | flag | off | Emit per-preset CSVs and a memory plan dump. |

## Build / debug

| Flag | Type | Default | Description |
|---|---|---|---|
| `--work-dir DIR` | path | tempdir | Working directory for generated firmware. Useful for debugging the generated NSX project. |
| `--keep-work-dir` | flag | off | Don't delete the work directory at exit. |
| `--compiler-launcher NAME` | string | `auto` | CMake compiler launcher to cache compiles. `auto` uses `sccache`/`ccache` if installed; a name/path requires it to be found. Overridden by `HPX_COMPILER_LAUNCHER`. |
| `--no-compiler-launcher` | flag | — | Disable the compiler launcher (same as `--compiler-launcher none`). |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | `ConfigError` — bad YAML or invalid CLI combination |
| 2 | `PlatformError` — unsupported board/SoC |
| 3 | `EngineError` — engine adapter failure (download, AOT compile, etc.) |
| 4 | `FirmwareError` — template rendering failure |
| 5 | `BuildError` — NSX build failure |
| 6 | `CaptureError` — flash, transport, or PMU capture failure |
| 7 | `PowerError` — Joulescope driver failure |
| 8 | `ReportError` — report generation failure |

## Examples

### Quickest possible run

```bash
hpx profile model.tflite
```

### Full repeatable run with a config

```bash
hpx profile --config hpx.yml
```

### Override a few fields

```bash
hpx profile --config hpx.yml \
  --board apollo3p_evb \
  --iterations 50 \
  --output-dir ./results/ap3p
```

### Compare engines (two runs)

```bash
hpx profile model.tflite --engine helia-rt  --output-dir results/rt
hpx profile model.tflite --engine helia-aot --output-dir results/aot
```

### Compare toolchains

```bash
hpx profile model.tflite --toolchain gcc      --output-dir results/gcc
hpx profile model.tflite --toolchain armclang --output-dir results/armclang
```

### Add power capture

```bash
hpx profile model.tflite --power --power-duration 10
```

### Inspect generated firmware

```bash
hpx profile model.tflite --keep-work-dir --work-dir ./build
ls ./build/firmware/
```
