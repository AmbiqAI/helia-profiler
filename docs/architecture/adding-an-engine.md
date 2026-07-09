# Adding a New Engine

This guide walks through adding a new inference engine to heliaPROFILER. By the
end, `hpx profile --engine your-engine` will build, flash, and profile firmware
using your engine.

## Prerequisites

Before starting, you need:

- A working NSX module (or source tree) for your engine
- A way to run inference that can be instrumented per-layer
- Familiarity with the [Engine Adapters](engine-adapters.md) architecture

## Step 1: Create the adapter

Create `src/helia_profiler/engines/your_engine.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..engines.base import EngineAdapter, EngineArtifacts, NsxModuleRef
from ..config import EngineConfig
from ..platform import PlatformInfo


class YourEngineAdapter:
    """Engine adapter for YourEngine."""

    name = "your-engine"

    def prepare(
        self,
        model_path: Path,
        config: EngineConfig,
        platform_info: PlatformInfo,
        work_dir: Path,
    ) -> EngineArtifacts:
        # 1. Validate inputs
        # 2. Create local NSX module(s) if needed
        # 3. Return artifacts

        modules = [
            NsxModuleRef("nsx-core"),
            NsxModuleRef("nsx-harness"),
            NsxModuleRef("your-engine-module"),
        ]

        template_vars = {
            "engine": self.name,
            # Add engine-specific template variables
        }

        return EngineArtifacts(
            modules=modules,
            template_vars=template_vars,
        )
```

### Key requirements

Your `prepare()` method must:

1. **Return NSX module refs** — the build system needs to know what to link
2. **Provide template variables** — the firmware template needs engine-specific
   values (include paths, library names, function signatures)
3. **Be idempotent** — calling `prepare()` twice with the same inputs should
   produce the same output

## Step 2: Create the firmware template

Create `src/helia_profiler/firmware/templates/main_your_engine.cc.j2`:

```cpp
// main_your_engine.cc.j2
#include "hpx_pmu_profiler.h"

// Include your engine headers
#include "your_engine.h"

int main(void) {
    // 1. Initialize SoC (provided by hpx_common)
    hpx_init();

    // 2. Initialize your engine
    your_engine_init(model_data, model_size);

    // 3. Print HPX_START
    hpx_start();

    // 4. For each PMU preset
    {% for preset in pmu_presets %}
    {
        hpx_configure_pmu({{ preset.counter_ids }});
        hpx_print_preset("{{ preset.name }}");
        hpx_print_counters({{ preset.counter_names }});

        // Warmup
        for (int w = 0; w < {{ warmup }}; w++) {
            your_engine_invoke();
        }

        // Profiling iterations
        for (int iter = 0; iter < {{ iterations }}; iter++) {
            hpx_print_iter(iter);

            for (int layer = 0; layer < layer_count; layer++) {
                hpx_pmu_reset();
                your_engine_invoke_layer(layer);
                hpx_pmu_read_and_print(layer, op_names[layer]);
            }
        }

        hpx_print_preset_done();
    }
    {% endfor %}

    // 5. Print HPX_END
    hpx_end();

    while (1) { __WFI(); }
}
```

### Critical contract

Your template **must** follow the HPX protocol exactly:

- Print `HPX_START` before any data
- Print `HPX_META` lines with model metadata
- For each preset: `HPX_PRESET` → `HPX_COUNTERS` → iter/layer data → `HPX_PRESET_DONE`
- Print `HPX_END` when complete

The parser depends on this protocol. See [Data Capture](capture.md) for the
full protocol specification.

### Per-layer instrumentation

The key challenge for any new engine is **per-layer invocation**. Your engine
must support running one layer at a time so PMU counters can be read between
layers. If your engine only supports full-model inference, you'll need to:

- Add per-layer hooks to the engine, OR
- Profile at whole-model granularity (less useful but still valid)

## Step 3: Register the engine

Add your engine to the factory in `engines/__init__.py`:

```python
def get_engine_adapter(engine_type: str) -> EngineAdapter:
    if engine_type == "helia-rt":
        from .helia_rt import HeliaRtAdapter
        return HeliaRtAdapter()
    elif engine_type == "helia-aot":
        from .helia_aot import HeliaAotAdapter
        return HeliaAotAdapter()
    elif engine_type == "your-engine":
        from .your_engine import YourEngineAdapter
        return YourEngineAdapter()
    else:
        raise EngineError(f"Unknown engine: {engine_type}")
```

## Step 4: Update template selection

In `firmware/__init__.py`, add your template to the selection logic:

```python
def _get_main_template(engine: str) -> str:
    return {
        "helia-aot": "main_aot.cc.j2",
        "your-engine": "main_your_engine.cc.j2",
    }.get(engine, "main.cc.j2")
```

## Step 5: Add tests

Create `tests/test_your_engine.py` with at minimum:

```python
def test_prepare_returns_valid_artifacts(tmp_path):
    """prepare() returns EngineArtifacts with required fields."""
    adapter = YourEngineAdapter()
    artifacts = adapter.prepare(
        model_path=Path("test.tflite"),
        config=EngineConfig(type="your-engine"),
        platform_info=mock_platform_info(),
        work_dir=tmp_path,
    )
    assert artifacts.modules
    assert "engine" in artifacts.template_vars

def test_prepare_creates_nsx_module(tmp_path):
    """prepare() creates the local NSX module directory."""
    adapter = YourEngineAdapter()
    adapter.prepare(...)
    assert (tmp_path / "your-engine-module" / "nsx.yml").exists()
```

## Step 6: Document the engine

Add a section to [Engines](../guide/engines.md) describing:

- What the engine is and when to use it
- Installation requirements
- Config options specific to this engine
- Known limitations

## Checklist

- [ ] Adapter class implementing `EngineAdapter` protocol
- [ ] `prepare()` returns valid `EngineArtifacts`
- [ ] Firmware template following HPX protocol
- [ ] Per-layer instrumentation (or documented limitation)
- [ ] Registered in engine factory
- [ ] Template selection updated
- [ ] Tests for `prepare()` and template rendering
- [ ] Documentation in user guide
- [ ] End-to-end test with a real model (manual)
