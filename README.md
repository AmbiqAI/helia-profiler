# heliaPROFILER

**`hpx`** — Profile LiteRT models on Ambiq Apollo hardware.

Captures per-layer PMU counter breakdowns and optional power measurements
for a single explicitly-chosen inference engine per run.

## Install

```bash
pipx install helia-profiler
```

## Quick Start

```bash
# Check toolchain and dependencies
hpx doctor

# Profile a model with stock TFLM
hpx profile model.tflite --engine tflm --arena-size 65536

# Profile with heliaRT (HELIA backend)
hpx profile model.tflite --engine helia-rt --arena-size 65536

# Profile with heliaAOT
hpx profile model.tflite --engine helia-aot --engine-config aot_config.yml

# Use a config file for reproducible runs
hpx profile --config hpx.yml
```

## Supported Engines

| Engine | Description |
| --- | --- |
| `tflm` | Stock TensorFlow Lite for Microcontrollers with CMSIS-NN |
| `helia-rt` | Ambiq's optimized TFLM fork with HELIA/CMSIS-NN/reference backends |
| `helia-aot` | Ambiq's ahead-of-time model compiler |

## What It Does

1. Generates a temporary NSX profiler firmware for your model and engine
2. Builds and flashes it to the target board
3. Captures per-layer PMU counters (cycles, instructions, cache, MVE, etc.)
4. Optionally captures power measurements via Joulescope
5. Outputs results as CSV, JSON, or terminal summary

## What It Does NOT Do

- Export AmbiqSuite examples or static libraries
- Run multiple inference engines in one invocation
- Auto-detect arena sizes via recompilation
- Transfer models over RPC
- Provide a GUI (future scope)

## Configuration

See [SPEC.md](SPEC.md) for the full `hpx.yml` schema and CLI reference.

## Development

```bash
# Install in development mode
uv sync --all-groups

# Run linter
uv run --group lint ruff check .

# Run tests
uv run --group test pytest -q
```

## License

Apache-2.0
