# heliaPROFILER Specification

**CLI name:** `hpx`
**Package:** `helia_profiler`
**Repo:** `git@github.com:AmbiqAI/helia-profiler.git`

---

## 1. Purpose

`hpx` is a cross-platform (macOS, Linux, Windows) command-line tool that
profiles LiteRT (TFLite) flatbuffer models running on Ambiq Apollo hardware.
It captures per-layer PMU counter breakdowns and optional power measurements
for a single explicitly-chosen inference engine per run.

`hpx` is **not** a build system, SDK exporter, or application framework. It is
a profiler. It generates a temporary NSX firmware app, flashes it, captures
data, and reports results.

## 2. Anti-Goals (Lessons from AutoDeploy)

| AutoDeploy problem | heliaPROFILER approach |
| --- | --- |
| Tried to run multiple inference engines in one invocation | One engine per run. User specifies explicitly. |
| Brittle two-pass arena size auto-detection | User provides arena size, or engine reports it at runtime with clear error on OOM. No silent recompile. |
| Auto-detected memory locations with complex fallback chains | User specifies memory placement or accepts board defaults. Engine-specific YAML (e.g. heliaAOT) handles fine-tuning. |
| Exported AmbiqSuite examples and minimal static libraries | No export. `hpx` is a profiler, not a code generator. |
| Monolithic 2400-line validator.py | Focused modules with single responsibilities. |
| `os.system()` shell commands for builds | `subprocess.run()` with argument lists, or NSX Python API when available. |
| Monkey-patched `sys.exit` for heliaAOT error handling | Subprocess isolation — engine tools run in their own process. |
| Pickle for intermediate state | YAML/JSON for any persisted state. |
| Global mutable Params object | Immutable frozen dataclass config resolved once at startup. |
| RPC-based model transfer and validation | SWO/USB serial for structured output. No RPC framework dependency. |

## 3. Platform Model

### 3.1 Two-Level Hierarchy: Board → SoC

Every profiling run targets a **board** (e.g. `apollo510_evb`). The board
resolves to a **SoC** (e.g. `apollo510`), which determines the core
architecture, PMU capabilities, memory layout, and clock modes.

**Initial scope:** EVBs only. Custom boards are out of scope but the
architecture is extensible — adding a board is adding one `BoardDef` entry
and (if new) one `SocDef` entry.

### 3.2 SoC Families

| Family | Core | PMU | MVE | SDK Tier | SoCs |
| --- | --- | --- | --- | --- | --- |
| **AP3** | Cortex-M4 | DWT only | No | r3 | apollo3p |
| **AP4** | Cortex-M4 | DWT only | No | r4 | apollo4p, apollo4l |
| **AP5** | Cortex-M55 | Full Armv8-M (70+ events) | Yes | r5 | apollo510, apollo510b, apollo5b, **apollo330P** |

**Important:** Apollo330 (`apollo330P`) is Cortex-M55 and belongs to the AP5
family despite the "3" in its name. It has full PMU and MVE support.

### 3.3 PMU Tiers

| Tier | Architecture | Capabilities |
| --- | --- | --- |
| `DWT_ONLY` | Cortex-M4 | Cycle counter. Limited event coverage. No per-layer PMU event breakdown. |
| `ARMV8M_PMU` | Cortex-M55 | 8 configurable counters, 70+ events (cycles, instructions, cache, MVE, stalls). Full per-layer breakdown. |

When targeting a DWT-only SoC, `hpx` warns the user and falls back to
cycle-count-only profiling. PMU preset selection is ignored on DWT targets.

### 3.4 Supported EVBs

| Board | SoC | Family | Channel |
| --- | --- | --- | --- |
| `apollo3p_evb` | apollo3p | AP3 | stable |
| `apollo4p_evb` | apollo4p | AP4 | preview |
| `apollo510_evb` | apollo510 | AP5 | stable |
| `apollo510b_evb` | apollo510b | AP5 | preview |
| `apollo5b_evb` | apollo5b | AP5 | preview |
| `apollo330mP_evb` | apollo330P | AP5 | preview |

## 4. Supported Inference Engines

Each engine is a self-contained adapter. Only one runs per invocation.

