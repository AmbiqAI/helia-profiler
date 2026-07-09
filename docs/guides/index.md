# In-Depth Guides

The [User Guide](../guide/configuration.md) documents individual features
(configuration, engines, memory placement, PMU counters, power) one at a
time. The [Examples](../examples/index.md) section gives you short,
copy-pasteable recipes. **This section is different: it walks through a
complete task-oriented pattern that combines several features**, in the
order you'd actually work through it on a bench.

Guides here are narrative — "you want X, here's the path, here's what to
check at each step" — with small snippets and links back into the
reference pages for the exact flags and fields. They assume you've already
done [Getting Started](../getting-started/index.md).

This section is expected to grow as more workflows get written up.

<div class="grid cards" markdown>

-   :material-memory:{ .lg .middle } __Memory Placement Tuning__

    ---

    Go from `auto` placement to a tuned, apples-to-apples comparison of
    arena/weights regions — and what to do when a plan overflows.

    [:octicons-arrow-right-24: Read](memory-placement-tuning.md)

-   :material-clipboard-check:{ .lg .middle } __Validating a Board Setup__

    ---

    Use `hpx validate` to prove a bench or bring-up board works
    end-to-end, then widen the matrix with confidence.

    [:octicons-arrow-right-24: Read](validating-a-board-setup.md)

</div>
