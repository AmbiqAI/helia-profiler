# hpx doctor

Check that required toolchain tools and dependencies are installed.

## Usage

```bash
hpx doctor
```

## Checks

| Tool | Required | Description |
|---|---|---|
| `arm-none-eabi-gcc` | yes | ARM GCC toolchain |
| `cmake` | yes | CMake >= 3.24 |
| `ninja` | yes | Ninja build system |
| `JLinkExe` | yes | SEGGER J-Link commander |
| `JLinkSWOViewerCL` | yes | SEGGER SWO viewer |
| `nsx` | yes | neuralspotx CLI |
| `joulescope` | no | Joulescope Python package (for `--power`) |
