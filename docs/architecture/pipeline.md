# Pipeline & Stages

The profiling pipeline is a sequence of 8 stages executed in order. Each stage
has a single responsibility, reads from the shared `PipelineContext`, and
writes its outputs back.

## Stage execution

```python
# From profiler.py
def build_default_pipeline() -> list[Stage]:
    return [
        ResolvePlatform(),      # S01
        PrepareEngine(),        # S02
        GenerateFirmware(),     # S03
        BuildFirmware(),        # S04
        FlashFirmware(),        # S05
        CapturePmu(),           # S06
        CapturePower(),         # S07
        GenerateReport(),       # S08
    ]
```

The `PipelineRunner` calls each stage's `run(ctx)` method sequentially. If any
stage raises an exception, the pipeline stops and reports the error with its
typed hint.

## PipelineContext

The `PipelineContext` is a mutable state bag passed through all stages:

```python
@dataclass
class PipelineContext:
    config: ProfileConfig          # Frozen — never mutated
    run_metadata: RunMetadata      # Accumulated across stages

    # Set by individual stages:
    platform_info: PlatformInfo | None = None
    engine_artifacts: EngineArtifacts | None = None
    build_dir: Path | None = None
    binary_path: Path | None = None
    binary_sections: BinarySections | None = None
    pmu_result: PmuResult | None = None
    power_result: PowerResult | None = None
```

Stages are expected to **set** their designated fields and **read** fields
set by earlier stages. No stage should modify another stage's output after
it's been set.

## Stage-by-stage detail

### S01: Resolve Platform

**File:** `stages/s01_resolve_platform.py`
**Sets:** `ctx.platform_info`, `ctx.run_metadata.platform`, `ctx.run_metadata.model`

Validates the board name, resolves the SoC definition, computes the model file
hash (SHA-256), and populates platform and model metadata.

If the board has `DWT_ONLY` PMU, logs a warning that only cycle counts will be
captured.

### S02: Prepare Engine

**File:** `stages/s02_prepare_engine.py`
**Sets:** `ctx.engine_artifacts`

Instantiates the engine adapter (TFLM, heliaRT, or heliaAOT) and calls its
`prepare()` method. The adapter produces `EngineArtifacts`:

```python
@dataclass
class EngineArtifacts:
    modules: list[NsxModuleRef]   # NSX modules to link
    template_vars: dict[str, Any] # Jinja template variables
    extra_modules: list[NsxModuleRef] = field(default_factory=list)
```

For **heliaRT**, this creates a local NSX module wrapping the pre-built static
library. For **heliaAOT**, this runs the AOT compiler and creates CMSIS-NN +
model NSX modules.

### S03: Generate Firmware

**File:** `stages/s03_generate_firmware.py`
**Reads:** `ctx.engine_artifacts`, `ctx.config`
**Sets:** writes firmware app to `ctx.config.work_dir`

Renders Jinja2 templates into a complete NSX application:

- `CMakeLists.txt` — project build config
- `nsx.yml` — NSX module manifest
- `src/main.cc` — entry point (different template for AOT vs RT/TFLM)
- `src/hpx_pmu_profiler.cc/.h` — PMU capture harness
- `modules.cmake` — local module paths

The template context includes engine-specific variables (e.g. operator manifest
for AOT, library path for RT).

### S04: Build Firmware

**File:** `stages/s04_build_firmware.py`
**Sets:** `ctx.build_dir`, `ctx.binary_path`, `ctx.binary_sections`, `ctx.run_metadata.toolchain`

Runs the NSX build pipeline:

1. `nsx configure --app-dir <app>` — CMake configure
2. `nsx build --app-dir <app>` — compile and link

After building, captures:
- **Binary section sizes** via `arm-none-eabi-size` (text, data, bss)
- **Toolchain info** — compiler and CMake versions

### S05: Flash Firmware

**File:** `stages/s05_flash_firmware.py`
**Reads:** `ctx.binary_path`

Flashes the built firmware to the target via `nsx flash` (which uses JLinkExe).

If the debug domain is locked (common after power issues), retries with a
power-cycle reset via the Joulescope (if available).

### S06: Capture PMU

**File:** `stages/s06_capture_pmu.py`
**Sets:** `ctx.pmu_result`

The core data collection stage:

1. **Reset the target** — J-Link reset to start firmware from the beginning
2. **Start SWO viewer** — `JLinkSWOViewerCL` captures the trace output
3. **Parse HPX protocol** — firmware prints structured data over SWO:
    - `HPX_START` / `HPX_END` markers
    - Metadata key-value pairs (arena size, model size, tensor count)
    - CSV rows: one row per layer per iteration with counter values
4. **Average iterations** — counter values are averaged across iterations
5. **Merge presets** — if multi-pass, layers from each pass are merged into
   unified results with all counters

The parser handles multi-preset firmware (one firmware binary can profile
multiple PMU counter sets in sequence).

### S07: Capture Power

**File:** `stages/s07_capture_power.py`
**Sets:** `ctx.power_result`

Skipped if `power.enabled` is false. Otherwise:

1. Power-cycle the EVB via Joulescope (cut power, wait, restore)
2. Start the Joulescope capture for `duration_s` seconds
3. Compute summary statistics (avg/peak current, power, energy)

### S08: Generate Report

**File:** `stages/s08_generate_report.py`
**Reads:** everything from `ctx`

Delegates to `report.write_report()` which produces:

- `summary.json` — always
- `profile_results.csv` — always (or `.json` if format=json)
- `run_metadata.json` — always
- `model_explorer/*.json` — unless disabled
- `detailed/` subfolder — only with `--detailed`

See [Output & Results](../guide/output.md) for file format details.

## Multi-pass profiling

When the requested PMU counters exceed the 8-counter hardware limit, the
pipeline runs stages S03–S06 **multiple times** — once per counter pass.
The counter planning is handled by `counters.py`:

```
Pass 1: [CPU_CYCLES, INST_RETIRED, LD_RETIRED, ST_RETIRED, BR_RETIRED, ...]
Pass 2: [STALL_FRONTEND, STALL_BACKEND, STALL, EXC_TAKEN, EXC_RETURN, ...]
Pass 3: [L1D_CACHE, L1D_CACHE_RD, L1D_CACHE_REFILL, L1D_CACHE_MISS_RD, ...]
...
```

Each pass produces its own `PresetResult`. After all passes complete, results
are merged into unified `LayerResult` objects with all counter columns.
