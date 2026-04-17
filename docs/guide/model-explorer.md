# Model Explorer Overlays

!!! note "Under construction"
    This page will cover Model Explorer integration in detail.

heliaPROFILER can export per-layer profiling metrics as JSON overlays for
[Google's Model Explorer](https://github.com/google-ai-edge/model-explorer).

## How It Works

After profiling, overlay files are written alongside the primary report:

```
results/
├── profile_results.csv
├── me_overlay_cycles.json
├── me_overlay_instructions.json
└── me_overlay_cache_misses.json
```

Each overlay maps node keys (output tensor names) to numeric values with a
green → yellow → red gradient.

## Disabling

```bash
hpx profile model.tflite --no-model-explorer
```

Or in `hpx.yml`:

```yaml
output:
  model_explorer: false
```
