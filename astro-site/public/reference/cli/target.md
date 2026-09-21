# hpx target

Run explicit target-side utility operations

## hpx target

```bash
hpx target COMMAND [ARGS]...
```

Run explicit target-side utility operations

Defined in `src/helia_profiler/cli/inspect_app.py` line 233.

## hpx target reset

```bash
hpx target reset [OPTIONS]
```

Reset a target through HPX's non-interactive J-Link wrapper

Defined in `src/helia_profiler/cli/inspect_app.py` line 240.

### Options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--board` | `str` |  | Target board ID Required. |
| `--jlink-serial` | `Optional[str]` |  | J-Link probe serial number |
| `--kind` | `debug \| swpoi` | debug | Reset kind: debug r/g reset (default) or SWPOI reset |

Generated from the `src/helia_profiler` tree `d8fc7a994d8f2b900482cdc6119d92751a2b7315` with typer 0.26.8 and click 8.3.3.
