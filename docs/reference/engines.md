# `hpx engines`

List all inference engines available to the profiler.

## Synopsis

```bash
hpx engines
```

## Output

```
  helia-rt
  helia-aot
```

- `helia-rt` — AmbiqAI heliaRT, an optimized TFLM fork (HELIA / CMSIS-NN /
  reference backends).
- `helia-aot` — AmbiqAI heliaAOT, an ahead-of-time compiler (no interpreter
  at runtime).

Use either name with `--engine` or `engine.type:` in YAML.

Stock `tflm` is temporarily unavailable in the public CLI/config surface.

## See also

- [Inference Engines](../guide/engines.md) — full description of each
  engine, when to use it, and engine-specific config.
