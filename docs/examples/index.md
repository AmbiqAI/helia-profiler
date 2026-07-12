# Examples

Short, runnable recipes for common profiling scenarios. Each page is a
**recipe, not a guide** — a minimal config, the command to run, and a peek
at the output. For the full mechanics behind a topic (counter selection,
placement policy, power wiring), follow the "Where to go deeper" links at
the end of each page into the [User Guide](../guide/configuration.md).

Every recipe follows the same shape:

1. **Goal** — what you'll accomplish, in one sentence.
2. **Setup** — the config (YAML and/or CLI flags) you need.
3. **Run** — the command.
4. **What you get** — a short excerpt of the output.
5. **Where to go deeper** — links to the guide pages with full depth.

!!! note "About the numbers"
    Sample outputs on these pages (cycle counts, mA, mW, µJ) are
    illustrative placeholders, not real captured measurements — see the
    note on the [home page](../index.md).

<div class="grid cards" markdown>

-   :material-play:{ .lg .middle } __Basic Profiling__

    ---

    Profile a model with default settings.

    [:octicons-arrow-right-24: Read](basic-profiling.md)

-   :material-layers:{ .lg .middle } __Per-Layer Breakdown__

    ---

    Analyze operator-level cycle counts and PMU events.

    [:octicons-arrow-right-24: Read](per-layer.md)

-   :material-scale-balance:{ .lg .middle } __Vanilla TFLM Baseline__

    ---

    Measure the vanilla TFLM port with reference kernels or upstream CMSIS-NN.

    [:octicons-arrow-right-24: Read](tflm-baseline.md)

-   :material-swap-horizontal:{ .lg .middle } __Engine Comparison__

    ---

    Compare heliaRT and heliaAOT on the same model.

    [:octicons-arrow-right-24: Read](engine-comparison.md)

-   :material-lightning-bolt:{ .lg .middle } __Power Profiling__

    ---

    Capture current/voltage traces with Joulescope.

    [:octicons-arrow-right-24: Read](power-profiling.md)

-   :material-tools:{ .lg .middle } __Toolchain Comparison__

    ---

    Compare GCC, armclang, and ATfE on the same model and engine.

    [:octicons-arrow-right-24: Read](toolchain-comparison.md)

</div>
