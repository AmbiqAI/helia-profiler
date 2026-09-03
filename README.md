# heliaPROFILER

**`hpx`** profiles LiteRT (`.tflite`) and ExecuTorch (`.pte`) models on real
Ambiq Apollo silicon.
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
- **Four engines, one per run** — `tflm` (vanilla TFLM baseline),
  `helia-rt` (Ambiq's optimized TFLM interpreter), `helia-aot` (Ambiq's
  ahead-of-time model compiler), and `executorch` (Cortex-M ExecuTorch
  programs, on the `arm` or `ns` CMSIS-NN provider). Selected explicitly —
  never auto-detected.
- **Multiple toolchains** — `arm-none-eabi-gcc`, `armclang`, and ATfE, so you
  can compare build/runtime trade-offs without changing your model.
- **Memory placement control** — place the heliaRT tensor arena in TCM, SRAM,
  or PSRAM and weights in TCM, SRAM, MRAM, or PSRAM; heliaAOT exposes
  per-tensor placement through its engine configuration, and ExecuTorch
  places each of its five runtime buffers independently.
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
- **Hardware-in-the-loop validation** — `hpx validate` runs canonical MLPerf
  Tiny models end-to-end across engines, toolchains, transports, and memory
  placements, and emits a portable bundle two runs can be compared from.
- **Typed Python API** — call `profile()` directly or use immutable,
  branchable `Session` workflows in notebooks and automation.

## Install

See the
[Quick install](https://ambiqai.github.io/helia-profiler/getting-started/install/#quick-install)
for Nix, uv, and pip setup.

## Quick taste

```bash
hpx doctor                                   # check toolchain + dependencies
hpx profile model.tflite                     # profile with defaults
hpx profile model.tflite --power             # add Joulescope power capture
hpx profile --config hpx.yml                 # reproducible, config-driven run
hpx analyze model.tflite                     # inspect the model without hardware
hpx compare results/baseline results/change  # compare two completed runs
hpx validate --suite smoke                   # prove the whole bench works
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
uv sync --locked --all-groups --extra aot --extra analysis
uv run ruff check .
uv run ty check src/helia_profiler tests
uv run pytest -q
uv run --group docs zensical build
```

`pytest` deselects the `hardware` marker by default, so the whole unit suite
runs with no board attached. Hardware cases run through `hpx validate` (or
`pytest -m hardware`).

### Pre-commit hooks

One-time setup after cloning:

```bash
uv tool install pre-commit
pre-commit install
```

This installs both hook stages the repo uses: `pre-commit` (formatting,
whitespace/YAML/JSON/TOML checks, ruff, a gate requiring every
`TODO(...)`/`FIXME(...)`/`HACK(...)` marker to carry a reference, and a
[gitleaks](https://github.com/gitleaks/gitleaks) secret scan —
see the install message from a commit if `gitleaks` isn't on your `PATH`
yet) and `prepare-commit-msg` (strips AI-tool attribution trailers from the
commit message). `pre-push` is left untouched, so git-lfs's own `pre-push`
hook keeps working.

Run every hook against the whole tree with `pre-commit run --all-files`.
CI runs the identical `.pre-commit-config.yaml`, so a clean local run means
a clean CI run. Hook revisions are bumped deliberately via `pre-commit
autoupdate` in its own reviewed PR, not ad hoc.

The docs site is built with [zensical](https://github.com/squidfunk/zensical)
from `mkdocs.yml` and deployed to GitHub Pages from `main`; `uv run mkdocs
build --strict` still works if you prefer it locally.

Repository workflows use the committed `uv.lock` for reproducibility. PyPI
installations continue to resolve the compatible dependency ranges published in
`pyproject.toml`.

Architectural rules, module responsibilities, and the repo workflows that
should stay stable are documented in [`AGENTS.md`](AGENTS.md) — worth reading
before a first change, whether you are a person or an agent.

## License

Apache-2.0
