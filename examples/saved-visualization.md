# Explore saved profiles

This offline experiment reads complete, valid ordinary JSON profile bundles.
It does not build, flash, capture, invoke a model or import a simulator. It
reuses the native manifest verifier and Model Explorer overlay writer.

From a checkout with the normal HPX environment:

```sh
uv run python examples/visualize_saved_profiles.py \
  --run baseline /path/to/baseline/results \
  --run candidate /path/to/candidate/results \
  --mapping /path/to/attribution.json \
  --output /path/to/new-visualization
```

One `--run` also works. Output must be a new directory outside both native
bundles. No source artifact is rewritten. The module and mapping format are
experimental, not additions to the common BENCH result schema or the stable
fixture API.

## Perfetto

Open `comparison.perfetto.json` at <https://ui.perfetto.dev>, or open one
`run-N.perfetto.json` for a single run. Each run has its own track group. Click
an operator slice for cycles, counters, nominal CPU frequency, aggregation,
source manifest identity and any supplied shapes/kernel annotation.

The horizontal axis is **cumulative aggregated operator cost**, in time units
using the recorded CPU frequency. It is not a captured wall-clock timeline.
Median layer costs may come from different invocations. Both runs start at
zero for visual comparison; this does not imply simultaneous execution or
matching clocks. PMU counter totals are slice attributes, not fabricated
time-varying samples. Separate PMU acquisition passes are not simultaneous.

The separate whole-invocation mean track retains the reported duration. It is
not used to rescale operator costs. The difference between a sum of medians
and a whole-invocation mean is not an exclusive measurement of overhead.
Power/energy, idle gaps, overlap and within-invocation sample alignment are
not inferred. This experiment does not reconstruct them.

## Model Explorer and comparison

With an explicit mapping, load the original model in Model Explorer and add
the `*.overlay.json` files using **Add per-node data**. `run-N.latency` shows
milliseconds; `run-N.counter-M` identifies the actual PMU metric in its display
name. Delta overlays show candidate minus baseline: red is an increase, blue a
decrease, gray zero. Positive latency delta means slower. A zero baseline has
no percentage delta and is omitted from that overlay, not assigned infinity.

`comparison.json` retains conditions, separate whole-model means, source
identities, attribution basis and unmatched events. `comparison.csv` contains
the matched-node changes and missing counterparts. This is a descriptive
view, not a qualified performance ratio or a numerical-equivalence verdict.
Different runtimes need not have corresponding events. An unmapped event
remains visible in Perfetto but receives no graph coloring or invented delta.

The optional mapping explicitly binds each event to a graph/node in the exact
source model and native result. Never populate it by assuming equal event IDs
mean equal source operators. Graph IDs must be those used by the viewer.
Shapes, kernels and the attribution basis remain caller-supplied annotations;
binding their identities does not independently prove their semantics.

```json
{
  "schema": "hpx.visualization-mapping",
  "schema_version": 1,
  "model_sha256": "<exact model SHA256>",
  "basis": "<existing mapping evidence and its limitations>",
  "runs": {
    "<native result manifest SHA256>": [
      {
        "event_id": 7,
        "event_name": "CONV_2D:12",
        "graph_id": "main",
        "node_id": "12",
        "input_shapes": [[1, 32, 32, 16]],
        "output_shapes": [[1, 16, 16, 32]],
        "kernel": "<recorded kernel identity>"
      }
    ]
  }
}
```

Many events mapping to one node are refused rather than silently summed. Fused
events may be left unmapped until an explicit group representation is available.
Without a mapping, the experiment writes cost traces and native summaries only.

The first retained experiment uses the PP-HumanSeg zero-input diagnostic:
107 individually source-verified operators per engine, medians of three
invocations, energy disabled. It is separate from the saved nonzero-input
energy cohort. No accuracy claim follows from the comparison.
