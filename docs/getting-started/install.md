# Installation

heliaPROFILER (`hpx`) needs Python plus a small set of embedded-development
tools: an ARM cross-compiler, CMake/Ninja, and SEGGER J-Link software. Power
capture additionally needs a Joulescope JS110/JS220 and, on Linux, a udev
rule for non-root USB access.

!!! warning "Alpha"
    heliaPROFILER is pre-1.0. Breaking changes may land on **minor**
    versions until v1.0 — pin an exact version (`pip install
    helia-profiler==0.1.0`) for anything long-lived.

## Requirements

| Dependency | Version | Purpose |
|---|---|---|
| Python | `>= 3.11, < 3.13` | Runtime |
| `arm-none-eabi-gcc` | 13.x or 14.x | Default ARM cross-compiler |
| CMake | `>= 3.24` | Build system |
| Ninja | any | Build backend |
| SEGGER J-Link software | `>= 7.80` | Flash and RTT/SWO capture |
| `neuralspotx` (`nsx`) | latest `0.7.x` | Firmware build pipeline (installed automatically as a dependency) |

`armclang` and ATfE are optional alternative toolchains — see
[Toolchains](../guide/toolchains.md). A Joulescope JS110/JS220 is optional
and only needed for power capture — see [Power Measurement](../guide/power.md).

## 1. Install heliaPROFILER

=== "Linux"

    ```bash
    # pip
    pip install helia-profiler

    # or uv (recommended for isolated tool installs)
    uv tool install helia-profiler
    ```

=== "macOS"

    ```bash
    pip install helia-profiler
    # or
    uv tool install helia-profiler
    ```

=== "Windows"

    ```powershell
    pip install helia-profiler
    # or
    uv tool install helia-profiler
    ```

Extras:

```bash
pip install 'helia-profiler[aot]'        # heliaAOT compiler support
pip install 'helia-profiler[analysis]'   # model compute/parameter analysis, no hardware needed
```

Power-measurement support (`pyjoulescope_driver`) ships as a core
dependency — no extra install needed, just the udev rule below on Linux.

## 2. ARM GNU Toolchain (`arm-none-eabi-gcc`)

=== "Linux"

    ```bash
    sudo apt install gcc-arm-none-eabi
    ```

    For a newer compiler than your distro packages, download the
    [Arm GNU Toolchain](https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads)
    tarball and put its `bin/` directory on `PATH`:

    ```bash
    export PATH="$HOME/arm-gnu-toolchain-14.3.rel1/bin:$PATH"
    ```

=== "macOS"

    Download the macOS package from the
    [Arm GNU Toolchain Downloads](https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads)
    page and install it (default location
    `/Applications/ArmGNUToolchain/`), then add it to `PATH`:

    ```bash
    export PATH="/Applications/ArmGNUToolchain/14.3.rel1/arm-none-eabi/bin:$PATH"
    ```

=== "Windows"

    Download the Windows installer from the
    [Arm GNU Toolchain Downloads](https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads)
    page and run it — check **"Add path to environment variable"** during
    setup. Verify in a new terminal:

    ```powershell
    arm-none-eabi-gcc --version
    ```

## 3. CMake and Ninja

=== "Linux"

    ```bash
    sudo apt install cmake ninja-build
    ```

=== "macOS"

    ```bash
    brew install cmake ninja
    ```

=== "Windows"

    ```powershell
    winget install Kitware.CMake Ninja-build.Ninja
    ```

    Or install both via `pip install cmake ninja` if you prefer not to use
    winget.

## 4. SEGGER J-Link software

hpx drives J-Link through `JLinkExe` (flashing) and `pylink-square`
(RTT/SWO capture, installed automatically with heliaPROFILER).

=== "Linux"

    Download the `.deb`/`.tgz` installer from
    [segger.com/downloads/jlink](https://www.segger.com/downloads/jlink/)
    and install it. The SEGGER installer sets up the udev rules needed for
    non-root USB access to the J-Link probe; reboot or replug the probe
    afterward if `hpx probes list` doesn't see it.

=== "macOS"

    Download and run the `.pkg` installer from
    [segger.com/downloads/jlink](https://www.segger.com/downloads/jlink/).

=== "Windows"

    Download and run the `.exe` installer from
    [segger.com/downloads/jlink](https://www.segger.com/downloads/jlink/).
    Drivers are installed automatically.

heliaPROFILER bundles a pinned, tested copy of the permissively licensed SEGGER
RTT target sources. No separate RTT source checkout is required for normal use.
The SEGGER J-Link host software remains a separate installation.

For testing another RTT release, hpx resolves explicit overrides in this order:

1. `target.segger_rtt_path` in configuration or `Session.with_target()`
2. The `SEGGER_RTT_PATH` environment variable
3. The bundled RTT target sources

An override directory must contain both `RTT/SEGGER_RTT.c` and
`Config/SEGGER_RTT_Conf.h`:

```bash
git clone --branch V8.58.0 https://github.com/SEGGERMicro/RTT.git segger-rtt
```

Prefer explicit profile configuration over modifying `PATH`:

```yaml
target:
    transport: rtt
    segger_rtt_path: /path/to/SEGGER_RTT
```

## 5. Joulescope (optional, for power capture)

`pyjoulescope_driver` is a core dependency, so no extra `pip install` is
needed — just make the USB device accessible:

=== "Linux"

    Joulescope needs a udev rule granting your user access to its USB
    device before `hpx profile --power` will find it without root. Follow
    the udev setup instructions from the
    [Joulescope project](https://github.com/jetperch/joulescope), then
    replug the device.

=== "macOS"

    No extra driver setup — plug in the Joulescope and it should enumerate.

=== "Windows"

    Some Windows configurations need a WinUSB driver bound to the
    Joulescope's USB interface (for example via
    [Zadig](https://zadig.akeo.ie/)) before `pyjoulescope_driver` can open
    it. If `hpx profile --power` reports the device isn't found, check
    Device Manager for an unbound interface first.

See [Power Measurement](../guide/power.md) for wiring and sync-GPIO setup.

## Verify everything: `hpx doctor`

```bash
hpx doctor
```

Expected output (columns will vary by platform; a dash `–` means an
optional tool wasn't found):

```text
Toolchain Check
╭────┬────────────────────────────────────┬────────────────────────────────╮
│    │ Tool                               │ Path                           │
├────┼────────────────────────────────────┼────────────────────────────────┤
│ ✓  │ ARM GCC toolchain                  │ /usr/bin/arm-none-eabi-gcc     │
│ ✓  │ CMake (>= 3.24)                    │ /usr/bin/cmake                 │
│ ✓  │ Ninja build system                 │ /usr/bin/ninja                 │
│ ✓  │ SEGGER J-Link commander            │ /usr/bin/JLinkExe              │
│ ✓  │ neuralspotx Python package         │ installed                      │
│ ✓  │ pylink Python package (RTT/SWO     │ installed                      │
│    │ transport)                         │                                │
│ –  │ heliaAOT compiler                  │ not installed                  │
│ –  │ ARM Compiler (armclang)            │ not installed                  │
│ –  │ ARM fromelf (armclang)             │ not installed                  │
╰────┴────────────────────────────────────┴────────────────────────────────╯

All required tools found.
```

`✓` rows are required; `–` rows are optional (only needed for heliaAOT or
armclang). Once every required row shows `✓`, continue to
[First Profile](first-profile.md).
