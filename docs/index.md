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
across TFLM, heliaRT, and heliaAOT.
{ .hero-sub }

[Get Started :material-arrow-right:](getting-started/index.md){ .md-button .md-button--primary }
[GitHub](https://github.com/AmbiqAI/helia-profiler){ .md-button }

</div>

---

## Features

<div class="grid cards" markdown>

-   :material-speedometer:{ .lg .middle } __End-to-end profiling__

    ---

    Cycle counts, instruction counts, cache stats, and per-layer PMU
    breakdowns — all from a single `hpx profile` command.

-   :material-engine:{ .lg .middle } __Multi-engine support__

    ---

    Profile the same model across stock TFLM, heliaRT, and heliaAOT to
    compare inference performance.

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
hpx profile model.tflite \
    --engine helia-aot \
    --board apollo510_evb \
    --pmu-presets full_cpu mve       # (4)!
```

1.  Install heliaPROFILER and its dependencies.
2.  Check that the ARM toolchain, J-Link, and NSX are available.
3.  Profile with defaults — heliaRT on Apollo 510, CPU counters.
4.  Full control — choose engine, board, and counter presets.

The pipeline handles firmware generation, build, flash, data capture, and
report output. Each step is a modular
[stage](architecture/pipeline.md) that fails with clear, actionable errors.
{ .section-sub }

---

## Where to start

<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } __New here?__

    ---

    Install the toolchain and profile your first model in minutes.

    [:octicons-arrow-right-24: Getting Started](getting-started/index.md)

-   :material-book-open-variant:{ .lg .middle } __Configuration__

    ---

    YAML config, CLI flags, engine options, and board selection.

    [:octicons-arrow-right-24: User Guide](guide/configuration.md)

-   :material-flask-outline:{ .lg .middle } __Examples__

    ---

    Walkthroughs for common profiling scenarios.

    [:octicons-arrow-right-24: Examples](examples/index.md)

-   :material-console-line:{ .lg .middle } __CLI Reference__

    ---

    Every `hpx` subcommand, flag, and option.

    [:octicons-arrow-right-24: Reference](reference/index.md)

</div>

</div>
