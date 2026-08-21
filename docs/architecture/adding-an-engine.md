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

from pathlib import Path

from ..config import ProfileConfig
from ..placement import Placement
from ..results import NsxModuleRef
from . import EngineType
from .base import ArenaRegion, EngineArtifacts


class YourEngineAdapter:
    """Engine adapter for YourEngine."""

    @property
    def name(self) -> str:
        return "YourEngine"

    @property
    def engine_type(self) -> EngineType:
        return EngineType.YOUR_ENGINE  # added in Step 3

    def default_auto_placement(
        self, *, tcm_cap: int, sram_cap: int
    ) -> tuple[Placement, Placement] | None:
        # None = fall through to the shared greedy fastest-fit policy.
        return None

    def apply_arena_placement_override(
        self, regions: list[ArenaRegion], target: Placement
    ) -> list[ArenaRegion]:
        # Identity unless your engine emits AOT-style arena regions.
        return regions

    def prepare(self, config: ProfileConfig, work_dir: Path) -> EngineArtifacts:
        # 1. Validate engine-specific config (config.engine.*)
        # 2. Create local NSX module(s) under work_dir if needed
        # 3. Return artifacts

        extra_modules = [
            NsxModuleRef(
                name="your-engine-module",
                path=Path(),      # unused for registry modules
                local=False,      # True for a module you generated on disk
                project="your-engine-project",
            ),
        ]

        return EngineArtifacts(
            engine_type=EngineType.YOUR_ENGINE,
            extra_modules=extra_modules,
            cmake_vars={"NSX_YOUR_ENGINE_OPTION": "value"},
            engine_header="your_engine/api.h",
        )
```

### Key requirements

Your `prepare()` method must:

1. **Return only *extra* NSX module refs** — the base module set (board, SDK,
   core runtime) comes from the board's NSX starter profile; you only declare
   what your engine adds on top (see `EngineArtifacts.extra_modules`)
2. **Fill the typed template fields** — the firmware renderer consumes typed
   fields on `EngineArtifacts` (`engine_header`, `cmake_vars`, and any
   engine-specific fields you add to `engines/base.py`), not a free-form dict
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

    // 3. Open the session and print metadata
    hpx_printf("\n--- HPX_START ---\n");
    hpx_printf("HPX_VERSION=1\n");
    hpx_printf("HPX_MODEL_SIZE=%u\n", model_size);
    hpx_printf("HPX_ARENA_SIZE=%d\n", kArenaSize);
    hpx_printf("HPX_NUM_PRESETS=%d\n", {{ pmu_passes | length }});
    hpx_printf("HPX_PRESETS={{ pmu_pass_names | join(',') }}\n");

    // 4. For each PMU counter pass
    {% for pass in pmu_passes %}
    {
        hpx_configure_pmu({{ pass.event_ids }});
        hpx_printf("\n--- HPX_PRESET {{ pass.name }} ---\n");

        // Warmup
        for (int w = 0; w < {{ warmup }}; w++) {
            your_engine_invoke();
        }

        // Profiling iterations — first row after HPX_ITER is the CSV header
        for (int iter = 0; iter < {{ iterations }}; iter++) {
            hpx_printf("\n--- HPX_ITER %d ---\n", iter);
            hpx_print_csv_header();  // "Layer","Op",<counters>,"overflow"

            for (int layer = 0; layer < layer_count; layer++) {
                hpx_pmu_reset();
                your_engine_invoke_layer(layer);
                hpx_pmu_read_and_print(layer, op_names[layer]);
            }
        }
    }
    {% endfor %}

    // 5. Close the session
    hpx_printf("\n--- HPX_END ---\n");

    while (1) { __WFI(); }
}
```

### Critical contract

Your template **must** follow the HPX protocol exactly:

- Print `--- HPX_START ---` before any data
- Print `HPX_<KEY>=<value>` metadata lines (`HPX_VERSION`, `HPX_MODEL_SIZE`,
  `HPX_ARENA_SIZE`, `HPX_NUM_PRESETS`, `HPX_PRESETS`, ...)
- For each preset: `--- HPX_PRESET <name> ---`, then per iteration
  `--- HPX_ITER <n> ---` followed by the CSV header row and one CSV data row
  per layer
- Print `--- HPX_END ---` when complete

The parser depends on this protocol. See [Data Capture](capture.md) for the
full protocol specification.

### Per-layer instrumentation

The key challenge for any new engine is **per-layer invocation**. Your engine
must support running one layer at a time so PMU counters can be read between
layers. If your engine only supports full-model inference, you'll need to:

- Add per-layer hooks to the engine, OR
- Profile at whole-model granularity (less useful but still valid)

## Step 3: Register the engine

Registration lives in `engines/__init__.py`. Add a value to the `EngineType`
enum, a deferred factory, and an entry in the adapter registry — factories are
deferred so registering an engine doesn't force-import its (possibly heavy)
module until it is requested:

```python
class EngineType(StrEnum):
    ...
    YOUR_ENGINE = "your-engine"


def _load_your_engine_adapter() -> "EngineAdapter":
    from .your_engine import YourEngineAdapter

    return YourEngineAdapter()


_ADAPTER_FACTORIES: dict[EngineType, "Callable[[], EngineAdapter]"] = {
    ...
    EngineType.YOUR_ENGINE: _load_your_engine_adapter,
}
```

The pipeline instantiates adapters through the existing factory — you don't
add a new function:

```python
def get_adapter(engine_type: EngineType) -> "EngineAdapter":
    ...
```

