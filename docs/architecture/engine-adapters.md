# Engine Adapters

The engine adapter layer decouples the profiling pipeline from specific
inference frameworks. Each engine implements the same `EngineAdapter` protocol,
letting the pipeline treat them uniformly.

## The EngineAdapter protocol

```python
class EngineAdapter(Protocol):
    """Prepares engine-specific build artifacts for NSX firmware."""

    name: str
    engine_type: EngineType

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
@dataclass(frozen=True)
class EngineArtifacts:
    engine_type: EngineType
    extra_modules: list[NsxModuleRef] = field(default_factory=list)
    static_libs: list[Path] = field(default_factory=list)
    memory_plan: MemoryPlan | None = None
```

## heliaRT adapter

**File:** `engines/helia_rt/adapter.py`

heliaRT is Ambiq's optimized TFLM fork, distributed as a pre-built static
library (`libhelia-rt.a`).

### What `prepare()` does

1. Validates that `dist_path` exists and contains the correct library variant
2. Creates a local NSX module (`helia-rt-local/`) that wraps the static lib:
    - Writes `nsx.yml` pointing to the `.a` file
    - Sets include paths for the RT headers
3. Returns an artifact bundle pointing at `helia-rt-local` plus the metadata the firmware renderer needs

### Template variables

```python
{
    "engine": "helia-rt",
    "helia_rt_include": "<path>/include",
    "helia_rt_lib": "<path>/lib/libhelia-rt.a",
}
```

### Assumptions

- The static library was built with the **same** GCC version as the profiling
  firmware. Mixing ARM GCC 13 libs with GCC 14 firmware may cause link errors.
- The library variant must match the build variant (`release-with-logs` is
  required for SWO output).

## heliaAOT adapter

**File:** `engines/helia_aot/adapter.py`

heliaAOT compiles the TFLite model into optimized C code at build time,
eliminating the interpreter overhead.

### What `prepare()` does

1. Runs the `helia-aot` CLI compiler on the model file:
    - Reads the model ops → generates operator manifest
    - Emits C source files for each layer
    - Produces `hpx_model.h` and `hpx_model.cc`
2. Creates two local NSX modules:
    - **aot-model/** — the generated C code
    - **ns-cmsis-nn/** — AmbiqAI's CMSIS-NN fork (required — not upstream)
3. Returns the artifact bundle and AOT-specific metadata

### Template variables

```python
{
    "engine": "helia-aot",
    "aot_model_dir": "<work_dir>/aot-model",
    "aot_op_manifest": [...],  # list of ops for #include generation
}
```

### Assumptions

- The `helia-aot` pip package must be installed (`pip install helia-aot`)
- The AOT compiler version must match the ns-cmsis-nn module version
- The model must use only ops supported by the AOT compiler
- Uses a different `main.cc` template (`main_aot.cc.j2`) because AOT
  inference calls are direct function invocations, not interpreter runs

## TFLM adapter (internal)

**File:** `engines/tflm.py`

Stock TensorFlow Lite for Microcontrollers adapter retained in source for the
shared interpreter path. It is not currently exposed by `hpx engines` / `--engine`.

### What `prepare()` does

1. Locates the TFLM source or pre-built library
2. Creates NSX module references for TFLM + CMSIS-NN
3. Returns module refs with standard TFLM template variables

### When to use

TFLM is primarily useful for:

- Validating that a model runs correctly before trying optimized engines
- Generating baseline numbers for comparison with heliaRT/heliaAOT

## How engines affect the firmware

The engine choice affects three things in the generated firmware:

### 1. Template selection

| Engine | Main template | Includes |
|---|---|---|
| heliaRT | `main.cc.j2` | Interpreter setup, arena allocation |
| heliaAOT | `main_aot.cc.j2` | Direct function calls, `arm_mve.h` pre-include |
| TFLM | `main.cc.j2` | Standard TFLM interpreter path |

### 2. NSX module graph

```
heliaRT:   [nsx-core, nsx-harness, ns-cmsis-nn, helia-rt-local]
heliaAOT:  [nsx-core, nsx-harness, ns-cmsis-nn, aot-model]
TFLM:      [nsx-core, nsx-harness, ns-cmsis-nn, tflm]
```

### 3. Binary size and layout

- **heliaRT** produces the largest binaries (~500KB+) because the full
  interpreter and all op kernels are linked
- **heliaAOT** produces the smallest binaries (~80–120KB) because only the
  required ops are compiled as direct code
- **TFLM** is similar to heliaRT in size

This is why `summary.json` includes binary section sizes — they reveal the
practical impact of engine choice on flash usage.
