# Firmware Generation

heliaPROFILER generates temporary, disposable firmware for each profiling run.
The firmware is a thin harness that runs the model, captures PMU counters, and
prints structured data over the selected transport.

## Template system

Firmware source files are generated from Jinja2 templates stored in
`src/helia_profiler/firmware/templates/`.

### Template files

| Template | Purpose |
|---|---|
| `CMakeLists.txt.j2` | Top-level CMake project file |
| `nsx.yml.j2` | NSX project manifest (module list + registry pins) |
| `modules.cmake.j2` | Rendered to `cmake/nsx/modules.cmake` — module wiring |
| `_main_base.cc.j2` | **Shared firmware skeleton** — boot, transport preamble, GPIO sync, clean window, PMU pass loop, teardown. Not rendered directly. |
| `main.cc.j2` | Child of `_main_base.cc.j2` — heliaRT / TFLM-style interpreter path |
| `main_aot.cc.j2` | Child of `_main_base.cc.j2` — heliaAOT (direct function calls) |
| `main_executorch.cc.j2` | Child of `_main_base.cc.j2` — ExecuTorch (`Method` load + static buffers) |
| `hpx_pmu_profiler.cc.j2` | PMU capture harness (interpreter path) |
| `hpx_pmu_profiler.h.j2` | PMU capture header |

Alongside these, the directory holds ~20 underscore-prefixed **partials**
(`_hpx_printf.j2`, `_dwt_init.j2`, `_system_includes.j2`,
`_power_terminal.j2`, `_psram_metadata.j2`, ...) — shared fragments included
by the skeleton so transport setup, timer init, power-measurement hooks, and
similar blocks are written once rather than per-engine.

```
templates/
├── _main_base.cc.j2          ← the skeleton; owns everything engine-agnostic
│   ├── main.cc.j2            ← {% extends %} — heliaRT / TFLM
│   ├── main_aot.cc.j2        ← {% extends %} — heliaAOT
│   └── main_executorch.cc.j2 ← {% extends %} — ExecuTorch
└── _*.j2                     ← partials, {% include %}d by the skeleton
```

### The block contract

Everything outside an `engine_*` block in `_main_base.cc.j2` is shared by every
engine; the blocks are the only place engine-specific code may appear. A child
can override a block, but it can never remove base text. Twelve of them
(`engine_invoke`, `engine_globals`, `engine_pass_init`, `engine_print_csv`, ...)
are **required** — the base renders nothing for them, so a child that misses one
ships firmware with that engine's code simply absent. The rest have working
defaults; a child overrides one only when the default is wrong for it (the
clearest case is `engine_clean_window`, which ExecuTorch owns because its invoke
reloads the model and reports its own execute-only cycle count).

The failure mode inheritance introduces is silence: a child block whose name
matches nothing in the base is ignored, the base's default renders in its place,
and nothing errors. `tests/contracts/test_template_blocks.py` makes that a test
failure — it pins the base's block inventory, each child's override set, the
required set, and the whitespace shape of every override (the render env runs
with `trim_blocks`/`lstrip_blocks` off, so an override that does not lead with a
newline where the base anchors it to a `//` comment is silently commented out of
the firmware). Read `_main_base.cc.j2`'s prelude for the whitespace contract
itself.

### Template context

Templates receive a merged context combining:

1. **Config values** — board name, SoC, arena size, iteration count
2. **Engine artifacts** — the typed fields of the engine's artifact type
   (`TflmArtifacts` / `HeliaRtArtifacts` / `HeliaAotArtifacts` /
   `ExecutorchArtifacts`: `engine_header` and `cmake_vars` on the common
   core, AOT arena regions and ExecuTorch buffer sizes on their owning
   subtypes; see `engines/base.py`)
3. **Counter passes** — PMU counter IDs grouped by compute unit and hardware capacity
4. **Platform features** — DSP, MVE, FPU flags

Example context for a heliaRT run:

```python
{
    "board": "apollo510_evb",
    "soc": "apollo510",
    "arena_size": 131072,
    "iterations": 10,
    "warmup": 5,
    "engine_type": "helia-rt",
    "pmu_passes": [
        {"name": "cpu_0", "event_ids": ["0x0011", "0x0008"], "counter_names": ["ARM_PMU_CPU_CYCLES", "ARM_PMU_INST_RETIRED"]},
        {"name": "memory_0", "event_ids": ["0x0004", "0x0003"], "counter_names": ["ARM_PMU_L1D_CACHE", "ARM_PMU_L1D_CACHE_REFILL"]},
    ],
    "has_mve": True,
    "has_dsp": True,
    # Module specs come from the board's NSX starter profile plus the
    # engine's extra modules — e.g. nsx-core, nsx-cmsis-core, nsx-ambiq-bsp,
    # nsx-board-apollo510-evb, ..., nsx-helia-rt
    "modules": [...],
}
```

## Generated firmware structure

After template rendering, the work directory contains a complete NSX app:

