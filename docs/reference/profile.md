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
| `MODEL` | Path to a `.tflite` or ExecuTorch `.pte` model file. Optional if `model.path` is set in `--config`. |

## Top-level options

| Flag | Type | Default | Description |
|---|---|---|---|
| `--config FILE` | path | — | YAML config file (`hpx.yml`). CLI flags override its values. |
| `-v`, `--verbose` | count | 0 | Increase log verbosity. `-v` = INFO, `-vv` = DEBUG. |
| `-h`, `--help` | flag | — | Print help and exit. |

## Engine selection

| Flag | Type | Default | Description |
|---|---|---|---|
| `--engine` | `tflm` \| `helia-rt` \| `helia-aot` \| `executorch` | `helia-rt` | Inference engine. See [Engines](../guide/engines.md). TFLM and ExecuTorch backends are selected with `engine.backend` in YAML. |
| `--engine-config FILE` | path | — | Engine-specific YAML loaded into `engine.config`. |
| `--core-override` | `cm4` \| `cm55` | — | Force a heliaRT prebuilt core-library variant. Intended for controlled experiments. |

## Model

| Flag | Type | Default | Description |
|---|---|---|---|
| `--arena-size` | int | (engine-specific) | Tensor arena size in bytes. |
| `--arena-location` | `tcm` \| `sram` \| `psram` | — | Runtime tensor arena placement for heliaRT. |
| `--weights-location` | `tcm` \| `sram` \| `mram` \| `psram` | — | Runtime model/weights placement for heliaRT. |

For heliaAOT, use `engine.config.aot_args.memory.tensors` for fine-grained
tensor placement rather than the heliaRT arena/weights flags.

## Target hardware

| Flag | Type | Default | Description |
|---|---|---|---|
| `--board` | string | `apollo510_evb` | Target board. `hpx boards` lists options. |
| `--toolchain` | `arm-none-eabi-gcc` \| `gcc` \| `armclang` \| `atfe` | `arm-none-eabi-gcc` | Cross-compiler. See [Toolchains](../guide/toolchains.md). |
| `--jlink-serial` | string | auto-detect | Pin a specific J-Link probe by serial number. |
| `--transport` | `rtt` \| `usb_cdc` \| `uart` \| `swo` | `rtt` | Capture transport. RTT is recommended. See [Transports](../guide/transports.md). |
| `--usb-port` | path | auto-detect | Explicit serial device for `--transport usb_cdc`. |
| `--rtt-buffer-size-up` | bytes | toolchain-aware | RTT firmware up-buffer size. Increase carefully for very large captures. |
| `--cpu-clock` | board speed name | board lowest-power tier | CPU clock selection such as `lp` or `hp`, validated against the board. |
| `--frozen` | flag | off | Deprecated alias for `--offline`. |

## Build resolution

| Flag | Type | Default | Description |
|---|---|---|---|
| `--nsx-channel` | string | `stable` | NSX channel used for module resolution. |
| `--nsx-module NAME:KEY=VALUE` (repeatable) | mapping | — | Override one module with `path`, `ref`, or `version`. |
| `--compiler-launcher` | name/path | `auto` | Use `sccache`/`ccache` automatically or require an explicit launcher. |
| `--no-compiler-launcher` | flag | — | Disable compiler-launcher caching. |
| `--offline` | flag | off | Require a compatible exact lock and already-materialized module trees; never resolve refs. |
| `--update-dependencies` | flag | off | Explicitly refresh dependency refs and rewrite the exact lock. Mutually exclusive with offline/frozen mode. |

Without either mode, HPX resolves only when the lock is missing or structurally
incompatible. Compatible locks are reused without rewriting or contacting refs,
and synchronization is frozen.

## Profiling

| Flag | Type | Default | Description |
|---|---|---|---|
| `--pmu-counters GROUP:SELECT` (repeatable) | list | `cpu:default` | `SELECT` is `default`, `all`, or comma-separated counter names. Repeat for multiple groups, e.g. `--pmu-counters cpu:default --pmu-counters mve:all`. |
| `--per-layer` | flag | on | Per-layer breakdown (default). |
| `--no-per-layer` | flag | — | Disable per-layer breakdown; capture whole-model only. |
| `--iterations` | int | 100 | Inference iterations averaged in the report. |
| `--warmup` | int | 5 | Warmup iterations before measurement. |
| `--aggregation` | `median` \| `trimmed` \| `mean` | `median` | Estimator used across iterations. Median rejects occasional corrupted samples. |

## Power

| Flag | Type | Default | Description |
|---|---|---|---|
| `--power` | flag | off | Enable power capture. See [Power](../guide/power.md). |
| `--power-driver` | `joulescope` | `joulescope` | Auto-detect a JS110, JS220, or JS320. On-device measurement values are reserved but not implemented. |
| `--power-mode` | `external` | `external` | External GPIO-gated measurement. Internal mode is reserved but not implemented. |
| `--power-duration` | int | 30 | Capture window length in seconds. |
| `--power-firmware` | `dedicated` \| `shared` | `dedicated` | Binary flashed during power capture. `dedicated` uses a transport-free image to avoid transport current contamination; `shared` reuses the transport binary. See [Power](../guide/power.md#dedicated-power-firmware). |
| `--power-reset-strategy` | `auto` \| `power_cycle` \| `none` \| `debug_reset` \| `swpoi_reset` \| `debug_reset+swpoi_reset` | `auto` | Override reset behavior for board bring-up or controlled experiments. |
| `--sync-gpio` | int | board default (`29` on `apollo510_evb` / `apollo510b_evb`, `10` on most other built-in EVBs) | GPIO pin the firmware toggles around inference. |
| `--ensure-power` | flag | off | Enable Joulescope passthrough before flashing when the board is powered from that rail. Implied by `--power`. |
| `--no-ensure-power` | flag | — | Explicitly skip the passthrough power-on step. |
| `--power-serial`, `--js-serial` | string | auto-detect | Select one instrument when multiple Joulescopes are connected. |

## Output

| Flag | Type | Default | Description |
|---|---|---|---|
| `--output-dir DIR` | path | `./results` | Where to write the result manifest, summary, selected CSV/JSON primary result, metadata, and overlays. |
| `--output-format` | `csv` \| `json` | `csv` | Primary report format. |
| `--no-model-explorer` | flag | — | Skip Model Explorer overlay generation. |
| `--detailed` | flag | off | Emit per-pass/group CSVs and detailed memory/power data. |

Final human-readable result tables are written to stdout. Progress spinners,
stage logs, warnings, interruptions, and errors are written to stderr. This
keeps stdout available for durable command results and future machine-output
modes; JSON/CSV profile data is currently written to files in `--output-dir`.

## Build / debug

| Flag | Type | Default | Description |
|---|---|---|---|
| `--work-dir DIR` | path | persistent HPX cache workspace | Working directory for generated firmware. Set explicitly to inspect or retain a specific project location. |
| `--clean` | flag | off | Wipe the cached build directory before building. |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | HPX configuration, platform, engine, build, capture, power, or report failure |
| 2 | Typer/Click command-line usage error |
| 130 | Interrupted with Ctrl-C |

Typed category-specific exit codes are not yet part of the CLI contract. Error
types remain available to programmatic callers through the `HpxError` hierarchy.

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
hpx profile model.tflite --work-dir ./build
ls ./build/firmware/
```
