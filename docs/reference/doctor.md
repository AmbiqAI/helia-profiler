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
- `JLinkExe` (and connected J-Link probes)
- `JLinkSWOViewerCL` (used for SWO transport diagnostics)
- `nsx`
- `joulescope` Python package (optional — only when power extras
  installed)

## Output

```
✓ arm-none-eabi-gcc   14.3.1
✓ arm-none-eabi-size  14.3.1
✓ cmake               3.31.6
✓ ninja               1.12.1
✓ JLinkExe            V8.12a
✓ J-Link probe        000123456789  Apollo510 EVB
✓ nsx                 0.6.0
○ armclang            (not installed)
○ atfe                (not installed)
○ joulescope          (optional — pip install 'helia-profiler[power]')
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

`doctor` only flags **required** dependencies as failures. Missing
optional binaries (armclang, atfe, joulescope) report `○` and exit 0.

## See also

- [Installation](../getting-started/install.md) — how to install each
  tool.
- [Toolchains](../guide/toolchains.md) — extra binaries the optional
  toolchains require.
