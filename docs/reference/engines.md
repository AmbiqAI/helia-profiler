# `hpx engines`

List all inference engines available to the profiler.

## Synopsis

```bash
hpx engines
```

## Output

```
  tflm
  helia-rt
  helia-aot
```

- `helia-rt` — AmbiqAI heliaRT, an optimized TFLM fork (HELIA / CMSIS-NN /
  reference backends).
- `helia-aot` — AmbiqAI heliaAOT, an ahead-of-time compiler (no interpreter
  at runtime).
- `tflm` — vanilla TFLM baseline through the `nsx-tflite-micro` port;
  supports `reference` and upstream `cmsis_nn` backends.

Use either name with `--engine` or `engine.type:` in YAML.

## See also

- [Inference Engines](../guide/engines.md) — full description of each
  engine, when to use it, and engine-specific config.
