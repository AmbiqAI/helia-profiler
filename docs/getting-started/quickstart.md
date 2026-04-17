# Quick Start

!!! note "Under construction"
    This page will walk through profiling a model end-to-end.

## Profile a Model

```bash
hpx profile my_model.tflite --board apollo510_evb
```

## View Results

Results are written to `./results/` by default:

- `profile_results.csv` — tabular per-layer breakdown
- `me_overlay_cycles.json` — Model Explorer overlay
