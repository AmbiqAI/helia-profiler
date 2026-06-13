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

Power-measurement support (`pyjoulescope_driver`) is now installed by
default with `helia-profiler`. You only need a Joulescope JS110/JS220
plugged in and the appropriate udev rules (Linux) to start capturing.

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
You need:

- **JLinkExe** — flash firmware to the EVB
- **`pylink-square`** — Python bindings used for RTT and SWO capture

Install `pylink-square` with your Python environment if you plan to use the
default RTT transport or SWO:

```bash
pip install pylink-square
```

## neuralspotx (nsx)

```bash
pip install 'neuralspotx>=0.6.7,<0.7'
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
✓ neuralspotx Python package  installed
✓ pylink Python package (RTT/SWO transport)  installed
✓ nsx                 <version>
```

All required tools show ✓. Optional tools show ○ when not installed.
