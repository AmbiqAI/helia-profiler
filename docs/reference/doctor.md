# `hpx doctor`

Verify that all required toolchain binaries and probes are available.

## Synopsis

```bash
hpx doctor
```

## Behavior

Probes for, and reports the version of, every external dependency the
profiler invokes:

- `arm-none-eabi-gcc` (and `arm-none-eabi-size`)
- `armclang`, `fromelf` (when present)
- `atfe` (when present)
- `cmake`
- `ninja`
- `JLinkExe`
- `neuralspotx` Python package
- `pylink` Python package (required for RTT/SWO transport, including the
  default RTT flow)
- `helia-aot` Python package (optional)
- `armclang`, `fromelf` (optional)

## Output

```
✓ ARM GCC toolchain: /usr/bin/arm-none-eabi-gcc
✓ CMake (>= 3.24): /usr/bin/cmake
✓ Ninja build system: /usr/bin/ninja
✓ SEGGER J-Link commander: /usr/bin/JLinkExe
✓ neuralspotx Python package: installed
✓ pylink Python package (RTT/SWO transport): installed
○ heliaAOT compiler: not installed
○ ARM Compiler (armclang): not installed
○ ARM fromelf (armclang): not installed
```

| Symbol | Meaning |
|---|---|
| `✓` | Present and functional |
| `✗` | **Required** binary missing — `hpx profile` will fail until installed |
| `○` | Optional binary missing — only matters if you opt into that feature |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | All required dependencies present |
| 1 | At least one required dependency is missing |

`doctor` flags **required** dependencies as failures. Missing optional
capabilities (for example `helia-aot` or Arm Compiler binaries) report
`○` and exit 0.

## See also

- [Installation](../getting-started/install.md) — how to install each
  tool.
- [Toolchains](../guide/toolchains.md) — extra binaries the optional
  toolchains require.
