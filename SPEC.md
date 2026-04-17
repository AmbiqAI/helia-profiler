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
├── __init__.py
├── cli.py                  # argparse CLI, thin delegation
├── config.py               # ProfileConfig dataclass + YAML/CLI merge
├── platform.py             # SoC families, board registry, capabilities
├── profiler.py             # Top-level orchestrator
├── engines/                # One adapter per inference engine
│   ├── __init__.py
│   ├── base.py             # EngineAdapter protocol/ABC
│   ├── tflm.py
│   ├── helia_rt.py
│   └── helia_aot.py
├── firmware/                # NSX app generation
│   ├── __init__.py
│   ├── app_gen.py          # Template rendering + nsx.yml assembly
│   └── templates/          # Jinja2 templates for profiler firmware
│       ├── CMakeLists.txt.j2
│       ├── nsx.yml.j2
│       └── src/
│           └── main.c.j2
├── capture/                # Data acquisition from target
│   ├── __init__.py
│   ├── serial.py           # SWO / USB-serial structured data reader
│   ├── pmu.py              # PMU counter parsing + per-layer aggregation
│   └── power.py            # Joulescope capture adapter
├── report/                 # Output formatting
│   ├── __init__.py
│   ├── csv_report.py
│   ├── json_report.py
│   └── summary.py          # Terminal pretty-print
└── _version.py             # Single version source
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
  backend: joulescope           # joulescope | (future: on-board monitor)
  duration_s: 30
  io_voltage: 1.8

output:
  format: csv                   # csv | json
  dir: ./results
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

### 5.5 Firmware App Generation

The profiler firmware is a purpose-built NSX app rendered from Jinja templates.
It is generated into a temporary (or user-specified) directory and built with
the standard NSX pipeline.

The firmware:

1. Initializes the platform via `ns_core_init()`.
2. Loads the model using the selected engine's API.
3. For each PMU preset:
   a. Configures PMU counters via `nsx-pmu-armv8m`.
   b. Runs warmup iterations.
   c. For each measured iteration, captures per-layer or whole-model counters.
4. Emits structured results over SWO (or USB serial) in a parseable format.
5. Optionally enters a power measurement loop with GPIO phase signaling.

### 5.6 Data Capture

**PMU data** is emitted by the firmware as structured text (CSV lines or tagged
records) over SWO/ITM or USB serial. The host-side capture module reads the
serial stream, parses records, and aggregates per-layer statistics.

**Power data** is captured via Joulescope in a parallel thread, synchronized
with GPIO phase signals from the firmware. The capture module accumulates
per-phase statistics (active, idle, total).

No RPC framework is used. The firmware pushes data; the host listens and
parses.

### 5.7 Report Generation

Results are written as:

- **Terminal summary:** Human-readable table with per-layer cycle counts,
  instruction counts, cache stats, and (if enabled) power/energy.
- **CSV:** One row per layer per PMU preset, suitable for spreadsheet analysis.
- **JSON:** Structured output for programmatic consumption.

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

| Flag | Type | Description |
| --- | --- | --- |
| `MODEL_PATH` | positional | Path to .tflite model file |
| `--config` | path | YAML config file (overrides/supplements CLI flags) |
| `--engine` | choice | `tflm`, `helia-rt`, `helia-aot` |
| `--engine-config` | path | Engine-specific YAML (e.g. heliaAOT config) |
| `--board` | string | Target board (default: `apollo510_evb`) |
| `--toolchain` | string | Toolchain (default: `arm-none-eabi-gcc`) |
| `--arena-size` | int | Tensor arena size in bytes |
| `--pmu-presets` | list | PMU preset names to capture |
| `--per-layer` / `--no-per-layer` | flag | Per-layer breakdown (default: on) |
| `--iterations` | int | Inference iterations (default: 100) |
| `--power` | flag | Enable Joulescope power capture |
| `--power-duration` | int | Power capture seconds (default: 30) |
| `--output-dir` | path | Results output directory |
| `--output-format` | choice | `csv`, `json` |
| `--work-dir` | path | Working directory for generated firmware (default: temp) |
| `--keep-work-dir` | flag | Don't delete working directory after profiling |
| `--verbose` / `-v` | count | Increase verbosity |

CLI flags override YAML values. YAML provides defaults for reproducible runs.

## 7. Dependencies

### Runtime (installed with `hpx`)

- `PyYAML` — config parsing
- `Jinja2` — firmware template rendering
- `pyserial` — USB serial capture
- `neuralspotx` — NSX Python API for configure/build/flash (or subprocess fallback)

### Optional

- `joulescope` — power measurement (only needed with `--power`)
- `helia-aot` — AOT compilation (only needed with `--engine helia-aot`)

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
| Subprocess calls | `subprocess.run()` with argument lists, no shell=True |
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
