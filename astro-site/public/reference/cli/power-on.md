# hpx power-on

Enable Joulescope current passthrough (keeps board powered)

## hpx power-on

```bash
hpx power-on [OPTIONS]
```

Enable Joulescope current passthrough (keeps board powered)

Defined in `src/helia_profiler/cli/validation_app.py` line 61.

### Options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--driver` | `joulescope` | joulescope | Joulescope driver (default: auto-detect) |
| `--power-serial` | `Optional[str]` |  | Joulescope serial number to select when multiple are connected |

### Examples

```text
Opens the Joulescope and enables current passthrough so the

target board stays powered.  Holds the connection open until

Ctrl-C.  Useful when the Joulescope app is not running and the

board would otherwise be unpowered.
```

Generated from the `src/helia_profiler` tree `d8fc7a994d8f2b900482cdc6119d92751a2b7315` with typer 0.26.8 and click 8.3.3.
