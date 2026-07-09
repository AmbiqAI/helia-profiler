# heliaPROFILER

**`hpx`** profiles LiteRT (`.tflite`) models on real Ambiq Apollo silicon —
one command builds temporary firmware, flashes it, and returns per-layer PMU
counter breakdowns plus optional Joulescope power/energy per inference.

> **Alpha.** heliaPROFILER is pre-1.0. Breaking changes may land on **minor**
> versions until v1.0. Pin an exact version in production pipelines.

📖 Full docs: **https://ambiqai.github.io/helia-profiler/**

## Why hpx

- **Per-layer PMU breakdowns** — cycles, instructions, cache, and (on
  Cortex-M55 boards) MVE and memory counter groups, one row per layer.
- **Power & energy per inference** — GPIO-gated Joulescope capture
  (JS110/JS220) isolates the inference window from setup/teardown noise.
- **Two engines** — `helia-rt` (Ambiq's optimized TFLM interpreter) and
  `helia-aot` (Ambiq's ahead-of-time model compiler), selected explicitly
  per run.
- **Multiple toolchains** — `arm-none-eabi-gcc`, `armclang`, and ATfE, so you
  can compare build/runtime trade-offs without changing your model.
- **Memory placement control** — pin the tensor arena and model weights to
  TCM, SRAM, MRAM, or PSRAM independently.
- **Model Explorer overlays** — export per-layer metrics as JSON overlays
  for [Model Explorer](https://github.com/google-ai-edge/model-explorer).
- **Config-file driven** — a frozen, immutable `hpx.yml` schema merges with
  CLI flags, with strict validation and did-you-mean suggestions for typos.
- **Multi-board** — Apollo3, Apollo4, and Apollo5-family EVBs. Run
  `hpx boards` for the exact list your install supports.

## Install

```bash
pip install helia-profiler
# or
uv tool install helia-profiler
```

Extras: `helia-profiler[aot]` adds the heliaAOT compiler;
`helia-profiler[analysis]` enables model compute/parameter analysis without
hardware.

Hardware prerequisites (ARM toolchain, SEGGER J-Link, and optional
Joulescope drivers) are covered step by step in
[Getting Started](https://ambiqai.github.io/helia-profiler/getting-started/).

## Quick taste

```bash
hpx doctor                                   # check toolchain + dependencies
hpx profile model.tflite                     # profile with defaults
hpx profile model.tflite --power             # add Joulescope power capture
hpx profile --config hpx.yml                 # reproducible, config-driven run
```

```text
  Layer  Op                  ARM_PMU_CPU_CYCLES  ARM_PMU_INST_RETIRED
  0      CONV_2D                        123,456                98,765
  1      DEPTHWISE_CONV_2D               45,678                34,567
  ...
  Power:  1.234 mA avg   12.345 mW avg   x.xxx µJ / inference
```

*(Illustrative sample only — see
[Getting Started](https://ambiqai.github.io/helia-profiler/getting-started/)
for a real walkthrough.)*

## What it does NOT do

- Run multiple inference engines in a single invocation
- Export AmbiqSuite examples or static libraries
- Provide a GUI

## Development

```bash
uv sync --all-groups
uv run ruff check src tests tools
uv run pytest -q
uv run mkdocs build --strict
```

## License

Apache-2.0
