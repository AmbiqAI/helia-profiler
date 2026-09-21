# hpx analyze

Analyze model compute/parameter breakdown (no hardware needed)

## hpx analyze

```bash
hpx analyze [OPTIONS] MODEL
```

Analyze model compute/parameter breakdown (no hardware needed)

Defined in `src/helia_profiler/cli/app.py` line 532.

### Arguments

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `model` | `Path` |  | Path to .tflite model file Required. |

### Options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--engine` | `tflm \| helia-rt \| helia-aot \| executorch` |  | Analyze as this engine would execute it. Default (no flag) uses the raw tflite graph. 'helia-aot' runs AOT compilation and analyzes the transformed graph. 'helia-rt' analyzes the original tflite graph. |
| `--compare` | `bool` | false | Show side-by-side comparison of original vs engine-transformed graph |
| `--format` | `table \| csv \| json` | table | Output format (default: table) |
| `--output, -o` | `Optional[Path]` |  | Write output to file |
| `--board` | `str` | apollo510_evb | Target board for AOT compilation (default: apollo510_evb) |

### Examples

```text
Analyze a .tflite model without hardware:

  hpx analyze model.tflite

  hpx analyze model.tflite --engine helia-aot --board apollo510_evb

  hpx analyze model.tflite --format csv --output analysis.csv

  hpx analyze model.tflite --engine helia-aot --compare
```

Generated from the `src/helia_profiler` tree `872d67ad4e9083b1eacd7d6d3859148437b96b59` with typer 0.26.8 and click 8.3.3.
