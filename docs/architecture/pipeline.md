# Pipeline & Stages

The profiling pipeline is a sequence of stages executed in order. The
summary below collapses some setup and verification work into the major
phases most contributors interact with. Each phase reads from the shared
`PipelineContext` and writes its outputs back.

## Stage execution

```python
# High-level phase grouping
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

The concrete pipeline also runs preflight, board-power, probe-resolution,
model-analysis, memory-planning, and placement-verification stages around
these major phases. `PipelineRunner` still executes everything sequentially;
if any stage raises an exception, the pipeline stops and reports the error
with its typed hint.

The planned evolution of these boundaries, including separate profile/power
artifacts, early checkpoints, and deferred power diagnostics, is documented in
[Profile and Power Pipeline Refactor Plan](profile-power-refactor-plan.md).

## PipelineContext

The `PipelineContext` is a mutable state bag passed through all stages:

```python
@dataclass
class PipelineContext:
    config: ProfileConfig
    work_dir: Path
    soc: SocDef | None = None
    board: BoardDef | None = None
    resolved_jlink_serial: str | None = None
    engine_artifacts: EngineArtifacts | None = None
    firmware_dir: Path | None = None
    build_dir: Path | None = None
    binary_path: Path | None = None
    binary_sections: BinarySections | None = None
    pmu_result: PmuResult | None = None
    power_result: PowerResult | None = None
    report_paths: list[Path] = field(default_factory=list)
    run_metadata: RunMetadata = field(default_factory=RunMetadata)
```

Stages are expected to **set** their designated fields and **read** fields
set by earlier stages. No stage should modify another stage's output after
it's been set.

## Stage-by-stage detail

### S01: Resolve Platform

**File:** `stages/resolve_platform.py`
**Sets:** `ctx.soc`, `ctx.board`, `ctx.run_metadata.platform`, `ctx.run_metadata.model`

Validates the board name, resolves the SoC definition, computes the model file
hash (SHA-256), and populates platform and model metadata.

If the board has `DWT_ONLY` PMU, logs a warning that only cycle counts will be
captured.

### S02: Prepare Engine

**File:** `stages/prepare_engine.py`
**Sets:** `ctx.engine_artifacts`

Instantiates the engine adapter (heliaRT/heliaAOT, plus the internal TFLM path) and calls its
`prepare()` method. The adapter produces an `EngineArtifacts` bundle that records
engine identity plus any local NSX modules, static libraries, and memory-planning
metadata needed by later stages.

For **heliaRT**, this creates a local NSX module wrapping the pre-built static
library. For **heliaAOT**, this runs the AOT compiler and creates CMSIS-NN +
model NSX modules.

### S03: Generate Firmware

**File:** `stages/generate_firmware.py`
**Reads:** `ctx.engine_artifacts`, `ctx.config`
**Sets:** writes firmware app to `ctx.firmware_dir`

Renders Jinja2 templates into a complete NSX application:

- `CMakeLists.txt` — project build config
- `nsx.yml` — NSX module manifest
- `src/main.cc` — entry point (`main_aot.cc.j2` for AOT, `main.cc.j2` for the shared interpreter path)
- `src/hpx_pmu_profiler.cc/.h` — PMU capture harness
- `modules.cmake` — local module paths

The template context includes engine-specific variables (e.g. operator manifest
for AOT, library path for RT).

### S04: Build Firmware

**File:** `stages/build_firmware.py`
**Sets:** `ctx.build_dir`, `ctx.binary_path`, `ctx.binary_sections`, `ctx.run_metadata.toolchain`

Runs the NSX build pipeline:

1. `nsx configure --app-dir <app>` — CMake configure
2. `nsx build --app-dir <app>` — compile and link

After building, captures:
- **Binary section sizes** via the toolchain-specific size probe (`arm-none-eabi-size` or `fromelf`)
- **Toolchain info** — compiler and CMake versions

### S05: Flash Firmware

**File:** `stages/flash.py`
**Reads:** `ctx.binary_path`

Flashes the built firmware to the target via `nsx flash` (which uses JLinkExe).

If the debug domain is locked (common after power issues), retries with a
power-cycle reset via the Joulescope (if available).

### S06: Capture PMU

**File:** `stages/capture_pmu.py`
**Sets:** `ctx.pmu_result`

The core data collection stage:

1. **Reset the target** — J-Link reset to start firmware from the beginning
2. **Attach the selected transport reader** — `pylink` drives RTT/SWO capture
    and `pyserial` reads USB CDC / UART when selected
3. **Parse HPX protocol** — firmware prints structured data over the selected transport:
    - `HPX_START` / `HPX_END` markers
    - Metadata key-value pairs (arena size, model size, tensor count)
    - CSV rows: one row per layer per iteration with counter values
4. **Aggregate iterations** — counter values are combined across iterations using the selected aggregation mode
5. **Merge presets** — if multi-pass, layers from each pass are merged into
   unified results with all counters

The parser handles multi-preset firmware (one firmware binary can profile
multiple PMU counter sets in sequence).

### S07: Capture Power

**File:** `stages/capture_power.py`
**Sets:** `ctx.power_result`

Skipped if `power.enabled` is false. The concrete pipeline first plans a fixed
inference count from profile timing, rerenders and incrementally rebuilds the
dedicated transport-free power target, and explicitly flashes that artifact.
Capture then arms the configured power driver, resets the target without
normally cycling its rail, observes the GPIO-gated clean window, and computes
summary statistics from samples inside the accepted gate.

### S08: Generate Report

**File:** `stages/report.py`
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
firmware runs **multiple counter passes** within one profiling session. The
counter planning is handled by `counters.py`:

```
Pass 1: [CPU_CYCLES, INST_RETIRED, LD_RETIRED, ST_RETIRED, BR_RETIRED, ...]
Pass 2: [STALL_FRONTEND, STALL_BACKEND, STALL, EXC_TAKEN, EXC_RETURN, ...]
Pass 3: [L1D_CACHE, L1D_CACHE_RD, L1D_CACHE_REFILL, L1D_CACHE_MISS_RD, ...]
...
```

Each pass produces its own `PresetResult`. After all passes complete, results
are merged into unified `LayerResult` objects with all counter columns.