```
work_dir/
├── CMakeLists.txt
├── nsx.yml
├── cmake/
│   └── nsx/
│       └── modules.cmake
├── src/
│   ├── main.cc              ← main.cc.j2, main_aot.cc.j2, or main_executorch.cc.j2
│   ├── main_power.cc        ← optional dedicated power binary (power_only render)
│   ├── model_data.h         ← embedded model bytes (interpreter/ExecuTorch path)
│   ├── hpx_pmu_profiler.cc  ← PMU capture harness (TFLM/heliaRT path)
│   ├── hpx_pmu_profiler.h
│   └── rtt/                 ← vendored SEGGER RTT sources (RTT transport)
└── modules/                 ← vendored local NSX modules (engine-created)
    └── hpx_model/           ← (heliaAOT) compiled model code — default module name
        ├── nsx-module.yaml
        ├── include/
        └── src/
```

Local engine modules are vendored under `modules/<project-or-name>` (with an
alias directory when the module name differs from its owning project, e.g. a
local `nsx-helia-rt` wrapper in project `helia-rt`). Registry-resolved engine
modules (the common case for heliaRT, TFLM, and ExecuTorch's provider) are
not copied — NSX fetches them during configure.

## NSX module wiring

The firmware depends on NSX modules from three sources:

### Starter profile modules

hpx does **not** maintain its own SDK-tier table. The board's NSX starter
profile is the single source of truth for the base module set; hpx takes the
profile's module list verbatim, minus the legacy `nsx-harness` / `nsx-utils`
helpers it deliberately does not consume (`firmware/project.py`), plus
`nsx-pmu-armv8m` when the SoC uses the Armv8-M PMU backend and the profile
omits it. A typical Apollo510 profile contributes:

| Module | Purpose |
|---|---|
| `nsx-core` | Runtime helpers, retarget, RTOS stubs |
| `nsx-cmsis-core` / `nsx-cmsis-startup` | CMSIS core headers, vector table, linker scripts |
| `nsx-soc-hal` | SoC HAL abstraction |
| `nsx-ambiqsuite`, `nsx-ambiq-hal`, `nsx-ambiq-bsp` | AmbiqSuite SDK, HAL, and BSP |
| `nsx-board-<board>` | Board definition module |
| `nsx-pmu-armv8m` | Armv8-M PMU driver (appended for PMU-capable SoCs) |

The Ambiq SDK modules are owned by the unified `nsx-ambiq-sdk` project. Some
starter profiles still list family-suffixed module names (e.g.
`nsx-ambiqsuite-r5`), which the profile's `module_overrides` repoint onto the
same unified project — hpx resolves ownership through the profile rather than
hard-coding it. Project/module revisions are pinned by the compatibility
baseline (`src/helia_profiler/data/compatibility-baseline-v1.json`).

### Engine modules (`EngineArtifacts.extra_modules`)

| Engine | Modules added |
|---|---|
| TFLM | `nsx-tflite-micro` (+ `arm-cmsis-nn` for the CMSIS-NN backend) |
| heliaRT | `nsx-helia-rt` (registry; local wrapper only for source/dist overrides) |
| heliaAOT | `hpx_model` (local, generated) + `nsx-cmsis-nn` |
| ExecuTorch | provider (`arm-cmsis-nn` or `nsx-cmsis-nn`) + `nsx-executorch` wrapper |

### Local modules (engine-generated)

Created by the engine adapter's `prepare()` method and vendored into the
work directory's `modules/` tree, where `cmake/nsx/modules.cmake` and the
NSX lock can resolve them.

## The firmware's runtime behavior

At a high level, the generated firmware does:

```
1. Initialize SoC (clocks, cache, selected transport)
2. Print "--- HPX_START ---" and HPX_<KEY>=<value> metadata lines
3. For each PMU preset:
   a. Configure PMU with this preset's counter IDs
   b. Print "--- HPX_PRESET <name> ---"
   c. Run warmup iterations (PMU enabled but results discarded)
   d. For each profiling iteration:
      - Print "--- HPX_ITER <n> ---" and the CSV header row
      - For each layer:
        - Reset PMU counters
        - Execute layer
        - Read PMU counters
        - Print CSV row over the selected transport
4. Print "--- HPX_END ---"
5. Enter sleep (wait for reset)
```

The transport output is captured by the host and parsed into `PmuResult`.

## The arm_mve.h workaround

On GCC 14+ with Cortex-M55 (MVE/Helium), the `arm_mve.h` intrinsics header
defines C++ function overloads that conflict with CMSIS-NN headers when
included in certain orders. The `main_aot.cc.j2` template works around this
by pre-including `arm_mve.h` before any other headers:

```cpp
// main_aot.cc.j2 (simplified)
#include <arm_mve.h>  // Must be first — GCC 14 C++ overload fix
#include "hpx_common.h"
#include "hpx_model.h"
```

This is only needed for heliaAOT because the AOT-generated headers pull in
CMSIS-NN types that trigger the overload conflict.
