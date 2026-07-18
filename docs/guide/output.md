# Output & Results

heliaPROFILER produces a structured set of output files organized for
progressive disclosure — high-level summaries by default, detailed breakdowns
on request.

## Output directory structure

### Default output (always generated)

```
results/
├── result_manifest.json    # Versioned bundle envelope + artifact digests
├── summary.json            # Machine-readable high-level summary
├── profile_results.csv     # Merged per-layer PMU breakdown (all counters)
├── run_metadata.json       # Config, toolchain, platform, model info
├── aot_operator_manifest.json # AOT only: compiled operators + tensor placement
├── aot_memory_layers.csv   # AOT only: spreadsheet-friendly per-layer buffers
└── model_explorer/         # Model Explorer overlay JSONs
    ├── me_overlay_ARM_PMU_CPU_CYCLES.json
    ├── me_overlay_ARM_PMU_INST_RETIRED.json
    └── ...
```

### Detailed output (with `--detailed`)

```
results/
├── summary.json
├── profile_results.csv
├── run_metadata.json
├── model_explorer/
│   └── ...
└── detailed/
    ├── memory.json                # Memory breakdown (binary, arena, per-layer cache)
    ├── profile_cpu.csv            # Merged CPU group results
    ├── profile_memory.csv         # Merged memory group results
    ├── profile_mve.csv            # Merged MVE group results
    ├── profile_cpu_0.csv          # Per-pass CPU breakdowns
    ├── profile_cpu_1.csv
    ├── profile_memory_0.csv
    ├── profile_mve_0.csv
    └── ...
```

## File reference

### result_manifest.json

The publication marker for a completed result bundle. HPX writes this file
after every other report artifact. It records the run identity, validity,
structured issues, open provenance and comparability fields, plus each
artifact's relative path, media type, size, and SHA-256 digest.

The v1 schema intentionally fixes only the envelope. `provenance`,
`comparability`, `extensions`, issue context, artifact metadata, and unknown
root fields remain open for additive evolution. Consumers must ignore fields
they do not use. Use `load_result_manifest(path, verify=True)` to reject missing,
modified, or path-escaping artifacts.

Each declared artifact may include additive semantic metadata:

| Field | Meaning |
| --- | --- |
| `role` | `core`, `projection`, `extension`, `export`, or `diagnostic` |
| `name` | Semantic artifact name used for discovery |
| `schema` | Content-schema identity only when a published schema actually exists |
| `schema_version` | Version of that published content schema, independent of the bundle version |
| `producer` | Component or exporter that generated the artifact |
| `optional` | Whether a valid bundle may omit this product |

Complete `profile` bundles require named core artifacts for `summary.json`,
`run_metadata.json`, and the selected primary profile result. Detailed CSV/JSON files are projections or
diagnostics. heliaAOT files are engine extensions. Model Explorer overlays are
optional exports governed by the Model Explorer format rather than the HPX core
schema. Semantic names do not claim a published content schema. `schema` and
`schema_version` remain absent until HPX or an external owner publishes one.
Optional means the product need not be generated; once an artifact is
declared in a manifest, verification still requires its file and digest.

The permissive JSON Schema is shipped as
`helia_profiler/data/result_manifest.schema.v1.json`.

HPX-owned JSON artifacts carry independent schema identities and versions so
consumers can evolve parsers without coupling every file to the bundle schema:

| Artifact | Schema | Packaged JSON Schema |
| --- | --- | --- |
| `summary.json` | `hpx.run-summary` v1 | `run_summary.schema.v1.json` |
| `run_metadata.json` | `hpx.run-metadata` v1 | `run_metadata.schema.v1.json` |
| `profile_results.json` | `hpx.profile-results` v1 | `profile_results.schema.v1.json` |

These schemas require the stable interpretation fields and remain open to
additive measurements and extensions. CSV output retains its semantic artifact
name but does not claim a formal content schema yet.

### summary.json

The top-level summary — start here for a quick overview.

```json
{
  "schema": "hpx.run-summary",
  "schema_version": 1,
  "engine": "helia-rt",
  "layers": 13,
  "total_cycles": 2016376,
  "overflow_detected": false,
  "top_layers": [
    {"op": "CONV_2D", "cycles": 338176, "pct": 16.8},
    {"op": "CONV_2D", "cycles": 207749, "pct": 10.3}
  ],
  "memory": {
    "arena_size": 131072,
    "allocated_arena": 29780,
    "model_size": 53936,
    "num_tensors": 35,
    "input_size": 490,
    "output_size": 12
  },
  "binary": {
    "text": 573968,
    "data": 14952,
    "bss": 163516,
    "total": 752436
  },
  "cache": {
    "ARM_PMU_L1D_CACHE": 230224,
    "ARM_PMU_L1D_CACHE_RD": 230203,
    "ARM_PMU_L1D_CACHE_REFILL": 0,
    "ARM_PMU_L1D_CACHE_MISS_RD": 0,
    "ARM_PMU_DTCM_ACCESS": 1338037,
    "ARM_PMU_MEM_ACCESS": 1568463,
    "l1d_hit_rate_pct": 100.0
  }
}
```

| Section | Contents |
|---|---|
| Top-level | Engine, layer count, total cycles, overflow flag |
| `top_layers` | Top 5 layers by cycle count with percentages |
| `memory` | Arena allocation, model size, tensor counts |
| `binary` | ELF section sizes (text, data, bss) from `arm-none-eabi-size` |
| `cache` | Aggregated cache/memory PMU counters + derived L1D hit rate |
| `power` | Power summary (when Joulescope capture is enabled) |

