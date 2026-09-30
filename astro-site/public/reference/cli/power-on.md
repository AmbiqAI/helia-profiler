# hpx power-on

Enable Joulescope current passthrough (keeps board powered)

## hpx power-on

```bash
hpx power-on [OPTIONS]
```

Enable Joulescope current passthrough (keeps board powered)

Defined in `src/helia_profiler/cli/validation_app.py` line 57.

### Options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--driver` | `joulescope` | joulescope | Power driver that holds the target rail on (JS110/JS220/JS320 auto-detected) |
| `--power-serial` | `Optional[str]` |  | Joulescope serial number to select when multiple are connected |

### Examples

```text
Opens the Joulescope and enables current passthrough so the

target board stays powered.  Holds the connection open until

Ctrl-C.  Useful when the Joulescope app is not running and the

board would otherwise be unpowered.
```

Generated from the `src/helia_profiler` tree `b67e332cd4cc51ae9149e14c32e910517775062c` with typer 0.26.8 and click 8.3.3.