### 4.1 Stock TFLM (`tflm`)

Standard upstream TensorFlow Lite for Microcontrollers with CMSIS-NN kernels.
Uses pre-built static libraries or source builds via NSX modules.

### 4.2 heliaRT (`helia-rt`)

Ambiq's optimized TFLM fork (github.com/AmbiqAI/helia-rt). Three kernel
backends: reference, CMSIS-NN, and HELIA (Ambiq-optimized MVE/DSP).

Current heliaRT static libraries target legacy neuralSPOT. Until an NSX-native
heliaRT module exists, `hpx` ships a local wrapper module that integrates the
heliaRT static library into the NSX build system.

### 4.3 heliaAOT (`helia-aot`)

Ambiq's ahead-of-time compiler. Generates a C module for a specific model.
The generated code is wrapped as an NSX-compatible local module for the
profiler firmware build. Accepts an engine-specific YAML config for
fine-grained control (memory placement, quantization settings, etc.).

## 5. Architecture

### 5.1 High-Level Flow

```
User config (CLI + YAML)
        │
        ▼
┌─────────────────┐
│  Config resolve  │  Merge CLI args + hpx.yml → frozen ProfileConfig
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Engine adapter  │  Prepare engine-specific module/sources
│  (tflm/hrt/aot) │  (e.g. run heliaAOT compiler, fetch heliaRT lib)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   App generate   │  Render profiler firmware as a temporary NSX app
│                  │  (Jinja templates → CMakeLists.txt, nsx.yml, main.c)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  NSX pipeline    │  configure → build → flash
│                  │  (via neuralspotx Python API or CLI subprocess)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Data capture    │  SWO/USB serial → structured PMU + timing output
│                  │  Optional: Joulescope power capture in parallel
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Report          │  Parse captured data → CSV / JSON / terminal summary
└─────────────────┘
```

### 5.2 Module Layout

```
src/helia_profiler/
├── __init__.py              # Public API exports
├── api.py                   # profile() — programmatic entry point
├── cli.py                   # argparse CLI, thin delegation to api.py
├── config.py                # ProfileConfig dataclass + YAML/CLI merge
├── doctor.py                # hpx doctor — host tool checks
├── errors.py                # Typed error hierarchy
├── jlink.py                 # SEGGER J-Link helpers (discovery, reset, SWO cmd)
├── nsx.py                   # NSX build-system subprocess wrapper
├── platform.py              # SoC families, board registry, capabilities
├── profiler.py              # Pipeline composition + logging setup
├── results.py               # Typed result models (PmuResult, ProfileResult, etc.)
├── engines/                 # One adapter per inference engine
│   ├── __init__.py
│   ├── base.py              # EngineAdapter protocol, EngineArtifacts, NsxModuleRef
│   ├── tflm.py
│   ├── helia_rt.py
│   └── helia_aot.py
├── firmware/                # NSX app generation
│   ├── __init__.py          # Template rendering + build/flash via nsx.py
│   └── templates/           # Jinja2 templates for profiler firmware
│       ├── CMakeLists.txt.j2
│       ├── nsx.yml.j2
│       └── src/
│           ├── main.cc.j2
│           ├── hpx_pmu_profiler.h.j2
│           └── hpx_pmu_profiler.cc.j2
├── capture/                 # Data acquisition from target
│   ├── __init__.py          # capture_pmu / capture_power orchestration
│   ├── serial_reader.py     # SWO-based firmware output via jlink.py
│   └── parser.py            # HPX protocol → PmuResult
├── power/                   # Power measurement drivers
│   ├── __init__.py          # Driver registry, auto-detection
│   ├── base.py              # PowerDriver protocol, PowerResult, PowerSummary
│   ├── joulescope_driver.py # JS110 driver (capture + power-cycle)
│   ├── joulescope_js220.py  # JS220 driver (capture + power-cycle)
│   └── ondevice_driver.py   # On-device measurement (experimental)
├── stages/                  # Pipeline stages (ordered s01–s08)
│   ├── s01_resolve_platform.py
│   ├── s02_prepare_engine.py
│   ├── s03_generate_firmware.py
│   ├── s04_build_firmware.py
│   ├── s05_flash_firmware.py  # Retry with power-cycle on locked debug domain
│   ├── s06_capture_pmu.py
│   ├── s07_capture_power.py   # Power-cycle reset → capture
│   └── s08_generate_report.py
├── report/                  # Output formatting
│   ├── __init__.py          # Dispatch + CSV/JSON/summary/Model Explorer
│   └── model_explorer.py    # Model Explorer overlay builder
├── pipeline.py              # PipelineContext, Stage protocol, PipelineRunner
└── _version.py              # Single version source
```

