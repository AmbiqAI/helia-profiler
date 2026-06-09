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

The profiler also probes the toolchain for binary size analysis using
`fromelf` (armclang/ATfE) or `arm-none-eabi-size` (GCC), so both binaries
must be on `PATH`.

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
- The default heliaRT prebuilt archive (`libhelia-rt-gcc.a`) is GCC-built,
  so no extra distribution download is needed.

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
and add the `bin/` to `PATH`.

```bash
export PATH="/path/to/atfe/<version>/bin:$PATH"

# ATfE binaries
atfe --version
fromelf --version
```

### Caveats

!!! info "heliaRT compatibility"
    The pinned heliaRT release does not yet ship a dedicated ATfE archive.
    When `target.toolchain: atfe` is selected, the heliaRT adapter falls
    back to `libhelia-rt-gcc.a` (with a warning). This works but means
    the heliaRT static library itself was GCC-built; only the surrounding
    profiler firmware is built with ATfE. heliaAOT and TFLM build cleanly
    with ATfE end-to-end.

## Toolchain comparison

KWS reference model on Apollo510 EVB, 100 iterations, default counter
set. Numbers come from `results/results_*/summary.json`.

| Engine | Toolchain | Total cycles | vs GCC |
|---|---|---|---|
| heliaRT | gcc | 2,014,841 | 1.00× |
| heliaRT | armclang | 1,874,429 | 0.93× |
| heliaAOT | gcc | 1,965,501 | 1.00× |
| heliaAOT | armclang | 1,869,210 | 0.95× |

armclang is consistently ~5–7% faster on this model. The exact gain
depends heavily on the operator mix and how MVE-friendly the kernels are.
Reproduce on your own model with the
[toolchain comparison example](../examples/toolchain-comparison.md).

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

??? failure "Heliart archive download fails for atfe"
    Expected — heliaRT does not yet publish an ATfE archive. The adapter
    falls back to GCC archive. To build heliaRT itself with ATfE, you'd
    need to build heliaRT from source with ATfE.

??? failure "Different cycle counts on different toolchains"
    Expected. Toolchain choice is one of several variables that affect
    code generation. To isolate, hold the engine, board, and counter
    config constant; only vary `--toolchain`.
