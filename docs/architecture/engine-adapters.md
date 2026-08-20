# Engine Adapters

The engine adapter layer decouples the profiling pipeline from specific
inference frameworks. Each engine implements the same `EngineAdapter` protocol,
letting the pipeline treat them uniformly.

## The EngineAdapter protocol

```python
class EngineAdapter(Protocol):
    """Prepares engine-specific build artifacts for NSX firmware."""

    @property
    def name(self) -> str: ...

    @property
    def engine_type(self) -> EngineType: ...

    def prepare(
        self,
        config: ProfileConfig,
        work_dir: Path,
    ) -> EngineArtifacts:
        ...
```

Every adapter receives:

| Parameter | Purpose |
|---|---|
| `config` | Resolved profile config, including engine-specific settings |
| `work_dir` | Writable directory for generated files |

And returns `EngineArtifacts`:

```python
@dataclass
class EngineArtifacts:
    engine_type: EngineType = EngineType.TFLM
    extra_modules: list[NsxModuleRef] = field(default_factory=list)
    cmake_vars: dict[str, str] = field(default_factory=dict)
    source_files: list[Path] = field(default_factory=list)
    include_dirs: list[Path] = field(default_factory=list)
    static_libs: list[Path] = field(default_factory=list)
    engine_header: str = TFLM_ENGINE_HEADER
    # ... plus the remaining typed template fields consumed by the renderer
```

See `engines/base.py` for the full field list — it is the single source of
truth and grows as engines need new template inputs.

## heliaRT adapter

**File:** `engines/helia_rt/adapter.py`

heliaRT is Ambiq's optimized TFLM fork. The normal flow resolves the pinned
`nsx-helia-rt` registry module and builds it with the selected toolchain.
Advanced users can instead provide a local source checkout or an explicit
prebuilt distribution.

### What `prepare()` does

1. Validates the requested heliaRT variant and selected toolchain.
2. With no override, returns the pinned registry module reference for NSX.
3. With `source_path`, installs the local source tree as an NSX module.
4. With `dist_path` or a custom release source, verifies the matching
   core/toolchain archive and generates a temporary NSX wrapper.
5. Returns the module references and interpreter metadata needed by firmware
   generation.

### Assumptions

- Explicit prebuilt archives must match the target core, toolchain, and
  requested variant.
- The default registry and local-source modes build heliaRT with the selected
  toolchain.

## heliaAOT adapter

**File:** `engines/helia_aot/adapter.py`

heliaAOT compiles the TFLite model into optimized C code at build time,
eliminating the interpreter overhead.

### What `prepare()` does

1. Runs the `helia-aot` CLI compiler on the model file:
    - Reads the model ops → generates operator manifest
    - Emits C source files for each layer
    - Produces `hpx_model.h` and `hpx_model.cc`
2. Creates or resolves two NSX modules:
    - **hpx_model** — the generated C code, vendored as a local module
      (default name; override with `engine.config.module_name`)
    - **nsx-cmsis-nn** — AmbiqAI's CMSIS-NN fork, from the registry by
      default or an explicit local override
3. Returns the artifact bundle and AOT-specific metadata

### AOT fields on `EngineArtifacts`

```python
EngineArtifacts(
    engine_type=EngineType.HELIA_AOT,
    extra_modules=[cmsis_nn_ref, NsxModuleRef(name="hpx_model", ...)],
    aot_prefix=...,            # C symbol prefix of the generated model API
    aot_module_name="hpx_model",
    aot_cmake_target=...,      # CMake target the firmware links against
    aot_op_manifest=[...],     # ordered per-op descriptors for main_aot.cc.j2
    aot_arena_regions=[...],   # typed ArenaRegion list bound by bind_arena()
)
```

### Assumptions

- The `helia-aot` pip package must be installed (`pip install helia-aot`)
- The AOT compiler version must match the ns-cmsis-nn module version
- The model must use only ops supported by the AOT compiler
- Uses a different `main.cc` template (`main_aot.cc.j2`) because AOT
  inference calls are direct function invocations, not interpreter runs

