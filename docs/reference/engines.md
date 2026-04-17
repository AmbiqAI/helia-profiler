# hpx engines

List available inference engines.

## Usage

```bash
hpx engines
```

## Output

Prints the engine identifiers that can be passed to `--engine`:

- `tflm` — Stock TensorFlow Lite Micro (CMSIS-NN)
- `helia-rt` — heliaRT (Ambiq optimized TFLM fork)
- `helia-aot` — heliaAOT (ahead-of-time compiled)
