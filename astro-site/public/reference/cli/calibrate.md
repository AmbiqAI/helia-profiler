# hpx calibrate

Write a heliaOptimus calibration file from helia-aot result bundles

## hpx calibrate

```bash
hpx calibrate [OPTIONS] BUNDLES
```

Write a heliaOptimus calibration file from helia-aot result bundles

Defined in `src/helia_profiler/cli/calibrate_app.py` line 66.

### Arguments

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `bundles` | `list[Path]` |  | helia-aot result directories of one model under one set of conditions Required. |

### Options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--output, -o` | `Path` |  | Calibration file to write (schema version 1) Required. |
| `--merge` | `bool` | false | Read the existing --output file first and hold its prices to the same rules |
| `--tolerance` | `float` | 0.02 | Largest relative disagreement two prices of one value may have, as a fraction (default: 0.02, two percent); two timings in operator_cycles may also differ by 8 cycles |

### Examples

```text
Examples:

  hpx calibrate results/kws-auto results/kws-mve --output kws-calibration.json

  hpx calibrate results/kws-int16 --output kws-calibration.json --merge

Each bundle prices every operator its plan lists with knobs, as operators[<id>][<knob>][<value>], where an operator without knobs has no slot and is left out; and it times every operator the plan lists, with knobs or without, as operator_cycles[<id>], one entry of knobs and cycles per knob vector, so a knob sweep adds an entry. Every bundle must profile the same model under the same recorded conditions and setup: a digest of the run's frozen settings less the heliaAOT knob values (optimization.<knob> and operators[].optimization.<knob>), the power, output and timeout sections and the run-only fields, plus the resolved toolchain and engine, the configuration the heliaAOT compiler received, the build's ISA flags and the kernel build, recorded under capture.setup with the fields it covers; the hpx version and the contents of locally overridden modules are not held. capture.setup also records pairing_digest and pairing_fields: the same setup less where the constants sit (model.weights_location and the constants' placement rules), so a capture with the weights in MRAM or PSRAM and one with them in TCM share it; it is null for a bundle that does not record it and for a merge whose sources do not all share one. Every bundle must carry cycles the profiler did not flag: an invalid run counts only when the issue registry confines every error to power metrics, and a run whose cycle counter was flagged is refused whatever its verdict. Two sources pricing one value under the same other knobs must agree within --tolerance, and a second vector for one value is an error; two sources timing one operator under one knob vector must agree within --tolerance or within 8 cycles, whichever is looser, so --tolerance 0 is exact for prices but leaves the 8-cycle floor for operator_cycles; anything else is an error too. The plan's fixed choices and kernel entry are recorded with each price, never compared. Reading a bundle needs the aot extra installed, since the knob values are stripped by heliaAOT's own registry, and the file records the heliaAOT release the captures compiled with; captures compiled by different releases do not merge. --merge accepts only a file hpx wrote and keeps every price it holds; heliaOptimus reads a hand-written table directly. heliaOptimus matches the file when the planner passes the same conditions, including application_load=none and, with PSRAM, external_memory_clock.
```

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__` with typer 0.27.3 and click 8.3.3.