## TFLM adapter

**File:** `engines/tflm.py`

Stock TensorFlow Lite for Microcontrollers adapter for interpreter baselines.
It is exposed as `--engine tflm` and supports reference or upstream CMSIS-NN
backends.

### What `prepare()` does

1. Resolves the `nsx-tflite-micro` module
2. Selects the reference or CMSIS-NN backend
3. Returns module refs with standard TFLM template variables

### When to use

TFLM is primarily useful for:

- Establishing an upstream interpreter baseline
- Generating baseline numbers for comparison with heliaRT/heliaAOT

## ExecuTorch adapter

**File:** `engines/executorch.py`

ExecuTorch runs a `.pte` program — a graph lowered ahead of time by
`helia-torch`/`nsx_cortex_m` — through the `nsx-executorch` runtime. Unlike
the LiteRT engines, HPX does not compile the model itself; the PTE arrives
already lowered against a chosen CMSIS-NN provider.

### What `prepare()` does

1. Validates `engine.config.source_path` — a local `nsx-executorch` checkout
   whose `version.txt` **and commit** match the qualified pin in the
   [compatibility baseline](compatibility-baseline.md), including its
   `external/executorch` submodule gitlink and pinned torchgen sources.
2. Loads the optional `<model>.pte.json` sidecar and uses it to default the
   CMSIS-NN provider, `ns_ops`, portable op list, planned arena size, and
   I/O sizes. Explicit config always wins; a hash mismatch is a hard error.
3. Resolves the memory region and size for each of the five static RAM
   buffers (planned arena, method arena, temporary arena, input, output).
4. Generates a thin NSX wrapper module that `add_subdirectory()`s the
   checkout, and emits the provider module ref **before** it so NSX
   configures the provider exactly once.
5. Returns both module refs plus the `NSX_EXECUTORCH_*` CMake variables.

### Assumptions

- The checkout is at the exact pinned commit — HPX refuses to build an
  unqualified runtime rather than reporting drifted numbers.
- The PTE was exported against the same provider being built. A
  `cortex_m_ns::` PTE on a non-NS build fails at `Method::load()`, and HPX
  rejects that combination at config time.
- `arena_location` and the per-buffer overrides may only name RAM regions
  (`tcm`, `sram`).
- Power capture is not supported yet; keep `power.enabled: false`.

## How engines affect the firmware

The engine choice affects three things in the generated firmware:

### 1. Template selection

| Engine | Main template | Includes |
|---|---|---|
| heliaRT | `main.cc.j2` | Interpreter setup, arena allocation |
| heliaAOT | `main_aot.cc.j2` | Direct function calls, `arm_mve.h` pre-include |
| TFLM | `main.cc.j2` | Standard TFLM interpreter path |
| ExecuTorch | `main_executorch.cc.j2` | `Module`/`Method` load, five static RAM buffers |

### 2. NSX module graph

Every run starts from the board's NSX starter profile modules (`nsx-core`,
`nsx-cmsis-core`, the Ambiq SDK/BSP tier, the board module, ... — see
[Firmware Generation](firmware.md)); the adapter appends its
`extra_modules` on top:

```
heliaRT:     base + [nsx-helia-rt]
heliaAOT:    base + [nsx-cmsis-nn, hpx_model]
TFLM:        base + [nsx-tflite-micro]        (arm-cmsis-nn first for the cmsis_nn backend)
ExecuTorch:  base + [<arm|nsx>-cmsis-nn, nsx-executorch]
```

The ExecuTorch provider module is ordered *before* `nsx-executorch` on
purpose — NSX must configure exactly one CMSIS-NN provider before the
runtime creates its idempotent bridge target.

### 3. Binary size and layout

- **heliaRT** produces the largest binaries (~500KB+) because the full
  interpreter and all op kernels are linked
- **heliaAOT** produces the smallest binaries (~80–120KB) because only the
  required ops are compiled as direct code
- **TFLM** is similar to heliaRT in size

This is why `summary.json` includes binary section sizes — they reveal the
practical impact of engine choice on flash usage.
