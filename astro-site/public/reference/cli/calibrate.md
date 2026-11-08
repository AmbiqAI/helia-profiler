# hpx calibrate

Write a heliaOptimus calibration file from helia-aot result bundles

## hpx calibrate

```bash
hpx calibrate [OPTIONS] BUNDLES
```

Write a heliaOptimus calibration file from helia-aot result bundles

Defined in `src/helia_profiler/cli/calibrate_app.py` line 69.

### Arguments

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `bundles` | `list[Path]` |  | helia-aot result directories of one model under one set of conditions Required. |

### Options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--output, -o` | `Path` |  | Calibration file to write (calibration schema 2) Required. |
| `--merge` | `bool` | false | Read the existing --output file first and hold its samples to the same rules |
| `--tolerance` | `float` | 0.02 | Largest relative disagreement two samples of one operator and knob vector may have, as a fraction of the larger (default: 0.02, two percent); they may always differ by 8 cycles |

### Examples

```text
Examples:

  hpx calibrate results/kws-auto results/kws-mve --output kws-calibration.json

  hpx calibrate results/kws-int16 --output kws-calibration.json --merge

The file is calibration schema 2: each bundle times every operator its plan lists, with knobs or without, as operator_cycles[<id>], one entry of knobs, cycles and facts per knob vector, where knobs is the whole vector and facts are the plan's fixed choices and kernel entry. Any set of knob vectors merges, one knob at a time from a common baseline, several at once or every combination, since each vector is its own sample. Two samples of one operator and knob vector must agree within --tolerance of the larger or within 8 cycles, whichever is looser, so --tolerance 0 still allows 8 cycles, and they must report the same facts. Every bundle must profile the same model under the same recorded conditions and setup: a digest of the run's frozen settings less the heliaAOT knob values (optimization.<knob> and operators[].optimization.<knob>), the power, output and timeout sections and the run-only fields, plus the resolved toolchain and engine, the configuration the heliaAOT compiler received, the build's ISA flags and the kernel build, recorded under capture.setup with the fields it covers; the hpx version and the contents of locally overridden modules are not held. capture.setup also records pairing_digest and pairing_fields: the same setup less where the constants sit (model.weights_location and the constants' placement rules), so captures that differ only in where the weights sit share it. memory.allocate_arenas stays held and PSRAM weights need it false, so a pair that includes PSRAM shares it only when every capture of the pair runs with memory.allocate_arenas: false. It is null for a bundle that does not record it and for a merge whose sources do not all share one. Every bundle must carry cycles the profiler did not flag: an invalid run counts only when the issue registry confines every error to power metrics, and a run whose cycle counter was flagged is refused whatever its verdict. Reading a bundle needs the aot extra installed, since the knob values are stripped by heliaAOT's own registry, and the file records the heliaAOT release the captures compiled with; captures compiled by different releases do not merge. --merge accepts only a calibration schema 2 file hpx wrote and keeps every sample it holds; a schema 1 file is refused and must be written again from its bundles without --merge, and a file of a newer schema is merged with the release that wrote it; heliaOptimus reads a hand-written table directly. The file's schema is helia_profiler/data/calibration.schema.v2.json. heliaOptimus matches the file when the planner passes the same conditions, including application_load=none and, with PSRAM, external_memory_clock.
```

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__` with typer 0.27.3 and click 8.3.3.
