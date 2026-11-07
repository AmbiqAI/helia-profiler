# hpx calibrate

Write a heliaOptimus calibration file from helia-aot result bundles

## hpx calibrate

```bash
hpx calibrate [OPTIONS] BUNDLES
```

Write a heliaOptimus calibration file from helia-aot result bundles

Defined in `src/helia_profiler/cli/calibrate_app.py` line 38.

### Arguments

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `bundles` | `list[Path]` |  | helia-aot result directories of one model under one set of conditions Required. |

### Options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--output, -o` | `Path` |  | Calibration file to write (schema version 1) Required. |
| `--merge` | `bool` | false | Read the existing --output file first and hold its prices to the same rules |
| `--tolerance` | `float` | 0.02 | Largest relative disagreement two prices of one value may have, as a fraction (default: 0.02, two percent) |

### Examples

```text
Examples:

  hpx calibrate results/kws-auto results/kws-mve --output kws-calibration.json

  hpx calibrate results/kws-sram --output kws-calibration.json --merge

Each bundle contributes the cycles of every operator its plan lists with knobs, as operators[<id>][<knob>][<value>]; an operator without knobs has no slot in the file and is left out. Every bundle must profile the same model under the same recorded conditions, with no overflowed layer, and two bundles pricing one value must agree within --tolerance; anything else is an error.
```

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__` with typer 0.27.3 and click 8.3.3.