Tests can swap in a stub with `register_engine_adapter(engine_type, factory)`.

## Step 4: Add a firmware template

If your engine can run through the interpreter path's `main.cc.j2`, skip this
step. Otherwise write a **child of the shared skeleton** — never a standalone
main. Every engine template is a child of `_main_base.cc.j2`, which owns boot,
the transport preamble, GPIO sync, the clean window, the PMU pass loop and
teardown; a standalone template drifts away from that and loses features
silently (the ExecuTorch one did, and had to be converted back in #154).

1. **Create `main_your_engine.cc.j2`** opening with
   `{% extends "_main_base.cc.j2" %}`, and read the base's prelude first: the
   render env has `trim_blocks`/`lstrip_blocks` OFF, so the whitespace shape of
   each override is part of the contract (a region block leads with its own
   newline; a single-line block carries exactly one line; an override anchored
   to a `//` comment that does not lead with a newline is silently commented
   out of the firmware).

2. **Override the required blocks.** These are the ones the base renders
   nothing for, so a missing one ships firmware without your engine's code:
   `engine_file_header`, `engine_includes`, `engine_globals`,
   `engine_heartbeat_arm`, `engine_invoke`, `engine_iteration_setup`,
   `engine_pass_init`, `engine_print_csv`, `engine_profiler_off`,
   `engine_reset_inputs`, `engine_reset_inputs_warm`, `engine_start_metadata`.
   The optional seams (`engine_model_storage`, `engine_model_setup`,
   `engine_pre_start`, `engine_window_prologue`, `engine_window_restore`,
   `engine_profiler_on`, `engine_psram_metadata`, `engine_io_metadata`,
   `engine_early_globals`, `engine_profiled_summary`, ...) have working
   defaults — override one only where the default is wrong for your engine.

   `engine_clean_window` is the seam to think hardest about, and only applies if
   your engine's invoke is **not** a pure inference call. The default brackets
   `self.engine_invoke()` with the window clock, which is correct whenever the
   invoke IS the inference (heliaRT, TFLM, heliaAOT). ExecuTorch overrides it
   because `run_once_profiled()` reloads the model per call and reports its own
   execute-only cycle count, so inheriting the default would silently redefine
   `HPX_CLEAN_INFER_*` as load+execute. If you override it, you own everything
   nested inside it too (`engine_window_prologue`, `engine_window_restore`,
   `engine_profiler_on`) — overriding those as well is a no-op that
   `tests/contracts/test_template_blocks.py` rejects.

3. **Select it in `firmware/__init__.py`.** Template selection is inline (there
   is no separate helper): the render code picks `main_aot.cc.j2` for
   `EngineType.HELIA_AOT`, `main_executorch.cc.j2` for `EngineType.EXECUTORCH`,
   and `main.cc.j2` for everything else. Extend that conditional — in both
   `generate_app()` and `render_power_source()` — to select
   `main_your_engine.cc.j2` for your `EngineType`.

4. **Update `tests/contracts/test_template_blocks.py`**: add the file to
   `CHILDREN` and add your override set to
   `test_child_override_sets_are_the_documented_ones`. The reserved-defaults
   pin will also shift if your child claims a seam no other engine had.

5. **Add the engine to the snapshot matrix** in
   `tests/contracts/test_firmware_render_snapshots.py`: add it to `_ENGINES`,
   give `_render()` a branch that renders your template with the variables
   production hands it (a missing branch raises rather than silently rendering
   `main.cc.j2`), narrow `_ENGINE_SOCS` if it does not run on every family, add
   it to `_MATRIX_ENGINES` only if it supports the dedicated power binary, then
   regenerate with `HPX_UPDATE_SNAPSHOTS=1` and review the JSON diff.

## Step 5: Add tests

Create `tests/test_your_engine.py` with at minimum (see
`tests/test_tflm_adapter.py` for the pattern):

```python
from helia_profiler.config import load_config
from helia_profiler.engines import EngineType
from helia_profiler.engines.your_engine import YourEngineAdapter


def _config(tmp_path):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    return load_config(
        None,
        {"model": {"path": str(model)}, "engine": {"type": "your-engine"}},
    )


def test_prepare_returns_valid_artifacts(tmp_path):
    """prepare() returns EngineArtifacts with required fields."""
    artifacts = YourEngineAdapter().prepare(_config(tmp_path), tmp_path)
    assert artifacts.engine_type is EngineType.YOUR_ENGINE
    assert [m.name for m in artifacts.extra_modules] == ["your-engine-module"]


def test_prepare_creates_nsx_module(tmp_path):
    """prepare() creates the local NSX module directory (if applicable)."""
    YourEngineAdapter().prepare(_config(tmp_path), tmp_path)
    assert (tmp_path / "your-engine-module" / "nsx-module.yaml").exists()
```

## Step 6: Document the engine

Add a section to [Engines](../guide/engines.md) describing:

- What the engine is and when to use it
- Installation requirements
- Config options specific to this engine
- Known limitations

## Checklist

- [ ] Adapter class implementing `EngineAdapter` protocol
- [ ] `prepare(config, work_dir)` returns valid `EngineArtifacts`
- [ ] Firmware template following HPX protocol
- [ ] Per-layer instrumentation (or documented limitation)
- [ ] `EngineType` value and factory registered in `engines/__init__.py`
- [ ] Template selection updated in `firmware/__init__.py`
- [ ] Tests for `prepare()` and template rendering
- [ ] Documentation in user guide
- [ ] End-to-end test with a real model (manual)
