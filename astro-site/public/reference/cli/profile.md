# hpx profile

Profile a model on target hardware

## hpx profile

```bash
hpx profile [OPTIONS] [MODEL]
```

Profile a model on target hardware

Defined in `src/helia_profiler/cli/app.py` line 93.

### Arguments

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `model` | `Optional[Path]` |  | Path to .tflite or .pte model file |

### Options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--config` | `Optional[Path]` |  | YAML config file (hpx.yml) |
| `--verbose, -v` | `int` | 0 | Increase verbosity |

### advanced

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--work-dir` | `Optional[Path]` |  | Working directory for generated firmware |
| `--clean` | `bool` | false | Wipe cached build directory before building (forces full rebuild) |

### build overrides

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--offline` | `bool` | false | Require exact compatible lock/module reuse without network resolution. |
| `--update-dependencies` | `bool` | false | Explicitly re-resolve dependency refs and rewrite nsx.lock. |
| `--nsx-channel` | `Optional[str]` |  | NSX channel for module resolution (default: the board's registered channel). |
| `--nsx-module` | `Optional[list[str]]` |  | Override an NSX module's source. Repeatable. Keys: path (local dir), ref (git ref/tag), version (pin). Examples: --nsx-module nsx-core:path=/my/nsx-core --nsx-module nsx-cmsis-core:ref=feat/new-cmsis --nsx-module nsx-gpio:version=2.0.0 Repeatable. |
| `--compiler-launcher` | `Optional[str]` |  | CMake compiler launcher to cache compiles (e.g. sccache, ccache). 'auto' (default) uses sccache/ccache if installed; a name or path requires it to be found. Overrides build.compiler_launcher; the HPX_COMPILER_LAUNCHER env var overrides both. |
| `--no-compiler-launcher` | `bool` | false | Disable the compiler launcher (equivalent to --compiler-launcher none). |

### engine

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--engine` | `tflm \| helia-rt \| helia-aot \| executorch` |  | Inference engine (default: helia-rt) |
| `--engine-config` | `Optional[Path]` |  | Engine-specific YAML config |
| `--arena-size` | `Optional[int]` |  | Tensor arena size in bytes |
| `--arena-location` | `tcm \| sram \| psram` |  | Tensor arena placement. helia-rt: places the single runtime tensor arena. helia-aot: use engine.config.aot_args.memory.tensors instead. Omit to let the engine and memory planner choose. |
| `--weights-location` | `tcm \| sram \| mram \| psram` |  | Model weights placement. helia-rt: places the model flatbuffer (psram requires J-Link upload via the RTT transport). helia-aot: use engine.config.aot_args.memory.tensors instead. Omit to let the engine and memory planner choose. |
| `--core-override` | `cm4 \| cm55` |  | Force heliaRT to use a specific core library variant (e.g. cm4 to disable MVE kernels on an M55 board). |

### output

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--output-dir` | `Optional[Path]` |  | Results output directory |
| `--output-format` | `csv \| json` |  | Output format |
| `--no-model-explorer` | `bool` | false | Skip Model Explorer overlay generation |
| `--detailed` | `bool` | false | Emit detailed per-preset/group CSVs and memory breakdown |
| `--fail-on-invalid` | `bool` | false | Exit 3 when the run evaluates INVALID (artifacts still written) |

### PMU profiling

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--pmu-counters` | `Optional[list[str]]` |  | PMU counter selection per compute unit. Repeatable. Format: GROUP:SELECT where GROUP is a supported group for the target SoC (for example cpu/mve/memory on Cortex-M55) and SELECT is 'default', 'all', or comma-separated counter names. Examples: --pmu-counters cpu:default --pmu-counters mve:all, --pmu-counters mve:ARM_PMU_MVE_INST_RETIRED,ARM_PMU_MVE_STALL Repeatable. |
| `--per-layer` | `Optional[bool]` |  | Per-layer breakdown (default) |
| `--iterations` | `Optional[int]` |  | Inference iterations (default: 100) |
| `--warmup` | `Optional[int]` |  | Warmup iterations (default: 5) |
| `--aggregation` | `mean \| median \| trimmed` |  | How per-layer counters are aggregated across iterations (default: median). 'median' rejects corrupted iterations; 'trimmed' drops extremes then means; 'mean' is the raw average. |

### power measurement

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--power` | `bool` | false | Enable power capture |
| `--power-driver` | `joulescope \| ondevice` |  | Power driver (default: joulescope = auto-detect JS110/JS220/JS320) |
| `--power-mode` | `external \| internal` |  | Power mode (default: external) |
| `--power-duration` | `Optional[int]` |  | Power capture seconds (default: auto-tuned from profile timing) |
| `--power-firmware` | `dedicated \| shared` |  | Which binary is on target during power capture (default: dedicated). 'dedicated' flashes the transport-free hpx_profiler_power image to avoid SWO/UART/RTT/USB current contamination (measured on AP510 EVBs); 'shared' reuses the already-flashed transport binary. |
| `--power-reset-strategy` | `auto \| power_cycle \| none \| debug_reset \| swpoi_reset \| debug_reset+swpoi_reset` |  | Reset strategy before power capture (default: auto). Use explicit values only for board bring-up or controlled experiments. |
| `--sync-gpio` | `Optional[int]` |  | GPIO pin for external power sync (default: per-board; 10 only for boards without a registered override) |
| `--ensure-power` | `bool` | false | Scan for a Joulescope at start-up and enable current passthrough so the board powers on before flashing. Off by default; only needed when the board's power genuinely comes from the Joulescope rail (--power already implies this). |
| `--no-ensure-power` | `bool` | false | Explicitly skip the auto power-on step, overriding --ensure-power or a config file's ensure_board_powered: true. |
| `--power-serial` | `Optional[str]` |  | Power instrument serial number to disambiguate when multiple devices are connected (e.g. Joulescope serial '004204'). Alias: --js-serial. |

### target hardware

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--board` | `Optional[str]` |  | Target board (default: apollo510_evb) |
| `--toolchain` | `Optional[str]` |  | Toolchain (default: arm-none-eabi-gcc) |
| `--jlink-serial` | `Optional[str]` |  | J-Link probe serial number (default: auto-detect) |
| `--transport` | `rtt \| usb_cdc \| swo \| uart` |  | Data transport (default: rtt). RTT is recommended for lossless capture. |
| `--usb-port` | `Optional[str]` |  | Explicit USB CDC device path for --transport usb_cdc (for example /dev/ttyACM1). |
| `--rtt-buffer-size-up` | `Optional[int]` |  | SEGGER RTT up-buffer size for generated RTT firmware. If too small, non-blocking writes during timed inference may be dropped, while blocking CSV/HPX_END writes may stall long enough to hit host timeouts. If omitted, hpx uses a toolchain-aware default. |
| `--cpu-clock` | `Optional[str]` |  | CPU clock speed for generated firmware (board-specific, e.g. 'lp'/'hp'). Default: the board's lowest-power tier. |
| `--frozen` | `bool` | false | Deprecated alias for --offline: require the compatible lock and materialized module state without dependency resolution. |

### Examples

```text
Quick start:

  hpx profile my_model.tflite

  hpx profile --config hpx.yml

  hpx profile my_model.tflite --engine helia-rt --power -vv
```

Generated from the `src/helia_profiler` tree `17bfdf5c68d6c73c3d22f7afb063ac9cd722152f` with typer 0.26.8 and click 8.3.3.
