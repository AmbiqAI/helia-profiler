---
hide:
  - navigation
  - toc
---

<div class="landing" markdown>

<div class="hero" markdown>

![heliaPROFILER](./assets/heliaprofiler-logo-light.png#only-light){ .hero-logo }
![heliaPROFILER](./assets/heliaprofiler-logo-dark.png#only-dark){ .hero-logo }

# Profile LiteRT models on Ambiq Apollo hardware.

One command to build, flash, capture PMU counters, and generate reports —
across TFLM, heliaRT, and heliaAOT engines.
{ .hero-sub }

[Get Started](getting-started/index.md){ .md-button .md-button--primary }
[View on GitHub](https://github.com/AmbiqAI/helia-profiler){ .md-button }

</div>

---

## Features at a Glance

<div class="grid cards feature-cards" markdown>

-   :material-speedometer: __End-to-end profiling__

    ---

    Cycle counts, instruction counts, cache stats, and per-layer PMU
    breakdowns — all from a single `hpx profile` command.

-   :material-engine: __Multi-engine support__

    ---

    Profile the same model across stock TFLM, heliaRT, and heliaAOT to
    compare inference performance.

-   :material-chip: __Apollo 3 / 4 / 5__

    ---

    Built-in platform model for every Ambiq SoC family. Full Armv8-M PMU on
    AP5, DWT cycle counts on AP3/AP4.

-   :material-lightning-bolt: __Power measurement__

    ---

    Optional Joulescope integration for current/voltage traces alongside
    PMU data.

-   :material-graph: __Model Explorer overlays__

    ---

    Export per-layer metrics as JSON overlays for Google's Model Explorer —
    see hot operators at a glance.

-   :material-file-cog: __YAML + CLI config__

    ---

    Declarative `hpx.yml` config merged with CLI flags.
    Frozen, immutable config — no surprises mid-run.

</div>

---

## How it Works

```bash
pip install helia-profiler        # install
hpx doctor                        # check toolchain
hpx profile model.tflite          # profile with defaults
hpx profile model.tflite \
    --engine helia-aot \
    --board apollo510_evb \
    --pmu-presets full_cpu mve     # full control
```

The pipeline handles firmware generation, build, flash, data capture, and
report output — each step is a modular stage that can fail with clear,
actionable error messages.
{ .section-sub }

---

## Where to Start

<div class="grid cards" markdown>

-   :material-rocket-launch: __New here?__

    ---

    Install the toolchain and profile your first model in minutes.

    [Getting Started →](getting-started/index.md)

-   :material-book-open-variant: __Configuration__

    ---

    YAML config, CLI flags, engine options, and board selection.

    [User Guide →](guide/configuration.md)

-   :material-flask-outline: __Examples__

    ---

    Walkthroughs for common profiling scenarios.

    [Examples →](examples/index.md)

-   :material-console-line: __CLI Reference__

    ---

    Every `hpx` subcommand, flag, and option.

    [Reference →](reference/index.md)

</div>

</div>
