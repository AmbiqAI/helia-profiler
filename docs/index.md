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

One command to build, flash, capture PMU counters, and generate reports —
with heliaRT or heliaAOT.
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

-   :material-engine:{ .lg .middle } __Two engines__

    ---

    Profile the same model with heliaRT (interpreter) or heliaAOT
    (ahead-of-time compiler) — one explicitly-chosen engine per run.

-   :material-chip:{ .lg .middle } __Apollo 3 / 4 / 5__

    ---

    Built-in platform definitions for every Ambiq SoC family. Full
    Armv8‑M PMU on AP5, DWT cycle counts on AP3/AP4.

-   :material-lightning-bolt:{ .lg .middle } __Power measurement__

    ---

    Optional Joulescope integration for current and voltage traces
    alongside PMU data.

-   :material-graph:{ .lg .middle } __Model Explorer overlays__

    ---

    Export per-layer metrics as JSON overlays for Google's
    [Model Explorer](https://github.com/nicholasjng/model-explorer) —
    see hot operators at a glance.

-   :material-file-cog:{ .lg .middle } __YAML + CLI config__

    ---

    Declarative config merged with CLI flags.
    Frozen and immutable — no surprises mid-run.

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

The pipeline handles firmware generation, build, flash, data capture, and
report output. Each step is a modular
[stage](architecture/pipeline.md) that fails with clear, actionable errors.
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

</div>

!!! note "About the numbers in these docs"
    Sample power, energy, and latency values shown throughout this site
    (mA, mW, µJ, cycle counts) are illustrative placeholders, not real
    captured measurements. Your own hardware, model, and configuration
    will produce different numbers — run `hpx profile` to get yours.

</div>
