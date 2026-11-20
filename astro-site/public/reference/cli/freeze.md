# hpx freeze

Freeze a resolved run plan to JSON for hpx build

## hpx freeze

```bash
hpx freeze [OPTIONS] [MODEL]
```

Freeze a resolved run plan to JSON for hpx build

Defined in `src/helia_profiler/cli/run_app.py` line 95.

### Arguments

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `model` | `Path \| None` |  | Path to .tflite or .pte model file |

### Options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--output, -o` | `Path` |  | Where to write the plan (JSON) Required. |
| `--config, -c` | `Path \| None` |  | YAML config file (hpx.yml) |
| `--verbose, -v` | `int` | 0 | Increase verbosity |
| `--iterations` | `int \| None` |  | Timed iterations (default: 100) |
| `--warmup` | `int \| None` |  | Warmup iterations (default: 5) |

### engine

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--engine` | `litert \| helia-rt \| helia-aot \| executorch` |  | Inference engine (default: helia-rt) |
| `--backend` | `str \| None` |  | Kernel backend (engine.backend). Run builds need helia for helia-rt and cmsis_nn for litert. |
| `--engine-config` | `Path \| None` |  | Engine-specific YAML config |
| `--arena-size` | `int \| None` |  | Tensor arena size in bytes |
| `--arena-location` | `tcm \| sram \| psram` |  | Tensor arena placement. Omit to let the engine and memory planner choose. |
| `--weights-location` | `tcm \| sram \| mram \| psram` |  | Model weights placement. Omit to let the engine and memory planner choose. |
| `--core-override` | `cm4 \| cm55` |  | Select the core library in an explicit heliaRT prebuilt distribution. |

### target hardware

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--board` | `str \| None` |  | Target board (default: apollo510_evb) |
| `--toolchain` | `str \| None` |  | Toolchain (default: arm-none-eabi-gcc; run builds use atfe) |
| `--cpu-clock` | `str \| None` |  | CPU clock speed (board-specific, e.g. 'lp'/'hp'; run builds use lp) |

### Examples

```text
Takes the config file and the engine, target and profiling options of hpx profile, plus --backend, and writes the resolved plan with the model's hash and the toolchain and runtime identities, so any host can build the same run.

Example:

  hpx freeze model.tflite --engine helia-rt --backend helia \
      --toolchain atfe --cpu-clock lp --arena-size 131072 \
      --arena-location sram --weights-location mram -o plan.json
```

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__` with typer 0.27.3 and click 8.3.3.
