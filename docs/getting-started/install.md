# Installation

heliaPROFILER (`hpx`) needs Python plus a small set of embedded-development
tools: an ARM cross-compiler, CMake/Ninja, and SEGGER J-Link software. Power
capture additionally needs a Joulescope JS110/JS220/JS320 and, on Linux, a udev
rule for non-root USB access.

## Quick install

=== "Nix"

    Install [Determinate Nix](https://determinate.systems/install/):

    ```bash
    curl -fsSL https://install.determinate.systems/nix | sh -s -- install
    ```

    Clone the repository:

    ```bash
    git clone https://github.com/AmbiqAI/helia-profiler.git && cd helia-profiler
    ```

    Enter the complete development environment:

    ```bash
    nix develop
    hpx doctor
    ```

    Nix downloads and verifies the pinned J-Link package directly from
    [SEGGER](https://www.segger.com/downloads/jlink/) under its terms; no
    separate download or preparation command is needed.

    On a Linux desktop (Ubuntu, Debian, Fedora, etc.), install USB access
    rules once, then reconnect the J-Link and any Joulescope:

    ```bash
    nix run .#install-udev-rules
    ```

    The flake supports x86-64 Linux, ARM64 Linux, and Apple Silicon macOS and
    includes Python, heliaAOT, LiteRT, NSX, CMake, Ninja, GNU Arm Embedded,
    ATfE, J-Link, and the development dependencies.

    You can skip the manual tool installation sections below. Continue with
    [Linux USB permissions](#linux-usb-permissions) for NixOS, SSH/CI hosts,
    and serial transports, or [verify your setup](#verify-everything-hpx-doctor).
    Code contributors can use `nix develop .#contrib` and then
    `pre-commit install` once to enable the repository checks.

    On macOS, no udev setup is needed. Open a new terminal and run
    `nix develop` again whenever you return to the repository.

    Nix does not run natively on Windows — Windows users should either run
    this method inside WSL2 (note that J-Link/USB access from WSL2 requires
    `usbipd-win` passthrough) or use the uv/pip methods below natively.

=== "uv"

    ```bash
    uv tool install helia-profiler
    ```

    Continue with the platform-specific hardware prerequisites below.

=== "pip"

    ```bash
    pip install helia-profiler
    ```

    Continue with the platform-specific hardware prerequisites below.

!!! warning "Alpha"
    heliaPROFILER is pre-1.0. Breaking changes may land on **minor**
    versions until v1.0 — pin the version you tested (for example,
    `pip install helia-profiler==0.1.1`) for anything long-lived.

## Requirements

| Dependency | Version | Purpose |
|---|---|---|
| Python | `>= 3.11` | Runtime (the `aot` extra currently needs 3.11–3.12) |
| `arm-none-eabi-gcc` | 13.x or 14.x | Default ARM cross-compiler |
| CMake | `>= 3.24` | Build system |
| Ninja | any | Build backend |
| SEGGER J-Link software | `>= 7.80` | Flash and RTT/SWO capture |
| `neuralspotx` (`nsx`) | `== 0.7.17` | Firmware build pipeline (installed automatically as a dependency) |

`armclang` and ATfE are optional alternative toolchains — see
[Toolchains](../guide/toolchains.md). A Joulescope JS110/JS220/JS320 is optional
and only needed for power capture — see [Power Measurement](../guide/power.md).
Git and initial network access to GitHub are also needed while NSX resolves and
clones firmware modules. After one successful build, `--offline` can reuse the
existing lock/module state for offline reruns.

On Windows, enable long-path support before the first build — some NSX module
checkouts (e.g. `ns-cmsis-nn` test data) exceed the 260-character `MAX_PATH`
limit. Both settings are required: git's, so checkouts can create the files
("Filename too long" otherwise), and the OS policy, so Python can traverse and
clean them (without it, `nsx sync` fails with "refusing to operate on
non-empty path"):

```powershell
git config --global core.longpaths true

# In an elevated (Administrator) PowerShell; new processes pick it up
# immediately, no reboot needed:
Set-ItemProperty -Path HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem `
    -Name LongPathsEnabled -Value 1
```

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

The AOT extra installs `helia-aot>=0.19.0` and a LiteRT-compatible analysis
stack. The analysis extra installs the same constrained LiteRT stack plus
flatbuffer inspection support. `helia-aot` currently declares
`requires-python >=3.11,<3.13`, so the `aot` extra needs Python 3.11 or 3.12
until that cap is lifted.

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

    If a toolchain is already installed but that command isn't found, it just
    isn't on `PATH` — no reinstall needed. Look for the `bin` directory of the
    existing installation (installer default
    `C:\Program Files (x86)\Arm GNU Toolchain arm-none-eabi\<version>\bin`;
    extracted archives are often under `C:\Program Files\Arm\`) and add it:

    ```powershell
    # current session only
    $env:PATH = "C:\path\to\arm-toolchain\bin;$env:PATH"

    # persistently, for future terminals
    [Environment]::SetEnvironmentVariable(
        "Path",
        "C:\path\to\arm-toolchain\bin;" + [Environment]::GetEnvironmentVariable("Path", "User"),
        "User")
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

hpx drives J-Link through SEGGER Commander (`JLinkExe` on Linux/macOS,
`JLink.exe` on Windows) for flashing and `pylink-square`
(RTT/SWO capture, installed automatically with heliaPROFILER).

Discovery checks `JLINK_PATH` first, then both executable names on `PATH`, then
common SEGGER install locations. Set `JLINK_PATH` to the full executable path
for non-standard installations.

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

    The installer puts the software in a versioned directory
    (`C:\Program Files\SEGGER\JLink_V<version>`). hpx finds it there
    automatically even when it isn't on `PATH`; for a custom location, add
    the directory to `PATH` or set `JLINK_PATH` to the full path of
    `JLink.exe`.

heliaPROFILER bundles a pinned, tested copy of the permissively licensed SEGGER
RTT target sources. No separate RTT source checkout is required for normal use.
The SEGGER J-Link host software is included in the Nix environment; uv/pip
users install it separately as described above.

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
    [Linux USB permissions](#linux-usb-permissions) below, then replug the device.

=== "macOS"

    No extra driver setup — plug in the Joulescope and it should enumerate.

=== "Windows"

    Some Windows configurations need a WinUSB driver bound to the
    Joulescope's USB interface (for example via
    [Zadig](https://zadig.akeo.ie/)) before `pyjoulescope_driver` can open
    it. If `hpx profile --power` reports the device isn't found, check
    Device Manager for an unbound interface first.

See [Power Measurement](../guide/power.md) for wiring and sync-GPIO setup.

## Linux USB permissions

Installing tools in a Nix shell does not grant access to attached hardware.
J-Link and Joulescope use raw USB devices; `uart` and `usb_cdc` also need
access to a serial device such as `/dev/ttyACM0` or `/dev/ttyUSB0`.
Run HPX as your normal user after configuring access.

### Desktop Linux

From the repository, preview and install the rules:

```bash
nix run .#install-udev-rules -- --dry-run
nix run .#install-udev-rules
```

Without Nix, run `bash nix/scripts/install-udev-rules.sh` from the checkout.
The installer uses `sudo` to write `70-segger-jlink.rules` and
`70-joulescope.rules` in `/etc/udev/rules.d/`, reload the rules, and refresh
USB devices. Unplug and reconnect the instruments afterward. The rules grant
the active local desktop session access through `uaccess`; they do not grant
every user access. Their `70-` prefix places them before
[systemd's access handler](https://github.com/systemd/systemd/blob/main/rules.d/73-seat-late.rules.in).

### NixOS

Declare the rules in your system configuration instead of using the installer:

```nix
services.udev.packages = [
  (pkgs.writeTextDir "lib/udev/rules.d/70-hpx.rules" ''
    SUBSYSTEM=="usb", ATTR{idVendor}=="1366", MODE="0660", TAG+="uaccess", ENV{ID_MM_DEVICE_IGNORE}="1"
    SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTRS{idVendor}=="16d0", ATTRS{idProduct}=="0e88|0e87|10ba|10b9|135a", MODE="0660", TAG+="uaccess"
  '')
];
```

Apply with `sudo nixos-rebuild switch`, then reconnect the devices.
Use `services.udev.packages` here: NixOS writes `services.udev.extraRules`
to `99-local.rules`, after the active-session access handler.

### SSH sessions and CI runners

An SSH login or background runner may have no active local desktop session.
For these hosts, give a dedicated group access to the instruments. For example,
on a non-NixOS Linux host:

```bash
sudo groupadd -f hpx
sudo usermod -aG hpx "$USER"
sudo tee /etc/udev/rules.d/70-hpx-group.rules >/dev/null <<'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="1366", GROUP="hpx", MODE="0660", ENV{ID_MM_DEVICE_IGNORE}="1"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTRS{idVendor}=="16d0", ATTRS{idProduct}=="0e88|0e87|10ba|10b9|135a", GROUP="hpx", MODE="0660"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb
```

For CI, add the account running the service to the group instead of `$USER`,
then restart that service. For an interactive account, log out and back in.
Reconnect the devices. On NixOS, declare `users.groups.hpx = {};`, add
`"hpx"` to the account's `extraGroups`, and use `GROUP="hpx"` in the rules
package above in place of `TAG+="uaccess"`.

### UART and USB CDC serial ports

Check the serial device and its owning group:

```bash
hpx ports list --all
ls -l /dev/ttyACM0  # replace with the port listed by HPX
```

If your account cannot access that port, add it to the device's group.
For example, Ubuntu/Debian commonly use `dialout`:

```bash
sudo usermod -aG dialout "$USER"
```

Use the group shown on your host, then log out and back in. On NixOS, add
that group to the account's `extraGroups` and rebuild the system configuration.
The raw-USB rules alone do not grant access to `/dev/tty*` devices.

## Verify everything: `hpx doctor`

```bash
hpx doctor
hpx probes list
hpx ports list --all
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

`✓` means the dependency was found; `–` rows are optional (only needed for
heliaAOT or an alternative toolchain). `hpx doctor` does not replace checking
that your installed compiler, CMake, and J-Link versions meet the table above.
Once every required row shows `✓`, continue to
[First Profile](first-profile.md).
