# hpx profile

Profile a LiteRT model on Ambiq Apollo hardware.

## Usage

```bash
hpx profile [MODEL] [OPTIONS]
```

## Arguments

| Argument | Description |
|---|---|
| `MODEL` | Path to `.tflite` model file (or set in `hpx.yml`) |

## Options

| Flag | Type | Description |
|---|---|---|
| `--config` | path | YAML config file (`hpx.yml`) |
| `--engine` | choice | Inference engine: `tflm`, `helia-rt`, `helia-aot` |
| `--engine-config` | path | Engine-specific YAML config |
| `--board` | string | Target board (default: `apollo510_evb`) |
| `--toolchain` | string | Toolchain (default: `arm-none-eabi-gcc`) |
| `--arena-size` | int | Tensor arena size in bytes |
| `--pmu-presets` | list | PMU preset names (default: `basic_cpu`) |
| `--per-layer` / `--no-per-layer` | flag | Per-layer breakdown (default: on) |
| `--iterations` | int | Inference iterations (default: 100) |
| `--power` | flag | Enable Joulescope power capture |
| `--power-duration` | int | Power capture seconds (default: 30) |
| `--output-dir` | path | Results output directory |
| `--output-format` | choice | `csv` or `json` |
| `--no-model-explorer` | flag | Skip Model Explorer overlay generation |
| `--work-dir` | path | Working directory for generated firmware |
| `--keep-work-dir` | flag | Keep working directory after run |
| `-v` / `--verbose` | count | Increase verbosity (repeat for more) |