### 5.3 Configuration Model

A single `hpx.yml` file (or equivalent CLI flags) provides everything:

```yaml
model:
  path: my_model.tflite
  arena_size: 65536             # bytes, required unless engine handles it

engine:
  type: helia-rt                # tflm | helia-rt | helia-aot
  backend: helia                # engine-specific (e.g. helia-rt backend)
  config: {}                    # passthrough dict for engine-specific YAML

target:
  board: apollo510_evb
  toolchain: arm-none-eabi-gcc
  # jlink_serial: "801000001"  # select J-Link probe by serial number

profiling:
  pmu_presets:                  # which PMU counter groups to capture
    - basic_cpu
    - memory
    - mve
  per_layer: true               # per-layer breakdown vs whole-model only
  iterations: 100               # inference iterations per PMU preset
  warmup: 5                     # warmup iterations before measurement

power:
  enabled: false
  driver: joulescope            # joulescope | joulescope-js110 | joulescope-js220 | ondevice
  mode: external                # external (Joulescope) | internal (on-device)
  duration_s: 30
  io_voltage: 1.8
  sync_gpio_pin: 10             # firmware toggles this GPIO during inference

output:
  format: csv                   # csv | json
  dir: ./results
  model_explorer: true          # emit Model Explorer overlay JSONs (default: true)
```

**Key rule:** The config is resolved once at startup into a frozen
`ProfileConfig` dataclass. No field is mutated after construction.

### 5.4 Engine Adapter Interface

```python
class EngineAdapter(Protocol):
    """Each inference engine implements this interface."""

    name: str

    def prepare(self, config: ProfileConfig, work_dir: Path) -> EngineArtifacts:
        """Produce any engine-specific artifacts (compiled AOT module,
        fetched static lib, source patches, etc.).
        Returns paths and metadata the firmware templates need."""
        ...

    def template_context(self, artifacts: EngineArtifacts) -> dict[str, Any]:
        """Return Jinja template variables specific to this engine."""
        ...
```

Each adapter is fully isolated. If an engine tool fails, the error propagates
naturally — no monkey-patching or global state recovery.

### 5.5 Typed Result Models

All structured data flowing between pipeline stages is represented as frozen
dataclasses in ``results.py``. No ``dict[str, Any]`` at stage boundaries.

| Model | Purpose |
| --- | --- |
| `LayerResult` | Per-layer counters, cycles, overflow flag |
| `PresetResult` | One PMU preset: header, iterations, averaged layers |
| `FirmwareMeta` | Firmware-reported metadata (arena, model size, etc.) |
| `PmuResult` | Complete PMU result: meta + presets + merged layers |
| `PowerResult` | Power capture: summary + samples (in `power/base.py`) |
| `PlatformInfo` | Resolved board/SoC/core details |
| `ModelInfo` | Model file name, size, sha256 |
| `ToolchainInfo` | Compiler and cmake versions |
| `RunMetadata` | Accumulates all of the above across stages |
| `NsxModuleRef` | Typed reference to an NSX module (name, path, version) |
| `ProfileResult` | **Public return type** of `profile()` — PMU, power, metadata, paths |

`ProfileResult` provides convenience accessors for progressive disclosure:

```python
result.total_cycles      # sum of all layer cycles
result.layer_count       # number of layers
result.layers            # shortcut to result.pmu.layers
result.overflow_detected # True if any counter overflowed
```

The one deliberate `dict` is `LayerResult.counters: dict[str, float]` — PMU
counter names are dynamic (vary by preset) and enumerating every ARM PMU
event as a field would be impractical.

