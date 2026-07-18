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
| `nsx.yml.j2` | NSX module manifest (lists dependencies) |
| `main.cc.j2` | Main for heliaRT / TFLM-style interpreter path |
| `main_aot.cc.j2` | Main for heliaAOT (direct function calls) |
| `hpx_pmu_profiler.cc.j2` | PMU capture harness |
| `hpx_pmu_profiler.h.j2` | PMU capture header |
| `modules.cmake.j2` | Local module path overrides |

### Template context

Templates receive a merged context combining:

1. **Config values** — board name, SoC, arena size, iteration count
2. **Engine variables** — from `EngineArtifacts.template_vars`
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
    "engine": "helia-rt",
    "pmu_passes": [
        {"name": "cpu_0", "event_ids": ["0x0011", "0x0008"], "counter_names": ["ARM_PMU_CPU_CYCLES", "ARM_PMU_INST_RETIRED"]},
        {"name": "memory_0", "event_ids": ["0x0004", "0x0003"], "counter_names": ["ARM_PMU_L1D_CACHE", "ARM_PMU_L1D_CACHE_REFILL"]},
    ],
    "has_mve": True,
    "has_dsp": True,
    "modules": ["nsx-core", "nsx-harness", "ns-cmsis-nn", "helia-rt-local"],
}
```

## Generated firmware structure

After template rendering, the work directory contains a complete NSX app:

```
work_dir/
├── CMakeLists.txt
├── nsx.yml
├── modules.cmake
├── src/
│   ├── main.cc              ← from main.cc.j2 or main_aot.cc.j2
│   ├── hpx_pmu_profiler.cc  ← PMU capture harness
│   └── hpx_pmu_profiler.h
└── local_modules/           ← engine-created NSX modules
    ├── helia-rt-local/      ← (heliaRT) wraps static lib
    │   ├── nsx.yml
    │   └── lib/
    └── aot-model/           ← (heliaAOT) compiled model code
        ├── nsx.yml
        ├── include/
        └── src/
```

## NSX module wiring

The firmware depends on NSX modules from multiple sources:

### System modules (from nsx-modules/)

| Module | Purpose |
|---|---|
| `nsx-core` | Startup, retarget, RTOS stubs |
| `nsx-harness` | SWO print, GPIO, timer |
| `ns-cmsis-nn` | AmbiqAI's CMSIS-NN fork |
| `nsx-perf` | PMU helper macros |
| `nsx-cmsis-startup` | Vector table, linker scripts |

### SDK tier modules

The board's SoC determines which SDK tier is used:

| SoC | BSP | HAL | AmbiqSuite |
|---|---|---|---|
| Apollo3p | `nsx-ambiq-bsp-r3` | `nsx-ambiq-hal-r3` | `nsx-ambiqsuite-r3` |
| Apollo4 | `nsx-ambiq-bsp-r4` | `nsx-ambiq-hal-r4` | `nsx-ambiqsuite-r4` |
| Apollo510 | `nsx-ambiq-bsp-r5` | `nsx-ambiq-hal-r5` | `nsx-ambiqsuite-r5` |

### Local modules (engine-generated)

Created by the engine adapter's `prepare()` method. These are placed in the
work directory and referenced via `modules.cmake`.

## The firmware's runtime behavior

At a high level, the generated firmware does:

```
1. Initialize SoC (clocks, cache, selected transport)
2. Print "HPX_START"
3. For each PMU preset:
   a. Configure PMU with this preset's counter IDs
   b. Run warmup iterations (PMU enabled but results discarded)
   c. For each profiling iteration:
      - For each layer:
        - Reset PMU counters
        - Execute layer
        - Read PMU counters
        - Print CSV row over the selected transport
   d. Print "HPX_PRESET_DONE"
4. Print "HPX_END"
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
