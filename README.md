# heliaPROFILER

**`hpx`** profiles LiteRT (`.tflite`) models on real Ambiq Apollo silicon.
One command resolves an inference engine, builds temporary NSX firmware,
flashes the target, captures measurements, and writes a portable result bundle.

> **Alpha.** heliaPROFILER is pre-1.0. Breaking changes may land on **minor**
> versions until v1.0. Pin an exact version in production pipelines.

📖 Full docs: **https://ambiqai.github.io/helia-profiler/**

## Why hpx

- **Per-layer PMU breakdowns** — cycles, instructions, cache, and (on
  Cortex-M55 boards) MVE and memory counter groups, one row per layer.
- **Power & energy per inference** — GPIO-gated Joulescope capture
  (JS110/JS220/JS320) uses a dedicated transport-free firmware image to
  isolate the inference window from setup/teardown and capture traffic.
- **Three engines** — `tflm` (vanilla TFLM baseline), `helia-rt` (Ambiq's
  optimized TFLM interpreter), and `helia-aot` (Ambiq's ahead-of-time model
  compiler), selected explicitly
  per run.
- **Multiple toolchains** — `arm-none-eabi-gcc`, `armclang`, and ATfE, so you
  can compare build/runtime trade-offs without changing your model.
- **Memory placement control** — place the heliaRT tensor arena in TCM, SRAM,
  or PSRAM and weights in TCM, SRAM, MRAM, or PSRAM; heliaAOT exposes
  per-tensor placement through its engine configuration.
- **Four capture transports** — lossless RTT by default, plus USB CDC, UART,
  and diagnostic SWO, with probe, port, and target-reset helpers for bring-up.
- **Host-only model analysis** — inspect MACs, parameters, tensor sizes, and
  heliaAOT graph transforms without connecting hardware.
- **Run comparison and regression policy** — compare two result bundles,
  inspect the largest layer deltas, and apply versioned metric thresholds.
- **Model Explorer overlays** — export per-layer metrics as JSON overlays
  for [Model Explorer](https://github.com/google-ai-edge/model-explorer).
- **Config-file driven** — a frozen, immutable `hpx.yml` schema merges with
  CLI flags, with strict validation and did-you-mean suggestions for typos.
- **Multi-board** — Apollo3, Apollo4, and Apollo5-family EVBs. Run
  `hpx boards` for the exact list your install supports.
- **Verifiable result bundles** — summaries, per-layer data, provenance,
  validity/comparability issues, and a SHA-256 manifest designed for scripts
  as well as people.
- **Typed Python API** — call `profile()` directly or use immutable,
  branchable `Session` workflows in notebooks and automation.

## Install

### Nix

The flake provides the complete environment on x86-64 Linux, ARM64 Linux, and
Apple Silicon macOS. After reviewing and accepting
[SEGGER's J-Link terms](https://www.segger.com/downloads/jlink/), run:

```bash
nix run .#prepare-jlink -- --accept-license && nix develop
```

Linux hardware users must also install the USB rules once:

```bash
nix run .#install-udev-rules
```

### Other installation options

```bash
pip install helia-profiler
# or
uv tool install helia-profiler
```

Extras: `helia-profiler[aot]` adds the heliaAOT compiler;
`helia-profiler[analysis]` enables model compute/parameter analysis without
hardware. Python 3.11 or 3.12 is required. Hardware prerequisites for these
installation methods are covered in
[Getting Started](https://ambiqai.github.io/helia-profiler/getting-started/).

## Quick taste

```bash
hpx doctor                                   # check toolchain + dependencies
hpx profile model.tflite                     # profile with defaults
hpx profile model.tflite --power             # add Joulescope power capture
hpx profile --config hpx.yml                 # reproducible, config-driven run
hpx analyze model.tflite                     # inspect the model without hardware
hpx compare results/baseline results/change  # compare two completed runs
```

`hpx profile` defaults to heliaRT, GNU Arm, RTT, `apollo510_evb`, per-layer
CPU counters, 100 measured iterations, and 5 warmups. Select a different
engine, board, toolchain, transport, memory plan, PMU selection, or output
directory explicitly on the command line or in YAML.

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

## Development

```bash
uv sync --locked --all-groups
uv run ruff check src tests tools
uv run pytest -q
uv run mkdocs build --strict
```

Repository workflows use the committed `uv.lock` for reproducibility. PyPI
installations continue to resolve the compatible dependency ranges published in
`pyproject.toml`.

## License

Apache-2.0
