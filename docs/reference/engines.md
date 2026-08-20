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
  executorch
```

- `tflm` — vanilla TFLM baseline through the `nsx-tflite-micro` port;
  supports `reference` and upstream `cmsis_nn` backends.
- `helia-rt` — AmbiqAI heliaRT, an optimized TFLM fork (HELIA / CMSIS-NN /
  reference backends).
- `helia-aot` — AmbiqAI heliaAOT, an ahead-of-time compiler (no interpreter
  at runtime).
- `executorch` — Cortex-M ExecuTorch programs (`.pte`) through
  `nsx-executorch`, with the `arm` or `ns` CMSIS-NN provider.

The first three consume LiteRT (`.tflite`) models; `executorch` consumes
`.pte` programs. Use any of these names with `--engine` or `engine.type:` in
YAML.

## See also

- [Inference Engines](../guide/engines.md) — full description of each
  engine, when to use it, and engine-specific config.
