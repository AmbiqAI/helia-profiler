---
hide:
  - navigation
  - toc
  - footer
---

<div class="landing" markdown>

<div class="hero" markdown>

![heliaPROFILER](./assets/heliaprofiler-logo-light.png#only-light){ .hero-logo }
![heliaPROFILER](./assets/heliaprofiler-logo-dark.png#only-dark){ .hero-logo }

# Profile LiteRT models on Ambiq hardware.

Build, flash, measure, and compare LiteRT models with heliaRT, heliaAOT,
or a stock TFLM baseline.
{ .hero-sub }

[Get Started :material-arrow-right:](getting-started/index.md){ .md-button .md-button--primary }
[GitHub](https://github.com/AmbiqAI/helia-profiler){ .md-button }

</div>

!!! warning "Alpha"
    heliaPROFILER is pre-1.0. Breaking changes may land on **minor**
    versions until v1.0 — pin an exact version for anything long-lived.

---

## Features

<div class="grid cards" markdown>

-   :material-speedometer:{ .lg .middle } __End-to-end profiling__

    ---

    Cycle counts, instruction counts, cache stats, and per-layer PMU
    breakdowns — all from a single `hpx profile` command.

-   :material-engine:{ .lg .middle } __Four engines__

    ---

    Profile with vanilla TFLM, heliaRT (interpreter), heliaAOT
    (ahead-of-time compiler), or ExecuTorch — one explicit engine per run.

-   :material-chip:{ .lg .middle } __Apollo 3 / 4 / 5__

    ---

    Built-in platform definitions for every Ambiq SoC family. Full
    Armv8‑M PMU on AP5, DWT cycle counts on AP3/AP4.

-   :material-lightning-bolt:{ .lg .middle } __Power measurement__

    ---

    GPIO-gated JS110/JS220/JS320 capture with a dedicated transport-free
    firmware image for current, voltage, and energy per inference.

-   :material-magnify-scan:{ .lg .middle } __Host-only model analysis__

    ---

    Inspect MACs, parameters, tensor sizes, and AOT graph transforms before
    connecting a board.

-   :material-compare:{ .lg .middle } __Comparison and regression policy__

    ---

    Compare compatible result bundles, find the largest layer deltas, and
    apply versioned metric thresholds.

-   :material-graph:{ .lg .middle } __Model Explorer overlays__

    ---

    Export per-layer metrics as JSON overlays for
    [Model Explorer](https://github.com/google-ai-edge/model-explorer) —
    see hot operators at a glance.

-   :material-file-cog:{ .lg .middle } __YAML + CLI config__

    ---

    Declarative config merged with CLI flags.
    Frozen and immutable — no surprises mid-run.

-   :material-shield-check:{ .lg .middle } __Verifiable result bundles__

    ---

    Machine-readable summaries, per-layer data, provenance, validity issues,
    and a manifest with artifact sizes and SHA-256 digests.

-   :material-language-python:{ .lg .middle } __Python and notebook API__

    ---

    Typed `profile()` results and immutable `Session` workflows for
    interactive exploration and automation.

</div>

---

## How it works

```bash
pip install helia-profiler          # (1)!
hpx doctor                          # (2)!
hpx profile model.tflite            # (3)!
```

1.  Install heliaPROFILER and its dependencies.
2.  Check that the ARM toolchain, J-Link, and NSX are available.
3.  Profile with defaults — heliaRT, GCC, RTT capture, CPU counters, on the
    connected Apollo510 EVB. Results land in `./results/`.

The pipeline handles engine resolution, firmware generation, build, flash,
capture, and report output. Start with [Output & Results](guide/output.md) to
understand the portable bundle produced by a successful run.
{ .section-sub }

---

## Where to start

Pick the path that matches what you're trying to do:

<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } __First time here?__

    ---

    Install the toolchain and profile your first model in minutes.

    [:octicons-arrow-right-24: Getting Started](getting-started/index.md)

-   :material-book-open-variant:{ .lg .middle } __Configuring a run__

    ---

    YAML config, CLI flags, engine options, board selection, memory
    placement, PMU counters, and power measurement.

    [:octicons-arrow-right-24: User Guide](guide/configuration.md)

-   :material-flask-outline:{ .lg .middle } __Doing a specific task__

    ---

    Short recipes for common scenarios (basic profiling, engine
    comparison, power capture), plus worked patterns for multi-feature
    workflows.

    [:octicons-arrow-right-24: Examples](examples/index.md) ·
    [:octicons-arrow-right-24: In-Depth Guides](guides/index.md)

-   :material-console-line:{ .lg .middle } __Integrating or automating__

    ---

    Every `hpx` subcommand and flag, the configuration schema, and the
    `profile()` Python API for calling heliaPROFILER programmatically.

    [:octicons-arrow-right-24: Reference](reference/index.md)

-   :material-chart-box-outline:{ .lg .middle } __Analyzing or comparing__

    ---

    Inspect a model without hardware, compare two runs, or define regression
    thresholds for repeatable experiments.

    [:octicons-arrow-right-24: Analysis & Run Comparison](guide/analysis-comparison.md)

-   :material-notebook-edit-outline:{ .lg .middle } __Working in Python__

    ---

    Use the typed API and branchable session workflow from scripts or
    notebooks.

    [:octicons-arrow-right-24: Interactive Python](examples/interactive-python.md)

</div>

!!! note "About the numbers in these docs"
    Sample power, energy, and latency values shown throughout this site
    (mA, mW, µJ, cycle counts) are illustrative placeholders, not real
    captured measurements. Your own hardware, model, and configuration
    will produce different numbers — run `hpx profile` to get yours.

</div>
