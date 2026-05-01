# Installation

## Requirements

| Dependency | Version | Purpose |
|---|---|---|
| Python | >= 3.11, < 3.13 | Runtime |
| `arm-none-eabi-gcc` | 13.x or 14.x | ARM cross-compiler |
| CMake | >= 3.24 | Build system |
| Ninja | any | Build backend |
| SEGGER J-Link | >= 7.80 | Flash and SWO capture |
| `nsx` CLI | latest | [neuralspotx](https://github.com/AmbiqAI/neuralspotx) build system |

## Install heliaPROFILER

=== "pip"

    ```bash
    pip install helia-profiler
    ```

=== "uv (recommended)"

    ```bash
    uv add helia-profiler
    ```

=== "From source"

    ```bash
    git clone https://github.com/AmbiqAI/helia-profiler.git
    cd helia-profiler
    uv sync
    ```

### Optional extras

For **power measurement** with Joulescope:

```bash
pip install 'helia-profiler[power]'
```

For **heliaAOT** engine support:

```bash
pip install 'helia-profiler[aot]'
```

## ARM GCC Toolchain

=== "macOS"

    Download from [Arm GNU Toolchain Downloads](https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads)
    and install to `/Applications/ArmGNUToolchain/`. Ensure the `bin/` directory
    is on your `PATH`:

    ```bash
    export PATH="/Applications/ArmGNUToolchain/14.3.rel1/arm-none-eabi/bin:$PATH"
    ```

=== "Linux (apt)"

    ```bash
    sudo apt install gcc-arm-none-eabi
    ```

    Or download the latest release from the Arm website for a newer version.

## SEGGER J-Link

Download and install from [segger.com/jlink](https://www.segger.com/downloads/jlink/).
You need both:

- **JLinkExe** — flash firmware to the EVB
- **JLinkSWOViewerCL** — capture SWO trace data

## neuralspotx (nsx)

```bash
pip install neuralspotx
```

Verify `nsx` is available:

```bash
nsx --version
```

## Verify Everything

Run the built-in dependency checker:

```bash
hpx doctor
```

Expected output:

```
✓ arm-none-eabi-gcc   14.3.1
✓ cmake               3.31.6
✓ ninja               1.12.1
✓ JLinkExe            V8.12a
✓ JLinkSWOViewerCL    V8.12a
✓ nsx                 0.4.0
○ joulescope          (optional — install with pip install 'helia-profiler[power]')
```

All required tools show ✓. Optional tools show ○ when not installed.
