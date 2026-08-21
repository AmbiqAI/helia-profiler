# Pipeline & Stages

The profiling pipeline is a flat, ordered sequence of stages. Each stage
reads from the shared `PipelineContext` and writes its outputs back.

## Stage execution

The full list, from `profiler.py`:

```python
def build_default_pipeline() -> PipelineRunner:
    return PipelineRunner([
        PreflightStage(),              # host-only dependency + config checks
        EnsureBoardPoweredStage(),     # restore the rail before touching the probe
        ResolvePlatformStage(),        # board -> SoC, model hash
        ResolveJLinkProbeStage(),      # pick and open the probe
        PrepareEngineStage(),          # engine adapter prepare()
        AnalyzeModelStage(),           # host-side model analysis (optional)
        PlanMemoryStage(),             # arena / weights placement
        GenerateFirmwareStage(),       # render the NSX app
        BuildFirmwareStage(),          # nsx configure + build
        VerifyPlacementStage(),        # confirm the linker honoured the plan
        FlashFirmwareStage(),
        CapturePmuStage(),
        PlanPowerRunStage(),           # fixed N from clean profile timing
        BuildPowerFirmwareStage(),     # transport-free power image
        FlashPowerFirmwareStage(),
        CapturePowerStage(),
        CollectPowerTerminalStage(),   # post-gate diagnostics from the target
        GenerateReportStage(),
    ])
```

The five power stages are no-ops when `power.enabled` is false.
`PipelineRunner` executes everything sequentially; if any stage raises, the
pipeline stops and reports the error with its typed hint.

The sections below detail the stages most contributors touch.

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

(Abridged — `pipeline.py` carries the full field list, including the
probe/flash/reset handles, dependency workspace, power plan, and the grouped
`ProfileRun`/`PowerRun` workflow records.)

Stages are expected to **set** their designated fields and **read** fields
set by earlier stages. No stage should modify another stage's output after
it's been set.

## Stage-by-stage detail

### Resolve Platform

**File:** `stages/resolve_platform.py`
**Sets:** `ctx.soc`, `ctx.board`, `ctx.run_metadata.platform`, `ctx.run_metadata.model`

Validates the board name, resolves the SoC definition, computes the model file
hash (SHA-256), and populates platform and model metadata.

If the board has `DWT_ONLY` PMU, logs a warning that only cycle counts will be
captured.

### Prepare Engine

**File:** `stages/prepare_engine.py`
**Sets:** `ctx.engine_artifacts`

Instantiates the selected heliaRT, heliaAOT, TFLM, or ExecuTorch adapter and calls its
`prepare()` method. The adapter produces its engine's `EngineArtifacts` subtype that records
engine identity plus any local NSX modules, static libraries, and memory-planning
metadata needed by later stages.

For **heliaRT**, this normally declares the pinned registry module, with local
source/prebuilt overrides available. For **heliaAOT**, this runs the compiler
and creates the model module while resolving CMSIS-NN. For **TFLM**, it resolves
the stock interpreter module and selected backend. For **ExecuTorch**, it
validates the pinned `nsx-executorch` checkout and wraps it as a local module
behind the selected CMSIS-NN provider.

### Generate Firmware

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

### Build Firmware

**File:** `stages/build_firmware.py`
**Sets:** `ctx.build_dir`, `ctx.binary_path`, `ctx.binary_sections`, `ctx.run_metadata.toolchain`

Runs the NSX build pipeline:

1. `nsx configure --app-dir <app>` — CMake configure
2. `nsx build --app-dir <app>` — compile and link

After building, captures:
- **Binary section sizes** via the toolchain-specific size probe (`arm-none-eabi-size` or `fromelf`)
- **Toolchain info** — compiler and CMake versions

### Flash Firmware

**File:** `stages/flash.py`
**Reads:** `ctx.binary_path`

Flashes the built firmware to the target via `nsx flash` (which uses JLinkExe).

If the debug domain is locked (common after power issues), retries with a
power-cycle reset via the Joulescope (if available).

### Capture PMU

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

### Capture Power

**File:** `stages/capture_power.py`
**Sets:** `ctx.power_result`

Skipped if `power.enabled` is false. Three stages run ahead of it:
`PlanPowerRunStage` derives a fixed inference count from the clean profile
timing, `BuildPowerFirmwareStage` rerenders and incrementally rebuilds the
dedicated transport-free power target, and `FlashPowerFirmwareStage` deploys
it. Capture then arms the configured power driver, resets the target without
normally cycling its rail, observes the GPIO-gated clean window, and computes
summary statistics from samples inside the accepted gate.
`CollectPowerTerminalStage` follows, reading the target's one terminal record
after the gate has closed.

**Why there is no `--power-only` flag.** The profile phase does more than
collect optional PMU counters — its clean inference timing is the
authoritative denominator used to choose fixed `N` and to verify the measured
gate duration, and `ProfileResult`, reporting, and validity all currently
require a `PmuResult`. Skipping `CapturePmuStage` would either remove that
denominator or leave later stages with an invalid contract, so a real
power-only workflow needs its own result type and pipeline composition rather
than a flag.

### Generate Report

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
