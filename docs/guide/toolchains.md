# Toolchains

heliaPROFILER builds firmware through [NSX](https://github.com/AmbiqAI/neuralspotx),
which supports three Cortex-M cross-compilers. The choice of toolchain
affects build time, binary size, and inference performance.

## Supported toolchains

| `target.toolchain` value | Compiler | Linker | Status |
|---|---|---|---|
| `arm-none-eabi-gcc` *(default)*, `gcc` | GCC | GNU `ld` | Stable |
| `armclang` | Arm Compiler 6 | `armlink` | Stable |
| `atfe` | Arm Toolchain for Embedded (LLVM-based) | LLD | Preview |

Selection happens via:

```bash
hpx profile model.tflite --toolchain armclang
```

or in YAML:

```yaml
target:
  toolchain: armclang
```

The default is `arm-none-eabi-gcc`, which is what `gcc` aliases to.

## How heliaPROFILER drives the toolchain

The selected toolchain flows through to NSX:

```text
hpx profile --toolchain X
   └─► firmware build stage
       └─► nsx configure --toolchain X
```

GCC is special-cased: when `arm-none-eabi-gcc`/`gcc` is selected the
profiler omits the `--toolchain` flag entirely so NSX uses its default
GCC configuration. `armclang` and `atfe` are passed through explicitly.

The profiler also probes binary sections with `arm-none-eabi-size` for GCC,
`fromelf` for Arm Compiler 6, or `llvm-size` from `ATFE_ROOT/bin` for ATfE.

## GCC (default)

Free, widely available, well-documented.

### Install

=== "macOS"

    ```bash
    # Download from https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads
    # Extract to /Applications/ArmGNUToolchain/<version>/
    export PATH="/Applications/ArmGNUToolchain/14.3.rel1/arm-none-eabi/bin:$PATH"
    ```

=== "Linux"

    ```bash
    sudo apt install gcc-arm-none-eabi
    # Or download a newer release from developer.arm.com
    ```

=== "Windows"

    Use the official installer from
    [developer.arm.com](https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads).

Verify:

```bash
arm-none-eabi-gcc --version
arm-none-eabi-size --version
```

### When to use GCC

- First-time setup or CI on machines without a paid Arm Compiler license.
- Cross-platform build reproducibility — works the same on all OSes.
- The default heliaRT registry module is built with GCC through NSX.

## armclang (Arm Compiler 6)

Commercial Arm Compiler 6. Generally produces faster code than GCC on
Cortex-M55 workloads, particularly for MVE-heavy kernels.

### Install

armclang ships with [Arm Development Studio](https://developer.arm.com/Tools%20and%20Software/Arm%20Development%20Studio)
or the standalone Arm Compiler for Embedded. A license is required.

```bash
# Add the toolchain bin/ to PATH
export PATH="/path/to/ArmCompilerForEmbedded/<version>/bin:$PATH"

# Point at the license server (or an offline license file)
export ARMLMD_LICENSE_FILE="<port>@<server>"
```

Verify:

```bash
armclang --version
fromelf --version
```

### When to use armclang

- You have an Arm Compiler license available.
- You're optimizing for cycle count on Apollo510 / Cortex-M55.
- You want to validate that performance numbers replicate across
  toolchains before committing to one.

## ATfE (Arm Toolchain for Embedded)

Arm's newer LLVM-based toolchain. Free and open-source. Currently in
preview within heliaPROFILER.

### Install

Download from
[developer.arm.com](https://developer.arm.com/downloads/-/arm-toolchain-for-embedded)
and set `ATFE_ROOT` to the extracted toolchain root:

```bash
export ATFE_ROOT="/path/to/atfe/<version>"

"$ATFE_ROOT/bin/clang" --version
"$ATFE_ROOT/bin/llvm-size" --version
```

HPX expects `clang`, `clang++`, `llvm-ar`, `llvm-objcopy`, `llvm-size`, and
`llvm-nm` under `$ATFE_ROOT/bin`. The default heliaRT registry flow builds the
runtime and profiler firmware with ATfE end to end.

## Toolchain comparison

Compiler results depend on the model, engine, optimization settings, and target
clock. Use the [toolchain comparison example](../examples/toolchain-comparison.md)
to measure the trade-off on your own workload.

## Switching toolchains mid-experiment

```bash
# Same model, three toolchains, three result directories
hpx profile model.tflite --toolchain gcc      --output-dir results/gcc
hpx profile model.tflite --toolchain armclang --output-dir results/armclang
hpx profile model.tflite --toolchain atfe     --output-dir results/atfe
```

The work directory is not shared between toolchains — each run
re-configures and re-builds from scratch.

## Troubleshooting

??? failure "`armclang: command not found`"
    The Arm Compiler `bin/` directory is not on `PATH`. Source the
    setup script that ships with Arm Development Studio, or add the path
    manually.

??? failure "`License checkout failed (-15)`"
    Set `ARMLMD_LICENSE_FILE` to point at your license server or file.
    `armclang --version` works without consuming a license; the build
    will fail when the actual compile step requests one.

??? failure "`ATFE_ROOT` is missing or incomplete"
    Set `ATFE_ROOT` to the extracted toolchain root, not its `bin/`
    directory. Confirm that `$ATFE_ROOT/bin/clang` and
    `$ATFE_ROOT/bin/llvm-size` exist.

??? failure "Different cycle counts on different toolchains"
    Expected. Toolchain choice is one of several variables that affect
    code generation. To isolate, hold the engine, board, and counter
    config constant; only vary `--toolchain`.