Every summary also includes top-level `validity` and `issues` fields. Validity
is `valid`, `degraded`, or `invalid`; issues carry stable codes, severity,
human guidance, and open context. Consumers should inspect these fields before
using headline metrics.

### profile_results.csv

The primary data file — one row per layer with all measured PMU counters.

```csv
id,op,ARM_PMU_CPU_CYCLES,ARM_PMU_INST_RETIRED,...,cycles,overflow
0,CONV_2D,338176,270137,...,338176,False
1,DEPTHWISE_CONV_2D,206245,152970,...,206245,False
```

- `id` — sequential layer index (TFLM) or original TFLite op index (AOT)
- `op` — operator type (e.g. `CONV_2D`, `DEPTHWISE_CONV_2D:1` for AOT)
- Counter columns — averaged across iterations
- `cycles` — dedicated cycle counter value
- `overflow` — `True` if any counter overflowed (2³² saturation)

### run_metadata.json

Full provenance for the run:

```json
{
  "hpx_version": "0.1.0",
  "run_id": "a1b2c3d4",
  "timestamp": "2025-04-21T10:30:00",
  "config": { ... },
  "platform": {
    "board": "apollo510_evb",
    "soc": "apollo510",
    "core": "cortex-m55"
  },
  "model": {
    "name": "kws_ref_model.tflite",
    "size": 53936,
    "sha256": "abc123..."
  },
  "toolchain": {
    "compiler": "arm-none-eabi-gcc",
    "compiler_version": "arm-none-eabi-gcc (Arm GNU Toolchain 14.3.Rel1) 14.3.1",
    "cmake_version": "cmake version 3.31.6"
  },
  "firmware": {
    "arena_size": 131072,
    "allocated_arena": 29780,
    "model_size": 53936
  }
}
```

### aot_operator_manifest.json

For `helia-aot` runs, this captures the operators emitted by the AOT
compiler after graph transforms. Each operator includes inputs, outputs, and
local tensors such as weights, weight sums, and per-op scratch buffers.

When the installed `helia-aot` package exposes placement data, tensor entries
also include:

| Field | Meaning |
|---|---|
| `memory` | Runtime memory used by the kernel (`dtcm`, `sram`, `mram`, `psram`) |
| `source_memory` | Cold-storage source for staged constants |
| `staged` | `true` when a constant is copied from `source_memory` into `memory` |
| `arena_role` | `scratch`, `persistent`, or `constant` |
| `arena_region_id` | AOT arena enum value used by `bind_arena()` |
| `offset` | Byte offset inside the AOT arena |
| `allocation_size` | Planned allocation size in bytes |

### aot_memory_layers.csv

For `helia-aot` runs, this is a flat CSV view of the same placement data.
It is intended for customers who want to sort or filter buffers in a
spreadsheet while experimenting with AOT memory placement.

Example columns:

```csv
layer_idx,layer_id,op_type,op_name,tensor_role,tensor_id,tensor_name,tensor_kind,memory,source_memory,staged,arena_role,arena_region_id,offset,size,shape
0,0,CONV_2D,conv_2d_0,local,17,tensor_17,constant,dtcm,dtcm,False,constant,1,0,2560,"[64, 1, 5, 1]"
```

### detailed/memory.json

Deep memory breakdown (only with `--detailed`):

```json
{
  "binary_sections": {
    "text": 573968,
    "data": 14952,
    "bss": 163516,
    "total": 752436
  },
  "arena": {
    "arena_size": 131072,
    "allocated_arena": 29780,
    "num_tensors": 35,
    "num_inputs": 1,
    "num_outputs": 1,
    "model_size": 53936
  },
  "per_layer_memory": [
    {
      "op": "CONV_2D",
      "counters": {
        "ARM_PMU_L1D_CACHE": 28728,
        "ARM_PMU_L1D_CACHE_RD": 28727,
        "ARM_PMU_DTCM_ACCESS": 178393,
        "ARM_PMU_MEM_ACCESS": 207151
      }
    }
  ],
  "cache_totals": {
    "ARM_PMU_L1D_CACHE": 230224,
    "ARM_PMU_L1D_CACHE_MISS_RD": 0,
    "ARM_PMU_DTCM_ACCESS": 1338037,
    "l1d_hit_rate_pct": 100.0
  }
}
```

## Terminal summary

Every run prints a summary to the terminal:

```
============================================================
heliaPROFILER Results
============================================================
  arena_size: 131072
  allocated_arena: 29780
  model_size: 53936
  layers: 13
  total_cycles: 2,016,376

  Top layers by cycles:
    CONV_2D                           338,176 ( 16.8%)
    CONV_2D                           207,749 ( 10.3%)
    CONV_2D                           207,749 ( 10.3%)

  Memory: 29,780 / 131,072 bytes arena (22.7%)
  Model:  53,936 bytes
  Binary: text=573,968 data=14,952 bss=163,516 total=752,436

  Cache/Memory:
    L1D_CACHE                          230,224
    L1D_CACHE_RD                       230,203
    DTCM_ACCESS                      1,338,037
    MEM_ACCESS                       1,568,463
============================================================
```

## Controlling output

| Flag | Effect |
|---|---|
| `--output-dir PATH` | Change output directory (default: `./results`) |
| `--output-format csv` | CSV output (default) |
| `--output-format json` | JSON output |
| `--no-model-explorer` | Skip Model Explorer overlays |
| `--detailed` | Emit per-preset CSVs and memory.json in `detailed/` |