### 5.6 Firmware App Generation

The profiler firmware is a purpose-built NSX app rendered from Jinja templates.
It is generated into a temporary (or user-specified) directory and built with
the standard NSX pipeline.

### 5.7 Subprocess Isolation

All external process invocations are centralised in dedicated modules. No
stage or orchestration code calls ``subprocess`` directly.

| Module | Responsibility | Commands |
| --- | --- | --- |
| `jlink.py` | J-Link probe interaction | `JLinkExe` (reset), `JLinkSWOViewerCL` (SWO) |
| `nsx.py` | NSX build system | `nsx configure`, `nsx build`, `nsx flash` |
| `capture/serial_reader.py` | SWO capture process lifecycle | Uses `jlink.py` for commands |

**Rules:**

1. Every `subprocess.run()` call has a `timeout` to prevent hangs.
2. Every failure raises a typed `HpxError` subclass with actionable hints.
3. No `shell=True` — all commands use argument lists.
4. J-Link device strings come from the platform registry (`SocDef.jlink_device`),
   never hardcoded in calling code.
5. J-Link serial selection (`-SelectEmuBySN`) is handled exclusively by
   `jlink.py`. The `SEGGER_SNCODE` env var for cmake flash targets is set
   by `nsx.py`, keeping SEGGER-specific details in the hardware modules.

### 5.8 Hardware Control & Power-Cycle Reset

When a Joulescope is available, `hpx` can cut and restore target power via
the instrument's current shunt relay. This serves two purposes:

1. **Flash recovery** (stage 5): If the debug domain is locked (common after
   deep-sleep profiling), a power-cycle restores debug access so flash can
   proceed. Automatic — retry once on failure, transparent to the user.

2. **Accurate power measurement** (stage 7): A J-Link reset leaves the debug
   access port (DAP) powered, adding measurable current overhead. A
   power-cycle reset clears this, so stage 7 power numbers reflect true
   firmware consumption with no debug artefacts.

This is why PMU capture (stage 6) and power capture (stage 7) are separate
stages. Stage 6 uses a J-Link reset (debug overhead doesn't affect cycle
counters). Stage 7 uses a Joulescope power-cycle (zero debug overhead for
accurate current/energy).

The firmware:

1. Initializes the platform via `ns_core_init()`.
2. Loads the model using the selected engine's API.
3. For each PMU preset:
   a. Configures PMU counters via `nsx-pmu-armv8m`.
   b. Runs warmup iterations.
   c. For each measured iteration, captures per-layer or whole-model counters.
4. Emits structured results over SWO (or USB serial) in a parseable format.
5. Optionally enters a power measurement loop with GPIO phase signaling.

### 5.9 Data Capture

**PMU data** is emitted by the firmware as structured text (CSV lines or tagged
records) over SWO/ITM. The host-side capture module reads the
serial stream, parses records, and aggregates per-layer statistics.

**Power data** is captured by the configured power driver (Joulescope JS110 or
JS220) after a power-cycle reset. The firmware runs its inference loop
independently; the Joulescope samples current on the power rail for
`duration_s` seconds and computes aggregate statistics.

No RPC framework is used. The firmware pushes data; the host listens and
parses.

### 5.10 Report Generation

Results are written as:

- **Terminal summary:** Human-readable table with per-layer cycle counts,
  instruction counts, cache stats, and (if enabled) power/energy.
