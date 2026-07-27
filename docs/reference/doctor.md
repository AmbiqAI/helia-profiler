# `hpx doctor`

Check whether required host binaries and Python packages can be found.

## Synopsis

```bash
hpx doctor
```

## Behavior

Reports the current status of the host tools and Python packages that
`hpx` checks before or alongside profiling:

- `arm-none-eabi-gcc`
- `cmake`
- `ninja`
- `JLinkExe`
- `neuralspotx` Python package
- `pylink` Python package (required for RTT/SWO transport, including the
  default RTT flow)
- `helia-aot` Python package (optional)
- `armclang` (optional)
- `fromelf` (optional)
- ATfE's `clang`, `clang++`, `llvm-ar`, `llvm-objcopy`, `llvm-size`, and
  `llvm-nm` when `ATFE_ROOT` is set

## Output

```
✓ ARM GCC toolchain: /usr/bin/arm-none-eabi-gcc
✓ CMake (>= 3.24): /usr/bin/cmake
✓ Ninja build system: /usr/bin/ninja
✓ SEGGER J-Link commander: /usr/bin/JLinkExe
✓ neuralspotx Python package: installed
✓ pylink Python package (RTT/SWO transport): installed
– heliaAOT compiler: not installed
– ARM Compiler (armclang): not installed
– ARM fromelf (armclang): not installed
```

| Symbol | Meaning |
|---|---|
| `✓` | Found at the reported path |
| `✗` | **Required** binary missing — `hpx profile` will fail until installed |
| `–` | Optional dependency missing — only matters if you opt into that feature |

## Exit code

| Code | Meaning |
|---|---|
| 0 | Prints the status table. Missing required tools are shown as `✗`, but `hpx doctor` does not currently fail its exit status. |

`doctor` flags **required** dependencies as failures in the table. Missing
optional capabilities (for example `helia-aot` or Arm Compiler binaries)
report `–`. It does not compile a program or fully validate installed tool
versions.

## See also

- [Installation](../getting-started/install.md) — how to install each
  tool.
- [Toolchains](../guide/toolchains.md) — extra binaries the optional
  toolchains require.
