# Output & Results

heliaPROFILER produces a structured set of output files organized for
progressive disclosure — high-level summaries by default, detailed breakdowns
on request.

## Output directory structure

### Default output

```
results/
├── result_manifest.json    # Versioned bundle envelope + artifact digests
├── summary.json            # Machine-readable high-level summary
├── profile_results.{csv|json} # Selected primary per-layer result
├── run_metadata.json       # Config, toolchain, platform, model info
├── nsx.lock                # Exact dependency lock used for this profile
├── aot_operator_manifest.json # AOT only: compiled operators + tensor placement
├── aot_memory_layers.csv   # AOT only: spreadsheet-friendly per-layer buffers
└── model_explorer/         # Overlay JSONs unless explicitly disabled
    ├── me_overlay_ARM_PMU_CPU_CYCLES.json
    ├── me_overlay_ARM_PMU_INST_RETIRED.json
    └── ...
```

The primary profile artifact is CSV or JSON, not both. AOT artifacts appear
only for heliaAOT runs, and Model Explorer overlays are omitted when
`--no-model-explorer` is set.

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
`run_metadata.json`, `nsx.lock`, and the selected primary profile result. The
dependency provenance records the typed registry hash, baseline identity and
fingerprint, workspace fingerprint, lock mode/update state, requested refs and
tags, peeled commits, content hashes, and explicit overrides. Detailed CSV/JSON files are projections or
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
  "schema_version": 3,
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
| `memory_plan` | The pre-build DECISION RECORD: per-region capacity/used and named consumers — see [Planned vs measured memory](#planned-vs-measured-memory) |
| `memory_regions` | The MEASURED region occupancy from the linked ELF: per-region used/reserved/free/load_image against the verified per-SoC windows |
| `binary` | ELF section sizes (text, data, bss, total, and `reserved` when non-zero) — see [Reserved vs bss](#reserved-vs-bss) |
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
  "hpx_version": "0.1.1",
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


### Reserved vs bss

`bss` counts zero-initialized state the program actually uses. `reserved` is
separate: linker-reserved NOBITS regions that are never written at runtime.

This matters most on Apollo5 boards, where the NSX linker script deliberately
fills **all remaining DTCM** as a `.heap` region so `_sbrk` has a bounded area
to allocate from. `arm-none-eabi-size`'s default output has no per-section
detail, so before HPX 0.1.6 that reservation was reported as `bss` — on a
measured build, 392 KB of "bss" for 248 bytes of real state.

HPX now reads the ELF section headers and reports the two separately:

```json
"binary": {
  "text": 45000,
  "data": 1200,
  "bss": 248,
  "reserved": 392960,
  "total": 393264
}
```

`reserved` is omitted entirely when a board's linker script does not reserve
anything, and `total` is unchanged — it remains the size tool's own inclusive
sum, so `text + data + bss + reserved` reconciles against it.

!!! note "Comparing against older baselines"
    Because `bss` no longer includes the reservation, an Apollo5 run recorded
    with HPX 0.1.5 or earlier reports a far larger `bss` than the same binary
    does today. `summary.json`'s `schema_version` moves from 1 to 2 so the
    change is detectable, and `hpx compare` reports it as a
    `run_summary_schema_version` difference rather than silently as a memory
    improvement. Re-record affected baselines.

!!! note "armclang reports the same split"
    armclang binaries are measured with `fromelf` rather than `size`, and
    before #132 landed that path had no per-section detail — an armclang build
    folded the linker's heap reservation into `bss`, so comparing it against
    a GCC run of the same source showed a large `bss` difference that was
    entirely an artifact of the measuring tool. `fromelf`'s per-section
    listing is now read the same way the section headers are on the GCC/ATfE
    path: armlink's `ARM_LIB_HEAP` region (`SHT_NOBITS` + `SHF_ALLOC`) moves
    to `reserved`, while `ARM_LIB_STACK` stays in `bss` — like `.stack` on
    GCC, it is the live stack, not a reservation. If the per-section output
    is unavailable or unparseable (for example, an older `fromelf`), the run
    degrades to the unadjusted totals — `reserved` reads 0 and `bss` again
    includes any reservation — rather than failing.

### Planned vs measured memory

Two blocks describe memory, on purpose, because they answer different
questions:

- **`memory_plan`** is the decision record — what hpx *intended*, computed
  before any compiler ran: per-region `capacity` (datasheet-flavored),
  `used` (the sum of what hpx itself placed), and the named `consumers`
  (weights, arena, scratch). It deliberately carries **no** `free` or
  `overflow`: the plan only counts what hpx placed, so a plan-side "free"
  was overstated and a plan-side "overflow" could not fire on real
  exhaustion (issue #133).
- **`memory_regions`** is the measured truth — the linked ELF's section
  inventory classified into per-SoC memory windows characterized from the
  NSX linker scripts and SDK hardware apertures. Per region:

```json
"memory_regions": {
  "link_family": "gnu",
  "linker_profile": "default",
  "regions": [
    {
      "region": "DTCM",
      "window": {"start": 536870912, "length": 524288},
      "app_window": {"start": 536870912, "length": 507904},
      "used": 16664,
      "reserved": 491240,
      "free": 491240,
      "load_image": 0,
      "window_provenance": "hardware-aperture",
      "app_provenance": "linker-script"
    }
  ],
  "unattributed": []
}
```

`window` is the hardware classification aperture; `app_window` is the
extent the link family's script gives the app image, and `free` is
`app_window.length − used`. `used` includes the live stack (gcc links);
`reserved` is the linker's own reservations — the fill-to-end heap plus
armlink's fixed heap/stack regions. `load_image` is the flash bytes that
initialize this region's data (summed from program headers by physical
address, which is correct on both gcc and armlink). `unattributed` lists
any allocated section that falls outside every verified window — either
the binary put bytes somewhere uncharacterized, or the window table is
wrong for that part; both deserve eyes. The whole block is **absent**
whenever it cannot be true: a custom SoC, a non-default `linker_profile`,
a failed tool probe, or a partial section inventory — never guessed.

!!! note "Schema v3"
    `summary.json`'s `schema_version` moves from 2 to 3 with this split:
    `memory_plan` lost its `free`/`overflow`/`has_overflow` keys, so a
    consumer comparing across the boundary sees a
    `run_summary_schema_version` difference in `hpx compare` rather than
    silently reading the semantic change. Re-record baselines that
    consumed the plan's `free`.

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
