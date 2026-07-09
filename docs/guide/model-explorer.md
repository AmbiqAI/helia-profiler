# Model Explorer Overlays

heliaPROFILER exports per-layer profiling metrics as JSON overlays compatible
with [Google's Model Explorer](https://github.com/google-ai-edge/model-explorer).
This lets you visually inspect which operators are hot spots.

## How it works

After profiling, overlay files are written to the `model_explorer/` subfolder:

```
results/
├── summary.json
├── profile_results.csv
└── model_explorer/
    ├── me_overlay_ARM_PMU_CPU_CYCLES.json
    ├── me_overlay_ARM_PMU_INST_RETIRED.json
    ├── me_overlay_ARM_PMU_L1D_CACHE.json
    ├── me_overlay_ARM_PMU_MVE_INST_RETIRED.json
    └── ...
```

Each overlay maps graph node IDs to a numeric value with a gradient color
palette (green → yellow → red). One overlay file is generated per PMU counter.

## Using with Model Explorer

1. Open [Model Explorer](https://google-ai-edge.github.io/model-explorer/)
2. Load your `.tflite` model
3. Click **Add Overlay** and select one of the `me_overlay_*.json` files
4. Nodes are colored by the counter value — red = highest, green = lowest

!!! tip "Node ID matching"
    For **heliaRT** models, nodes are matched by sequential operator
    index (0, 1, 2, ...). For **heliaAOT**, nodes use the original TFLite
    operator index preserved through AOT compilation (e.g. the `3` in
    `CONV_2D:3`).

## Disabling

If you don't need overlays, disable them to skip file generation:

=== "CLI"

    ```bash
    hpx profile model.tflite --no-model-explorer
    ```

=== "YAML"

    ```yaml
    output:
      model_explorer: false
    ```

## Gradient palette

Overlays use a 5-stop gradient for value visualization:

| Value range | Color |
|---|---|
| Minimum | Green |
| 25th percentile | Light green |
| 50th percentile | Yellow |
| 75th percentile | Orange |
| Maximum | Red |

Zero-value nodes are rendered in neutral gray.
