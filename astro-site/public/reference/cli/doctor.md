# hpx doctor

Check toolchain and dependencies

## hpx doctor

```bash
hpx doctor [OPTIONS]
```

Check toolchain and dependencies

Defined in `src/helia_profiler/cli/inspect_app.py` line 49.

### Options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--json` | `bool` | false | Emit machine-readable JSON |
| `--bundle` | `Optional[Path]` |  | Write a sanitized support-bundle archive to this file or directory instead of printing the toolchain table |
| `--workspace` | `Optional[Path]` |  | Prepared profiler_app directory (or its nsx.lock/hpx-dependencies.json, or the parent fingerprint workspace) to include exact dependency lock provenance in the bundle |
| `--config` | `Optional[Path]` |  | Resolve this YAML config and include a sanitized snapshot in the bundle |
| `--toolchain` | `Optional[str]` |  | Toolchain to check (default: arm-none-eabi-gcc) |
| `--transport` | `rtt \| usb_cdc \| swo \| uart` |  | Transport to check (default: rtt) |
| `--engine` | `tflm \| helia-rt \| helia-aot \| executorch` |  | Engine to check (default: helia-rt) |
| `--no-probes` | `bool` | false | Skip live J-Link probe enumeration in --bundle |
| `--no-ports` | `bool` | false | Skip live serial port enumeration in --bundle |
| `--raw-probe-ids` | `bool` | false | Include unredacted device serial numbers in --bundle (opt-in; prints a warning) |

Generated from the `src/helia_profiler` tree `17bfdf5c68d6c73c3d22f7afb063ac9cd722152f` with typer 0.26.8 and click 8.3.3.
