# `hpx analyze`

Analyze a model's compute and parameter breakdown without hardware.

## Synopsis

```bash
hpx analyze MODEL [--engine helia-rt|helia-aot] [--compare]
            [--format table|csv|json] [--output FILE] [--board BOARD]
```

## Description

Performs a static, host-only analysis of a `.tflite` model: per-operator
MACs, parameter counts, and tensor sizes. No board, probe, or firmware
build is involved.

- With no `--engine`, the raw `.tflite` graph is analyzed as-is.
- `--engine helia-rt` analyzes the original graph (heliaRT executes the
  tflite graph directly).
- `--engine helia-aot` runs AOT compilation first and analyzes the
  transformed graph, so operator fusion and layout changes are reflected.
- `--compare` shows a side-by-side view of the original vs the
  engine-transformed graph.

## Options

| Flag | Description |
| --- | --- |
| `--engine` | Analyze as this engine would execute it (default: raw graph). |
| `--compare` | Side-by-side comparison of original vs transformed graph. |
| `--format` | Output format: `table` (default), `csv`, or `json`. |
| `--output`, `-o` | Write output to a file instead of the terminal. |
| `--board` | Target board for AOT compilation (default: `apollo510_evb`). |

## Examples

```bash
hpx analyze model.tflite
hpx analyze model.tflite --engine helia-aot --board apollo510_evb
hpx analyze model.tflite --format csv --output analysis.csv
hpx analyze model.tflite --engine helia-aot --compare
```