- **CSV:** One row per layer per PMU preset, suitable for spreadsheet analysis.
- **JSON:** Structured output for programmatic consumption.
- **Model Explorer overlay:** Per-metric JSON files compatible with the
  [Model Explorer](https://github.com/google-ai-edge/model-explorer) custom
  node data format. Load alongside the source `.tflite` to color-code nodes by
  cycles, instructions, cache misses, MVE operations, etc.

Model Explorer overlays are emitted **by default** (one file per PMU metric)
alongside the primary report format. Use `--no-model-explorer` to skip them.

#### Model Explorer overlay format

Each overlay file follows Model Explorer's `ModelNodeData` schema:

```json
{
  "main": {
    "name": "cycles",
    "results": {
      "output_tensor_0": {"value": 12450},
      "output_tensor_1": {"value": 8320},
      "output_tensor_2": {"value": 45600}
    },
    "gradient": [
      {"stop": 0, "bgColor": "#22c55e"},
      {"stop": 0.5, "bgColor": "#eab308"},
      {"stop": 1, "bgColor": "#ef4444"}
    ]
  }
}
```

Node keys use output tensor names (stable across builds) when available,
falling back to node IDs. The default gradient is green → yellow → red for
cost metrics (higher = hotter).

Power/current/energy numbers in reports should be clearly labeled as
measurements from the user's specific setup, not published reference values.

## 6. CLI Interface

```
hpx profile [OPTIONS] MODEL_PATH
hpx profile --config hpx.yml

hpx doctor                          # check toolchain, nsx, joulescope
hpx engines                         # list available inference engines
hpx boards                          # list supported boards and SoC capabilities
hpx version
```

### `hpx profile` flags

Flags are organized into logical groups in `--help` output for progressive
disclosure. The most common options (model, config, verbosity) appear first;
advanced options (work-dir, keep-work-dir) appear last.

**Top-level:**

| Flag | Type | Description |
| --- | --- | --- |
| `MODEL_PATH` | positional | Path to .tflite model file |
| `--config` | path | YAML config file (overrides/supplements CLI flags) |
| `--verbose` / `-v` | count | Increase verbosity |

**Engine:**

| Flag | Type | Description |
| --- | --- | --- |
| `--engine` | choice | `tflm`, `helia-rt`, `helia-aot` |
| `--engine-config` | path | Engine-specific YAML (e.g. heliaAOT config) |
| `--arena-size` | int | Tensor arena size in bytes |

**Target hardware:**

| Flag | Type | Description |
| --- | --- | --- |
| `--board` | string | Target board (default: `apollo510_evb`) |
| `--toolchain` | string | Toolchain (default: `arm-none-eabi-gcc`) |
| `--jlink-serial` | string | J-Link probe serial number (default: auto-detect) |

**PMU profiling:**

| Flag | Type | Description |
| --- | --- | --- |
| `--pmu-presets` | list | PMU preset names to capture |
| `--per-layer` / `--no-per-layer` | flag | Per-layer breakdown (default: on) |
| `--iterations` | int | Inference iterations (default: 100) |

**Power measurement:**

| Flag | Type | Description |
| --- | --- | --- |
| `--power` | flag | Enable Joulescope power capture |
| `--power-driver` | choice | `joulescope`, `joulescope-js110`, `joulescope-js220`, `ondevice` |
| `--power-mode` | choice | `external`, `internal` |
| `--power-duration` | int | Power capture seconds (default: 30) |
| `--sync-gpio` | int | GPIO pin for external power sync (default: 10) |

**Output:**

| Flag | Type | Description |
| --- | --- | --- |
| `--output-dir` | path | Results output directory |
| `--output-format` | choice | `csv`, `json` |
| `--no-model-explorer` | flag | Skip Model Explorer overlay generation |

**Advanced:**

| Flag | Type | Description |
| --- | --- | --- |
| `--work-dir` | path | Working directory for generated firmware (default: temp) |
| `--keep-work-dir` | flag | Don't delete working directory after profiling |

CLI flags override YAML values. YAML provides defaults for reproducible runs.

### Programmatic API

`helia_profiler` can be used as a library without the CLI:

```python
from helia_profiler import profile, ProfileConfig, ProfileResult
from helia_profiler.config import load_config

# Option A: from YAML
config = load_config("hpx_kws.yml")
result: ProfileResult = profile(config)

# Option B: pure Python
from helia_profiler import (
    ModelConfig, EngineConfig, TargetConfig, ProfilingConfig, OutputConfig,
)
config = ProfileConfig(
    model=ModelConfig(path="kws.tflite", arena_size=65536),
    engine=EngineConfig(type="tflm"),
    target=TargetConfig(board="apollo510_evb"),
    profiling=ProfilingConfig(pmu_presets=["basic_cpu"]),
    output=OutputConfig(dir="./results"),
)
result = profile(config)

# Inspect results
print(result.total_cycles)
print(result.layer_count)
for layer in result.layers:
    print(f"{layer.op}: {layer.cycles} cycles")
if result.power:
    print(f"Average power: {result.power.summary.mean_power_w:.3f} W")
```

The `profile()` function is the single entry point. It returns a
`ProfileResult` with typed accessors for PMU data, power measurements,
run metadata, and report file paths.

## 7. Dependencies

### Runtime (installed with `hpx`)

- `PyYAML` — config parsing
- `Jinja2` — firmware template rendering
- `pyserial` — USB serial capture
- `neuralspotx` — NSX Python API for configure/build/flash (or subprocess fallback)

### Optional

- `joulescope` — JS110 power measurement + power-cycle reset (only with `--power`)
- `pyjoulescope_driver` — JS220 power measurement + power-cycle reset
- `helia-aot` — AOT compilation (only with `--engine helia-aot`)

### Host toolchain (checked by `hpx doctor`)

- `arm-none-eabi-gcc` (or `armclang`)
- `cmake` ≥ 3.24
- `ninja`
- `JLinkExe` / `JLinkSWOViewerCL` (SEGGER J-Link)
- `nsx` CLI (neuralspotx)

## 8. Cross-Platform Requirements

| Concern | Approach |
| --- | --- |
| Path handling | `pathlib.Path` everywhere, no string concatenation |
| Subprocess calls | `subprocess.run()` with argument lists, no `shell=True`, all calls have `timeout` |
| J-Link / SEGGER | Centralised in `jlink.py` — device strings from platform registry |
| NSX build system | Centralised in `nsx.py` — timeouts, typed errors, one place to add env vars |
| Serial ports | `pyserial` with platform-appropriate port names |
| Temp directories | `tempfile.mkdtemp()` with proper cleanup |
| Console output | No ANSI codes unless terminal supports it (or use `rich` optional) |

## 9. What This Tool Does NOT Do

- Generate exportable AmbiqSuite examples or static libraries
- Run multiple inference engines in one invocation
- Auto-detect arena size via two-pass recompilation
- Transfer models over RPC
- Manage neuralSPOT or AmbiqSuite SDK installations
- Validate model numerical accuracy (host vs device comparison)
- Provide a GUI (future scope)

## 10. Future Considerations

- **On-board power monitor:** A click-module or I2C power monitor (e.g. INA226)
  readable directly from instrumented firmware code, eliminating desktop-side
  Joulescope dependency for power numbers.
- **GUI frontend:** Wrap the CLI/API in a desktop or web UI.
- **CI integration:** JSON output + exit codes suitable for regression tracking.
- **heliaRT NSX module PR:** Once heliaRT gets a proper `nsx-module.yaml`, the
  local wrapper shim can be retired.
- **CMSIS-NN standalone:** heliaRT with CMSIS-NN backend as a lighter
  alternative to full TFLM, if TFLM management overhead is a concern.
- **USB serial transport:** Add USB-CDC capture alongside SWO for boards without
  J-Link OB SWO routing (or when SWO bandwidth is a bottleneck).
- **NSX Python API:** Replace `nsx.py` subprocess calls with direct
  `neuralspotx` Python API imports when a stable programmatic interface is
  available. The `nsx.py` module is structured for a clean swap.
- **Per-layer power:** Use GPIO sync pulse per-layer combined with
  high-frequency Joulescope sampling to attribute energy to individual layers.
- **Multi-board parallel:** Profile the same model across multiple EVBs
  simultaneously (one J-Link serial per board, one Joulescope per power rail).
- **Pydantic v2 config + results models:** Migrate the `ProfileConfig` /
  `MemoryPlan` dataclasses to pydantic v2 with a discriminated `EngineConfig`
  union. Gives JSON-schema export, path-aware validation errors, and
  per-engine config typing without hand-rolled `__post_init__` coercers.
- **REST / FastAPI service layer:** Wrap the existing pipeline in a small
  FastAPI app so CI systems and a future web UI can submit profile jobs,
  poll status, and download artifacts without shelling out to the CLI.
  Out of scope for now — captured here so the underlying typed-config /
  typed-artifacts work continues to support it cleanly.
